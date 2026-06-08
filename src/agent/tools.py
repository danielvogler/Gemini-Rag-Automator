"""Function tool that queries the configured Vertex AI RAG corpus."""

import hashlib
import logging
import os
from typing import cast

from google.cloud import firestore
from google.cloud.firestore import DocumentSnapshot
from vertexai.preview import rag

logger = logging.getLogger(__name__)

# Must match PAPER_METADATA_COLLECTION / gcs_uri_to_doc_id in src/ingestor/main.py —
# that's where title/authors/journal get extracted and stored at ingestion time.
_PAPER_METADATA_COLLECTION = "paper_metadata"


def _gcs_uri_to_doc_id(gcs_uri: str) -> str:
    return hashlib.sha256(gcs_uri.encode("utf-8")).hexdigest()


def _lookup_paper_metadata(gcs_uri: str) -> dict | None:
    """Look up extracted title/authors/journal for a source document, if any."""
    if not gcs_uri:
        return None
    try:
        client = firestore.Client()
        # firestore.Client is synchronous; .get() always returns a DocumentSnapshot
        # here (the Awaitable branch in its type signature applies to AsyncClient).
        doc = cast(
            DocumentSnapshot,
            client.collection(_PAPER_METADATA_COLLECTION)
            .document(_gcs_uri_to_doc_id(gcs_uri))
            .get(),
        )
    except Exception as e:
        logger.warning(f"Paper metadata lookup failed for {gcs_uri}: {e}")
        return None

    if not doc.exists:
        return None

    data = doc.to_dict() or {}
    return {
        "title": data.get("title"),
        "authors": data.get("authors") or [],
        "journal": data.get("journal"),
    }


def retrieve_rag_documentation(query: str) -> dict:
    """Search the configured Vertex AI RAG corpus for chunks relevant to the query.

    ALWAYS call this exactly once per user turn before writing an answer. Pass
    the user's literal question (or a minimally rephrased query).

    Args:
        query: The user's question, used verbatim as the retrieval query.

    Returns:
        A dict with one of two shapes:
          - {"chunks": [...]}        when the corpus has matching content
          - {"chunks": [], "empty": True}  when nothing matches
        Each chunk has: index (int, 1-based), text (str), source_uri (str),
        source_display_name (str), score (float), and — when available from
        the paper-metadata lookup — title (str), authors (list[str]),
        journal (str).
    """
    rag_corpus = os.environ.get("RAG_CORPUS")
    if not rag_corpus:
        raise RuntimeError(
            "RAG_CORPUS env var must be set to a full corpus resource path"
        )

    response = rag.retrieval_query(
        text=query,
        rag_resources=[rag.RagResource(rag_corpus=rag_corpus)],
        similarity_top_k=int(os.environ.get("AGENT_TOP_K", "10")),
        vector_distance_threshold=float(
            os.environ.get("AGENT_DISTANCE_THRESHOLD", "0.6")
        ),
    )

    contexts = list(response.contexts.contexts) if response.contexts else []
    if not contexts:
        return {"chunks": [], "empty": True}

    paper_metadata_cache: dict[str, dict | None] = {}
    chunks = []
    for i, c in enumerate(contexts):
        if c.source_uri not in paper_metadata_cache:
            paper_metadata_cache[c.source_uri] = _lookup_paper_metadata(c.source_uri)
        paper = paper_metadata_cache[c.source_uri]

        chunk = {
            "index": i + 1,
            "text": c.text,
            "source_uri": c.source_uri,
            "source_display_name": c.source_display_name,
            "score": c.score,
        }
        if paper:
            chunk["title"] = paper["title"]
            chunk["authors"] = paper["authors"]
            chunk["journal"] = paper["journal"]
        chunks.append(chunk)

    return {"chunks": chunks}
