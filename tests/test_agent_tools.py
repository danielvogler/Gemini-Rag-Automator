from unittest.mock import MagicMock

from agent.tools import (
    _gcs_uri_to_doc_id,
    _lookup_paper_metadata,
    retrieve_rag_documentation,
)


def _make_context(text, source_uri, source_display_name, score):
    context = MagicMock()
    context.text = text
    context.source_uri = source_uri
    context.source_display_name = source_display_name
    context.score = score
    return context


def test_lookup_paper_metadata_returns_fields_when_doc_exists(mocker):
    mock_firestore_client = mocker.patch("agent.tools.firestore.Client")
    mock_doc = mock_firestore_client.return_value.collection.return_value.document.return_value.get.return_value
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "gcs_uri": "gs://bucket/paper.pdf",
        "title": "Geothermal Energy Potential",
        "authors": ["A. One", "B. Two"],
        "journal": "Journal of Renewable Energy",
    }

    metadata = _lookup_paper_metadata("gs://bucket/paper.pdf")

    assert metadata == {
        "title": "Geothermal Energy Potential",
        "authors": ["A. One", "B. Two"],
        "journal": "Journal of Renewable Energy",
    }
    mock_firestore_client.return_value.collection.assert_called_once_with(
        "paper_metadata"
    )
    mock_firestore_client.return_value.collection.return_value.document.assert_called_once_with(
        _gcs_uri_to_doc_id("gs://bucket/paper.pdf")
    )


def test_lookup_paper_metadata_returns_none_when_doc_missing(mocker):
    mock_firestore_client = mocker.patch("agent.tools.firestore.Client")
    mock_doc = mock_firestore_client.return_value.collection.return_value.document.return_value.get.return_value
    mock_doc.exists = False

    assert _lookup_paper_metadata("gs://bucket/paper.pdf") is None


def test_lookup_paper_metadata_returns_none_on_error(mocker):
    mocker.patch("agent.tools.firestore.Client", side_effect=Exception("unavailable"))

    assert _lookup_paper_metadata("gs://bucket/paper.pdf") is None


def test_lookup_paper_metadata_returns_none_for_empty_uri():
    assert _lookup_paper_metadata("") is None


def test_retrieve_rag_documentation_merges_paper_metadata_into_chunks(mocker):
    mocker.patch.dict(
        "os.environ",
        {"RAG_CORPUS": "projects/p/locations/l/ragCorpora/123"},
        clear=False,
    )

    contexts = [
        _make_context("chunk one text", "gs://bucket/paper.pdf", "paper.pdf", 0.9),
        _make_context("chunk two text", "gs://bucket/paper.pdf", "paper.pdf", 0.8),
        _make_context("chunk three text", "gs://bucket/other.pdf", "other.pdf", 0.7),
    ]
    mock_response = MagicMock()
    mock_response.contexts.contexts = contexts
    mocker.patch("agent.tools.rag.retrieval_query", return_value=mock_response)
    mocker.patch("agent.tools.rag.RagResource")

    paper_metadata = {
        "title": "Geothermal Energy Potential",
        "authors": ["A. One"],
        "journal": "Journal of Renewable Energy",
    }
    mock_lookup = mocker.patch(
        "agent.tools._lookup_paper_metadata",
        side_effect=lambda uri: (
            paper_metadata if uri == "gs://bucket/paper.pdf" else None
        ),
    )

    result = retrieve_rag_documentation("what is geothermal energy")

    chunks = result["chunks"]
    assert len(chunks) == 3
    assert chunks[0]["title"] == "Geothermal Energy Potential"
    assert chunks[0]["authors"] == ["A. One"]
    assert chunks[0]["journal"] == "Journal of Renewable Energy"
    assert chunks[1]["title"] == "Geothermal Energy Potential"
    assert "title" not in chunks[2]
    assert "authors" not in chunks[2]
    assert "journal" not in chunks[2]

    # Same source_uri looked up only once despite two chunks sharing it.
    assert mock_lookup.call_count == 2
    mock_lookup.assert_any_call("gs://bucket/paper.pdf")
    mock_lookup.assert_any_call("gs://bucket/other.pdf")


def test_retrieve_rag_documentation_returns_empty_when_no_contexts(mocker):
    mocker.patch.dict(
        "os.environ",
        {"RAG_CORPUS": "projects/p/locations/l/ragCorpora/123"},
        clear=False,
    )

    mock_response = MagicMock()
    mock_response.contexts = None
    mocker.patch("agent.tools.rag.retrieval_query", return_value=mock_response)
    mocker.patch("agent.tools.rag.RagResource")
    mock_lookup = mocker.patch("agent.tools._lookup_paper_metadata")

    result = retrieve_rag_documentation("what is geothermal energy")

    assert result == {"chunks": [], "empty": True}
    mock_lookup.assert_not_called()
