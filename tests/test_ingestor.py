from unittest.mock import MagicMock

from cloudevents.http import CloudEvent
from src.ingestor.main import get_corpus_id, process_file


def test_get_corpus_id(mocker):
    # Mock the SecretManagerServiceClient
    mock_client_class = mocker.patch(
        "src.ingestor.main.secretmanager.SecretManagerServiceClient"
    )
    mock_client_instance = mock_client_class.return_value

    # Setup the mock response
    mock_response = MagicMock()
    mock_response.payload.data.decode.return_value = "mock_corpus_id  \n"
    mock_client_instance.access_secret_version.return_value = mock_response

    # Mock environment variables
    mocker.patch("src.ingestor.main.project_id", "test-project")
    mocker.patch("src.ingestor.main.secret_id", "test-secret")

    corpus_id = get_corpus_id()

    assert corpus_id == "mock_corpus_id"
    mock_client_instance.access_secret_version.assert_called_once_with(
        request={"name": "projects/test-project/secrets/test-secret/versions/latest"}
    )


def test_process_file_ignores_non_pdf(mocker):
    mock_get_corpus = mocker.patch("src.ingestor.main.get_corpus_id")
    mock_import = mocker.patch("src.ingestor.main.rag.import_files")

    attributes = {"type": "google.cloud.storage.object.v1.finalized", "source": "test"}
    data = {"bucket": "test-bucket", "name": "test_document.txt"}
    event = CloudEvent(attributes, data)

    process_file(event)

    mock_get_corpus.assert_not_called()
    mock_import.assert_not_called()


def test_process_file_missing_data(mocker):
    mock_get_corpus = mocker.patch("src.ingestor.main.get_corpus_id")
    mock_import = mocker.patch("src.ingestor.main.rag.import_files")

    attributes = {"type": "google.cloud.storage.object.v1.finalized", "source": "test"}
    data = {"bucket": "test-bucket"}  # Missing name
    event = CloudEvent(attributes, data)

    process_file(event)

    mock_get_corpus.assert_not_called()
    mock_import.assert_not_called()


def test_process_file_success(mocker):
    mock_get_corpus = mocker.patch("src.ingestor.main.get_corpus_id")
    mock_get_corpus.return_value = "mock_corpus_123"

    mock_vertexai_init = mocker.patch("src.ingestor.main.vertexai.init")
    mock_import_files = mocker.patch("src.ingestor.main.rag.import_files")
    mock_response = MagicMock()
    mock_response.imported_rag_files_count = 1
    mock_import_files.return_value = mock_response

    mock_llm_parser = mocker.patch("src.ingestor.main.rag.LlmParserConfig", create=True)
    mock_chunking_config = mocker.patch(
        "src.ingestor.main.rag.ChunkingConfig", create=True
    )
    mock_transformation_config = mocker.patch(
        "src.ingestor.main.rag.TransformationConfig", create=True
    )

    # Setup event
    attributes = {"type": "google.cloud.storage.object.v1.finalized", "source": "test"}
    data = {"bucket": "test-bucket", "name": "test_document.pdf"}
    event = CloudEvent(attributes, data)

    mocker.patch("src.ingestor.main.project_id", "test-project")
    mocker.patch("src.ingestor.main.location", "us-central1")

    process_file(event)

    mock_get_corpus.assert_called_once()
    mock_vertexai_init.assert_called_once_with(
        project="test-project", location="us-central1"
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
    mock_get_corpus = mocker.patch("src.ingestor.main.get_corpus_id")
    mock_get_corpus.side_effect = Exception("Secret Manager Error")
    mock_import = mocker.patch("src.ingestor.main.rag.import_files")

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
    mock_get_corpus = mocker.patch("src.ingestor.main.get_corpus_id")
    mock_get_corpus.return_value = "mock_corpus_123"

    mocker.patch("src.ingestor.main.vertexai.init")

    mock_import_files = mocker.patch("src.ingestor.main.rag.import_files")
    mock_import_files.side_effect = Exception("Import Error")

    attributes = {"type": "google.cloud.storage.object.v1.finalized", "source": "test"}
    data = {"bucket": "test-bucket", "name": "test_document.pdf"}
    event = CloudEvent(attributes, data)

    mocker.patch("src.ingestor.main.project_id", "test-project")
    mocker.patch("src.ingestor.main.location", "us-central1")

    import pytest

    with pytest.raises(Exception, match="Import Error"):
        process_file(event)

    mock_import_files.assert_called_once()
