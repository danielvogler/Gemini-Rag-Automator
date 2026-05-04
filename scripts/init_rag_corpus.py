"""Script to initialize a Vertex AI RAG Corpus and store its ID in Secret Manager."""

import os
import logging
import vertexai
from vertexai.preview import rag
from google.cloud import secretmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    """Create a Vertex AI RAG Corpus and store its ID in Secret Manager."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION")
    secret_id = os.environ.get("GCP_SECRET_ID")
    display_name = os.environ.get("RAG_CORPUS_DISPLAY_NAME")
    description = os.environ.get("RAG_CORPUS_DESCRIPTION")

    if not all([project_id, location, secret_id, display_name]):
        logger.error("Missing required environment variables.")
        return

    logger.info(f"Initializing Vertex AI in {project_id} ({location})")
    vertexai.init(project=project_id, location=location)

    logger.info(f"Creating RAG Corpus '{display_name}'...")
    try:
        corpus = rag.create_corpus(display_name=display_name, description=description)
        corpus_id = corpus.name
        logger.info(f"Successfully created RAG Corpus: {corpus_id}")
    except Exception as e:
        logger.error(f"Failed to create RAG Corpus: {e}")
        return

    logger.info(f"Saving corpus ID to Secret Manager secret: {secret_id}")
    client = secretmanager.SecretManagerServiceClient()

    parent = f"projects/{project_id}"
    secret_name = f"{parent}/secrets/{secret_id}"

    try:
        version = client.add_secret_version(
            request={
                "parent": secret_name,
                "payload": {"data": corpus_id.encode("UTF-8")},
            }
        )
        logger.info(f"Successfully added secret version: {version.name}")
    except Exception as e:
        logger.error(f"Failed to add secret version: {e}")


if __name__ == "__main__":
    main()
