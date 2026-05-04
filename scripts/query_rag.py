"""Script to query the Gemini RAG Pipeline."""

import os
import logging
import argparse
import vertexai
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel, Tool

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main():
    """Execute the query against the RAG engine."""
    parser = argparse.ArgumentParser(
        description="Query the Gemini RAG Automator Pipeline"
    )
    parser.add_argument("query", type=str, help="The question to ask the RAG engine")
    args = parser.parse_args()

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION")
    corpus_id = os.environ.get(
        "GCP_SECRET_ID"
    )  # Fallback, ideally fetched from secret manager
    model_name = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-flash")

    if not all([project_id, location, corpus_id]):
        logger.error(
            "Missing required environment variables (GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, GCP_SECRET_ID)."
        )
        return

    # Initialize Vertex AI
    vertexai.init(project=project_id, location=location)

    # Note: In a real scenario, you'd fetch the actual Corpus ID (projects/.../locations/.../ragCorpora/...)
    # from Secret Manager. For testing if you have the direct ID, you could pass it directly.
    # To keep this script simple and functional right away, let's assume we retrieve it like in the Cloud Function.

    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{corpus_id}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
        true_corpus_id = response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        logger.error(f"Error fetching Corpus ID from Secret Manager: {e}")
        return

    logger.info(f"\n[+] Using RAG Corpus: {true_corpus_id}")
    logger.info(f"[+] Querying {model_name} with question: '{args.query}'\n")

    # Define the RAG Tool pointing to our corpus
    rag_retrieval_tool = Tool.from_retrieval(
        retrieval=rag.Retrieval(
            source=rag.VertexRagStore(
                rag_resources=[rag.RagResource(rag_corpus=true_corpus_id)],
                similarity_top_k=5,
            ),
        )
    )

    # Initialize the Gemini model with the RAG tool
    model = GenerativeModel(
        model_name=model_name,
        tools=[rag_retrieval_tool],
    )

    # Generate the grounded response
    response = model.generate_content(args.query)

    logger.info("ANSWER:")
    logger.info(response.text)


if __name__ == "__main__":
    main()
