"""Script to list files ingested into the Vertex AI RAG Corpus."""

import os
import logging
import vertexai
from vertexai.preview import rag
from google.cloud import secretmanager

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main():
    """List all files from the active RAG corpus."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION")
    secret_id = os.environ.get("GCP_SECRET_ID")

    if not all([project_id, location, secret_id]):
        logger.error(
            "Missing required environment variables (GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, GCP_SECRET_ID)."
        )
        return

    vertexai.init(project=project_id, location=location)

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
        true_corpus_id = response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        logger.error(f"Error fetching Corpus ID from Secret Manager: {e}")
        return

    logger.info(f"[+] Listing files in RAG Corpus: {true_corpus_id}\n")

    try:
        files = rag.list_files(corpus_name=true_corpus_id)
        count = 0
        for file in files:
            count += 1
            logger.info(f"  - {file.display_name} (ID: {file.name})")
            logger.info(
                f"    Source: {file.gcs_source.uris if file.gcs_source else 'N/A'}"
            )

            # Use getattr with a default of 'N/A' or check if it exists safely
            try:
                state = file.rag_file_state.state.name
            except Exception:
                state = "ACTIVE / IMPORTED"

            logger.info(f"    Status: {state}\n")

        logger.info(f"Total files found: {count}")
    except Exception as e:
        logger.error(f"Failed to list files: {e}")


if __name__ == "__main__":
    main()
