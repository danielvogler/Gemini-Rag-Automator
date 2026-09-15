.PHONY: all init tf-plan tf-apply tf-destroy tf-check init-rag ingest-test clean test pre-commit \
        agent-run agent-deploy agent-query agent-delete

include .env
export $(shell sed 's/=.*//' .env)

# Try to find uv in PATH, fallback to common install locations
UV_BIN ?= $(shell command -v uv 2>/dev/null || command -v ~/.local/bin/uv 2>/dev/null || command -v ~/.cargo/bin/uv 2>/dev/null || echo uv)

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

TF_VARS = -var="project_id=$${GOOGLE_CLOUD_PROJECT}" \
          -var="region=$${GOOGLE_CLOUD_LOCATION}" \
          -var="bucket_name=$${GCS_BUCKET_NAME}" \
          -var="secret_id=$${GCP_SECRET_ID}" \
          -var="corpus_display_name=$${RAG_CORPUS_DISPLAY_NAME}" \
          -var="corpus_description=$${RAG_CORPUS_DESCRIPTION}" \
          -var="service_account_id=$${SERVICE_ACCOUNT_ID}" \
          -var="function_name=$${CLOUD_FUNCTION_NAME}" \
          -var="gemini_model_name=$${GEMINI_MODEL_NAME}" \
          -var="agent_engine_region=$${AGENT_ENGINE_LOCATION}" \
          -var="agent_engine_staging_bucket_name=$${AGENT_STAGING_BUCKET_NAME}"

tf-plan:
	@echo "Planning terraform..."
	cd terraform && terraform plan $(TF_VARS)

tf-apply:
	@echo "Applying terraform..."
	cd terraform && terraform apply -auto-approve $(TF_VARS)

tf-destroy:
	@echo "Destroying terraform infrastructure..."
	cd terraform && terraform destroy -auto-approve $(TF_VARS)

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
	@echo "Testing local ingestion CLI..."
	echo "test_document.pdf" | $(UV_BIN) run gra-ingest run ingest_test

agent-run:
	@echo "Launching the ADK web UI for the local agent on http://localhost:8000 ..."
	$(UV_BIN) run adk web src/

agent-deploy:
	@echo "Deploying corpus-only ADK agent to Vertex AI Agent Engine..."
	$(UV_BIN) run python scripts/deploy_agent.py

agent-query:
	@if [ -z "$(Q)" ]; then echo "Usage: make agent-query Q=\"your question\" [STRUCTURED=1]"; exit 1; fi
	@echo "Querying deployed agent..."
	@if [ "$(STRUCTURED)" = "1" ]; then \
		$(UV_BIN) run python scripts/query_agent.py "$(Q)" --structured; \
	else \
		$(UV_BIN) run python scripts/query_agent.py "$(Q)"; \
	fi

agent-delete:
	@if [ -z "$$AGENT_ENGINE_ID" ]; then echo "AGENT_ENGINE_ID is not set in .env; nothing to delete."; exit 1; fi
	@echo "Deleting deployed agent: $$AGENT_ENGINE_ID"
	$(UV_BIN) run python -c "import os, vertexai; from vertexai import agent_engines; eid='$$AGENT_ENGINE_ID'.rsplit('/', 1)[-1]; vertexai.init(project='$$GOOGLE_CLOUD_PROJECT', location='$$AGENT_ENGINE_LOCATION'); agent_engines.get(eid).delete(force=True); print('Deleted.')"

test:
	@echo "Running tests..."
	$(UV_BIN) run pytest tests/ -v --cov=src --cov-report=term-missing | tee logs/test_results.log

clean:
	rm -rf terraform/.terraform
	rm -f terraform/.terraform.lock.hcl
	rm -f logs/*.log
