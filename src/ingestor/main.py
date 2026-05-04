"""Main module for the Cloud Function ingestor."""

import os
import logging
from cloudevents.http import CloudEvent
import functions_framework
from google.cloud import secretmanager
import vertexai
from vertexai.preview import rag

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
location = os.environ.get("GOOGLE_CLOUD_LOCATION", "")
secret_id = os.environ.get("GCP_SECRET_ID", "")


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

    gcs_source = [f"gs://{bucket}/{name}"]

    # Define parsing and chunking configuration
    transformation_config = rag.TransformationConfig(
        chunking_config=rag.ChunkingConfig(
            chunk_size=512,
            chunk_overlap=50,
        )
    )

    logger.info(f"Importing {gcs_source} into RAG Corpus {corpus_id}...")

    try:
        gemini_model_name = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-flash")
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
