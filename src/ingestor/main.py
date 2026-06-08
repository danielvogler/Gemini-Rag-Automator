"""Main module for the Cloud Function ingestor."""

import hashlib
import io
import json
import os
import logging
from cloudevents.http import CloudEvent
import functions_framework
import google.cloud.storage as storage
from google.cloud import firestore, secretmanager
from pypdf import PdfReader
import vertexai
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
location = os.environ.get("GOOGLE_CLOUD_LOCATION", "")
secret_id = os.environ.get("GCP_SECRET_ID", "")

# Scientific papers carry title/authors/journal on their first page. We
# extract that text once at ingestion time, ask Gemini to structure it, and
# store the result in Firestore keyed by a hash of the GCS URI (Firestore
# document IDs can't contain '/'). The agent looks this up at query time to
# enrich citations beyond the raw filename — see src/agent/tools.py.
PAPER_METADATA_COLLECTION = "paper_metadata"
_FIRST_PAGE_MAX_CHARS = 6000

_METADATA_PROMPT = """\
You are extracting bibliographic metadata from the first page of a scientific \
paper. Read the text below and respond with ONLY a JSON object (no markdown \
fences, no commentary) with exactly these keys:

  "title":   the paper's title as a string, or null if not present
  "authors": an array of author name strings, or [] if not present
  "journal": the journal or publication venue name as a string, or null if not present

Text:
\"\"\"
{text}
\"\"\"
"""


def gcs_uri_to_doc_id(gcs_uri: str) -> str:
    """Derive a stable Firestore document ID from a GCS URI (which contains '/')."""
    return hashlib.sha256(gcs_uri.encode("utf-8")).hexdigest()


def extract_first_page_text(pdf_bytes: bytes) -> str:
    """Return the text of a PDF's first page, truncated for prompting."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if not reader.pages:
        return ""
    text = reader.pages[0].extract_text() or ""
    return text[:_FIRST_PAGE_MAX_CHARS]


def extract_paper_metadata(first_page_text: str, model_name: str) -> dict | None:
    """Ask Gemini to structure title/authors/journal from first-page text.

    Returns None if the text is empty or the model response can't be parsed.
    """
    if not first_page_text.strip():
        return None

    model = GenerativeModel(model_name)
    response = model.generate_content(_METADATA_PROMPT.format(text=first_page_text))
    raw = (response.text or "").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning(f"Could not parse paper metadata response: {raw!r}")
        return None

    if not isinstance(data, dict):
        return None

    return {
        "title": data.get("title"),
        "authors": data.get("authors") or [],
        "journal": data.get("journal"),
    }


def store_paper_metadata(gcs_uri: str, metadata: dict) -> None:
    """Persist extracted metadata in Firestore, keyed by a hash of the GCS URI."""
    client = firestore.Client()
    doc_id = gcs_uri_to_doc_id(gcs_uri)
    client.collection(PAPER_METADATA_COLLECTION).document(doc_id).set(
        {"gcs_uri": gcs_uri, **metadata}
    )


def ingest_paper_metadata(
    bucket: str, name: str, gcs_uri: str, model_name: str
) -> None:
    """Best-effort: extract and store title/authors/journal for a paper.

    Citation enrichment is a nice-to-have layered on top of the core RAG
    import, so failures here are logged and swallowed rather than failing
    (and retriggering retries of) the whole ingestion event.
    """
    try:
        pdf_bytes = storage.Client().bucket(bucket).blob(name).download_as_bytes()
        first_page_text = extract_first_page_text(pdf_bytes)
        metadata = extract_paper_metadata(first_page_text, model_name)
        if metadata is None:
            logger.info(f"No paper metadata extracted for {gcs_uri}")
            return
        store_paper_metadata(gcs_uri, metadata)
        logger.info(f"Stored paper metadata for {gcs_uri}: {metadata}")
    except Exception as e:
        logger.warning(f"Failed to extract/store paper metadata for {gcs_uri}: {e}")


def get_corpus_id() -> str:
    """Retrieve the RAG Corpus ID from Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8").strip()


@functions_framework.cloud_event
def process_file(cloud_event: CloudEvent) -> None:
    """Event-driven function to ingest new PDFs into Vertex AI RAG."""
    data = cloud_event.data

    bucket = data.get("bucket")
    name = data.get("name")

    if not name or not bucket:
        logger.error("Invalid event data. Missing bucket or name.")
        return

    if not name.lower().endswith(".pdf"):
        logger.info(f"Ignoring non-PDF file: {name}")
        return

    logger.info(f"Processing file: gs://{bucket}/{name}")

    try:
        corpus_id = get_corpus_id()
    except Exception as e:
        logger.error(f"Failed to get corpus ID: {e}")
        raise e

    logger.info(f"Using corpus ID: {corpus_id}")

    # Initialize in the environment's configured location
    vertexai.init(project=project_id, location=location)

    gcs_uri = f"gs://{bucket}/{name}"
    gcs_source = [gcs_uri]
    gemini_model_name = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-flash")

    ingest_paper_metadata(bucket, name, gcs_uri, gemini_model_name)

    # Define parsing and chunking configuration
    transformation_config = rag.TransformationConfig(
        chunking_config=rag.ChunkingConfig(
            chunk_size=512,
            chunk_overlap=50,
        )
    )

    logger.info(f"Importing {gcs_source} into RAG Corpus {corpus_id}...")

    try:
        response = rag.import_files(
            corpus_name=corpus_id,
            paths=gcs_source,
            transformation_config=transformation_config,
            llm_parser=rag.LlmParserConfig(model_name=gemini_model_name),
            timeout=500,
        )
        logger.info(
            f"Import completed. Imported files count: {response.imported_rag_files_count}"
        )
    except Exception as e:
        logger.error(f"Failed to import file to RAG: {e}")
        # Explicitly raise so Eventarc/PubSub knows to retry this event
        raise e
