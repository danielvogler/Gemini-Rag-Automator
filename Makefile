.PHONY: all init tf-plan tf-apply tf-destroy tf-check init-rag ingest-test clean test pre-commit

include .env
export $(shell sed 's/=.*//' .env)

UV_BIN ?= /Library/Frameworks/Python.framework/Versions/3.10/bin/uv

all: init tf-plan

init:
	@echo "Initializing environment..."
	cd terraform && terraform init

tf-check:
	@echo "Running Terraform validation and formatting checks..."
	cd terraform && terraform fmt -check && terraform init -backend=false && terraform validate -no-color

pre-commit:
	@echo "Running pre-commit hooks on all files..."
	$(UV_BIN) run pre-commit run --all-files

tf-plan:
	@echo "Planning terraform..."
	cd terraform && terraform plan -var="project_id=$${GOOGLE_CLOUD_PROJECT}" -var="region=$${GOOGLE_CLOUD_LOCATION}" -var="bucket_name=$${GCS_BUCKET_NAME}" -var="secret_id=$${GCP_SECRET_ID}" -var="corpus_display_name=$${RAG_CORPUS_DISPLAY_NAME}" -var="corpus_description=$${RAG_CORPUS_DESCRIPTION}" -var="service_account_id=$${SERVICE_ACCOUNT_ID}" -var="function_name=$${CLOUD_FUNCTION_NAME}" -var="gemini_model_name=$${GEMINI_MODEL_NAME}"

tf-apply:
	@echo "Applying terraform..."
	cd terraform && terraform apply -auto-approve -var="project_id=$${GOOGLE_CLOUD_PROJECT}" -var="region=$${GOOGLE_CLOUD_LOCATION}" -var="bucket_name=$${GCS_BUCKET_NAME}" -var="secret_id=$${GCP_SECRET_ID}" -var="corpus_display_name=$${RAG_CORPUS_DISPLAY_NAME}" -var="corpus_description=$${RAG_CORPUS_DESCRIPTION}" -var="service_account_id=$${SERVICE_ACCOUNT_ID}" -var="function_name=$${CLOUD_FUNCTION_NAME}" -var="gemini_model_name=$${GEMINI_MODEL_NAME}"

tf-destroy:
	@echo "Destroying terraform infrastructure..."
	cd terraform && terraform destroy -auto-approve -var="project_id=$${GOOGLE_CLOUD_PROJECT}" -var="region=$${GOOGLE_CLOUD_LOCATION}" -var="bucket_name=$${GCS_BUCKET_NAME}" -var="secret_id=$${GCP_SECRET_ID}" -var="corpus_display_name=$${RAG_CORPUS_DISPLAY_NAME}" -var="corpus_description=$${RAG_CORPUS_DESCRIPTION}" -var="service_account_id=$${SERVICE_ACCOUNT_ID}" -var="function_name=$${CLOUD_FUNCTION_NAME}" -var="gemini_model_name=$${GEMINI_MODEL_NAME}"

init-rag:
	@echo "Initializing Vertex AI RAG Corpus..."
	$(UV_BIN) run python scripts/init_rag_corpus.py

query:
	@if [ -z "$(Q)" ]; then echo "Usage: make query Q=\"your question\""; exit 1; fi
	@echo "Querying RAG Engine..."
	$(UV_BIN) run python scripts/query_rag.py "$(Q)"

list-files:
	@echo "Listing ingested files in Vertex AI RAG Corpus..."
	$(UV_BIN) run python scripts/list_rag_files.py

ingest-test:
	@echo "Testing ADK CLI Integration..."
	echo "test_document.pdf" | $(UV_BIN) run adk run ingest_test

test:
	@echo "Running tests..."
	$(UV_BIN) run pytest tests/ -v --cov=src --cov-report=term-missing | tee logs/test_results.log

clean:
	rm -rf terraform/.terraform
	rm -f terraform/.terraform.lock.hcl
	rm -f logs/*.log
