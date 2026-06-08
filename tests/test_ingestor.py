from unittest.mock import MagicMock

from cloudevents.http import CloudEvent
from ingestor.main import (
    extract_first_page_text,
    extract_paper_metadata,
    gcs_uri_to_doc_id,
    get_corpus_id,
    ingest_paper_metadata,
    process_file,
    store_paper_metadata,
)


def test_get_corpus_id(mocker):
    # Mock the SecretManagerServiceClient
    mock_client_class = mocker.patch(
        "ingestor.main.secretmanager.SecretManagerServiceClient"
    )
    mock_client_instance = mock_client_class.return_value

    # Setup the mock response
    mock_response = MagicMock()
    mock_response.payload.data.decode.return_value = "mock_corpus_id  \n"
    mock_client_instance.access_secret_version.return_value = mock_response

    # Mock environment variables
    mocker.patch("ingestor.main.project_id", "test-project")
    mocker.patch("ingestor.main.secret_id", "test-secret")

    corpus_id = get_corpus_id()

    assert corpus_id == "mock_corpus_id"
    mock_client_instance.access_secret_version.assert_called_once_with(
        request={"name": "projects/test-project/secrets/test-secret/versions/latest"}
    )


def test_process_file_ignores_non_pdf(mocker):
    mock_get_corpus = mocker.patch("ingestor.main.get_corpus_id")
    mock_import = mocker.patch("ingestor.main.rag.import_files")

    attributes = {"type": "google.cloud.storage.object.v1.finalized", "source": "test"}
    data = {"bucket": "test-bucket", "name": "test_document.txt"}
    event = CloudEvent(attributes, data)

    process_file(event)

    mock_get_corpus.assert_not_called()
    mock_import.assert_not_called()


def test_process_file_missing_data(mocker):
    mock_get_corpus = mocker.patch("ingestor.main.get_corpus_id")
    mock_import = mocker.patch("ingestor.main.rag.import_files")

    attributes = {"type": "google.cloud.storage.object.v1.finalized", "source": "test"}
    data = {"bucket": "test-bucket"}  # Missing name
    event = CloudEvent(attributes, data)

    process_file(event)

    mock_get_corpus.assert_not_called()
    mock_import.assert_not_called()


def test_process_file_success(mocker):
    mock_get_corpus = mocker.patch("ingestor.main.get_corpus_id")
    mock_get_corpus.return_value = "mock_corpus_123"

    mock_vertexai_init = mocker.patch("ingestor.main.vertexai.init")
    mock_ingest_paper_metadata = mocker.patch("ingestor.main.ingest_paper_metadata")
    mock_import_files = mocker.patch("ingestor.main.rag.import_files")
    mock_response = MagicMock()
    mock_response.imported_rag_files_count = 1
    mock_import_files.return_value = mock_response

    mock_llm_parser = mocker.patch("ingestor.main.rag.LlmParserConfig", create=True)
    mock_chunking_config = mocker.patch("ingestor.main.rag.ChunkingConfig", create=True)
    mock_transformation_config = mocker.patch(
        "ingestor.main.rag.TransformationConfig", create=True
    )

    # Setup event
    attributes = {"type": "google.cloud.storage.object.v1.finalized", "source": "test"}
    data = {"bucket": "test-bucket", "name": "test_document.pdf"}
    event = CloudEvent(attributes, data)

    mocker.patch("ingestor.main.project_id", "test-project")
    mocker.patch("ingestor.main.location", "us-central1")

    process_file(event)

    mock_get_corpus.assert_called_once()
    mock_vertexai_init.assert_called_once_with(
        project="test-project", location="us-central1"
    )

    # Paper metadata extraction is attempted (best-effort) before the RAG import
    mock_ingest_paper_metadata.assert_called_once_with(
        "test-bucket",
        "test_document.pdf",
        "gs://test-bucket/test_document.pdf",
        "gemini-2.5-flash",
    )

    # Check rag.import_files is called with correct arguments
    mock_import_files.assert_called_once()

    call_args = mock_import_files.call_args.kwargs
    assert call_args["corpus_name"] == "mock_corpus_123"
    assert call_args["paths"] == ["gs://test-bucket/test_document.pdf"]
    assert call_args["transformation_config"] == mock_transformation_config.return_value
    assert call_args["llm_parser"] == mock_llm_parser.return_value
    assert call_args["timeout"] == 500

    mock_chunking_config.assert_called_once_with(chunk_size=512, chunk_overlap=50)
    mock_transformation_config.assert_called_once_with(
        chunking_config=mock_chunking_config.return_value
    )
    mock_llm_parser.assert_called_once_with(model_name="gemini-2.5-flash")


def test_process_file_get_corpus_id_fails(mocker):
    mock_get_corpus = mocker.patch("ingestor.main.get_corpus_id")
    mock_get_corpus.side_effect = Exception("Secret Manager Error")
    mock_import = mocker.patch("ingestor.main.rag.import_files")

    attributes = {"type": "google.cloud.storage.object.v1.finalized", "source": "test"}
    data = {"bucket": "test-bucket", "name": "test_document.pdf"}
    event = CloudEvent(attributes, data)

    # Process file should now raise the error
    import pytest

    with pytest.raises(Exception, match="Secret Manager Error"):
        process_file(event)

    mock_get_corpus.assert_called_once()
    mock_import.assert_not_called()


def test_process_file_import_fails(mocker):
    mock_get_corpus = mocker.patch("ingestor.main.get_corpus_id")
    mock_get_corpus.return_value = "mock_corpus_123"

    mocker.patch("ingestor.main.vertexai.init")

    mock_import_files = mocker.patch("ingestor.main.rag.import_files")
    mock_import_files.side_effect = Exception("Import Error")

    attributes = {"type": "google.cloud.storage.object.v1.finalized", "source": "test"}
    data = {"bucket": "test-bucket", "name": "test_document.pdf"}
    event = CloudEvent(attributes, data)

    mocker.patch("ingestor.main.project_id", "test-project")
    mocker.patch("ingestor.main.location", "us-central1")

    import pytest

    with pytest.raises(Exception, match="Import Error"):
        process_file(event)

    mock_import_files.assert_called_once()


def test_gcs_uri_to_doc_id_is_stable_and_path_safe():
    uri = "gs://bucket/path/to/paper.pdf"

    doc_id = gcs_uri_to_doc_id(uri)

    assert doc_id == gcs_uri_to_doc_id(uri)
    assert "/" not in doc_id
    assert doc_id != gcs_uri_to_doc_id("gs://bucket/path/to/other.pdf")


def test_extract_first_page_text_returns_first_page_only(mocker):
    mock_reader_class = mocker.patch("ingestor.main.PdfReader")
    first_page = MagicMock()
    first_page.extract_text.return_value = "Title\nAuthors\nJournal of Things"
    second_page = MagicMock()
    mock_reader_class.return_value.pages = [first_page, second_page]

    text = extract_first_page_text(b"%PDF-1.4 ...")

    assert text == "Title\nAuthors\nJournal of Things"
    second_page.extract_text.assert_not_called()


def test_extract_first_page_text_handles_no_pages(mocker):
    mock_reader_class = mocker.patch("ingestor.main.PdfReader")
    mock_reader_class.return_value.pages = []

    assert extract_first_page_text(b"%PDF-1.4 ...") == ""


def test_extract_paper_metadata_parses_json_response(mocker):
    mock_model_class = mocker.patch("ingestor.main.GenerativeModel")
    mock_response = MagicMock()
    mock_response.text = (
        '{"title": "Geothermal Energy Potential", '
        '"authors": ["A. One", "B. Two"], '
        '"journal": "Journal of Renewable Energy"}'
    )
    mock_model_class.return_value.generate_content.return_value = mock_response

    metadata = extract_paper_metadata("first page text", "gemini-2.5-flash")

    assert metadata == {
        "title": "Geothermal Energy Potential",
        "authors": ["A. One", "B. Two"],
        "journal": "Journal of Renewable Energy",
    }
    mock_model_class.assert_called_once_with("gemini-2.5-flash")


def test_extract_paper_metadata_strips_markdown_fences(mocker):
    mock_model_class = mocker.patch("ingestor.main.GenerativeModel")
    mock_response = MagicMock()
    mock_response.text = '```json\n{"title": "T", "authors": [], "journal": null}\n```'
    mock_model_class.return_value.generate_content.return_value = mock_response

    metadata = extract_paper_metadata("first page text", "gemini-2.5-flash")

    assert metadata == {"title": "T", "authors": [], "journal": None}


def test_extract_paper_metadata_returns_none_for_blank_text():
    assert extract_paper_metadata("   \n  ", "gemini-2.5-flash") is None


def test_extract_paper_metadata_returns_none_on_unparseable_response(mocker):
    mock_model_class = mocker.patch("ingestor.main.GenerativeModel")
    mock_response = MagicMock()
    mock_response.text = "Sorry, I can't help with that."
    mock_model_class.return_value.generate_content.return_value = mock_response

    assert extract_paper_metadata("first page text", "gemini-2.5-flash") is None


def test_store_paper_metadata_writes_to_firestore_keyed_by_hashed_uri(mocker):
    mock_firestore_client = mocker.patch("ingestor.main.firestore.Client")
    mock_collection = mock_firestore_client.return_value.collection.return_value
    mock_document = mock_collection.document.return_value

    gcs_uri = "gs://bucket/papers/geothermal.pdf"
    metadata = {"title": "T", "authors": ["A"], "journal": "J"}

    store_paper_metadata(gcs_uri, metadata)

    mock_firestore_client.return_value.collection.assert_called_once_with(
        "paper_metadata"
    )
    mock_collection.document.assert_called_once_with(gcs_uri_to_doc_id(gcs_uri))
    mock_document.set.assert_called_once_with({"gcs_uri": gcs_uri, **metadata})


def test_ingest_paper_metadata_extracts_and_stores(mocker):
    mock_storage_client = mocker.patch("ingestor.main.storage.Client")
    mock_blob = mock_storage_client.return_value.bucket.return_value.blob.return_value
    mock_blob.download_as_bytes.return_value = b"%PDF-1.4 ..."

    mocker.patch("ingestor.main.extract_first_page_text", return_value="page text")
    mock_extract_metadata = mocker.patch(
        "ingestor.main.extract_paper_metadata",
        return_value={"title": "T", "authors": ["A"], "journal": "J"},
    )
    mock_store = mocker.patch("ingestor.main.store_paper_metadata")

    ingest_paper_metadata(
        "test-bucket", "paper.pdf", "gs://test-bucket/paper.pdf", "gemini-2.5-flash"
    )

    mock_extract_metadata.assert_called_once_with("page text", "gemini-2.5-flash")
    mock_store.assert_called_once_with(
        "gs://test-bucket/paper.pdf", {"title": "T", "authors": ["A"], "journal": "J"}
    )


def test_ingest_paper_metadata_skips_storage_when_nothing_extracted(mocker):
    mock_storage_client = mocker.patch("ingestor.main.storage.Client")
    mock_blob = mock_storage_client.return_value.bucket.return_value.blob.return_value
    mock_blob.download_as_bytes.return_value = b"%PDF-1.4 ..."

    mocker.patch("ingestor.main.extract_first_page_text", return_value="")
    mocker.patch("ingestor.main.extract_paper_metadata", return_value=None)
    mock_store = mocker.patch("ingestor.main.store_paper_metadata")

    ingest_paper_metadata(
        "test-bucket", "paper.pdf", "gs://test-bucket/paper.pdf", "gemini-2.5-flash"
    )

    mock_store.assert_not_called()


def test_ingest_paper_metadata_swallows_errors(mocker):
    mocker.patch(
        "ingestor.main.storage.Client", side_effect=Exception("download failed")
    )
    mock_store = mocker.patch("ingestor.main.store_paper_metadata")

    # Citation enrichment is best-effort: failures must not raise or block ingestion.
    ingest_paper_metadata(
        "test-bucket", "paper.pdf", "gs://test-bucket/paper.pdf", "gemini-2.5-flash"
    )

    mock_store.assert_not_called()
