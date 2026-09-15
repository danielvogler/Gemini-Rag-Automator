"""Local-dev invoker for the production ingestion handler.

Pipes a filename from stdin into a synthesised CloudEvent and calls the same
``process_file`` that the deployed Cloud Function calls in production. Useful
for re-triggering ingestion of a file already in GCS without redeploying or
re-uploading.
"""

import sys
import os
import logging

from cloudevents.http import CloudEvent
from ingestor.main import process_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "run" and sys.argv[2] == "ingest_test":
        input_file = sys.stdin.read().strip()
        logger.info(
            f"local_ingestor: testing ingestion for file from stdin: {input_file}"
        )

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
        print("Usage: gra-ingest run ingest_test (reads filename from stdin)")


if __name__ == "__main__":
    main()
