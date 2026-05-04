"""Entrypoint for the Agentic Development Kit (ADK) local CLI tool."""

import sys
import os
import logging
from cloudevents.http import CloudEvent
from src.ingestor.main import process_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Execute the ADK CLI tool commands."""
    if len(sys.argv) >= 3 and sys.argv[1] == "run" and sys.argv[2] == "ingest_test":
        input_file = sys.stdin.read().strip()
        logger.info(f"ADK CLI: testing ingestion for file from stdin: {input_file}")

        # Pull required configs from environment (scoped to project and location defined in .env)
        bucket = os.environ.get("GCS_BUCKET_NAME", "mock-bucket")

        attributes = {
            "type": "google.cloud.storage.object.v1.finalized",
            "source": "//storage.googleapis.com",
        }
        data = {
            "bucket": bucket,
            "name": input_file,
        }

        event = CloudEvent(attributes, data)
        try:
            process_file(event)
        except Exception as e:
            logger.error(f"Error processing file in ingest_test: {e}")
    else:
        print("Usage: adk run ingest_test (reads filename from stdin)")


if __name__ == "__main__":
    main()
