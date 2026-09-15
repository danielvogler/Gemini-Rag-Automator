"""Function tool that queries the configured Vertex AI RAG corpus."""

import os

from vertexai.preview import rag


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
        source_display_name (str), score (float).
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

    return {
        "chunks": [
            {
                "index": i + 1,
                "text": c.text,
                "source_uri": c.source_uri,
                "source_display_name": c.source_display_name,
                "score": c.score,
            }
            for i, c in enumerate(contexts)
        ]
    }
