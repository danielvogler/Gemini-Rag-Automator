variable "project_id" {
  description = "The GCP project ID"
  type        = string
}

variable "region" {
  description = "The GCP region"
  type        = string
}

variable "bucket_name" {
  description = "The name of the GCS bucket to store documents"
  type        = string
}

variable "secret_id" {
  description = "The secret ID for the RAG Corpus"
  type        = string
}

variable "corpus_display_name" {
  description = "Display name for the RAG Corpus"
  type        = string
}

variable "corpus_description" {
  description = "Description for the RAG Corpus"
  type        = string
}

variable "service_account_id" {
  description = "The ID of the Service Account for the RAG ingestor"
  type        = string
}

variable "function_name" {
  description = "The name of the Cloud Function for the RAG ingestor"
  type        = string
}

variable "gemini_model_name" {
  description = "The name of the Gemini model to use"
  type        = string
}

variable "agent_engine_region" {
  description = "GCP region for the Vertex AI Agent Engine deployment. May differ from var.region (the corpus region)."
  type        = string
}

variable "agent_engine_staging_bucket_name" {
  description = "GCS bucket used by Vertex AI Agent Engine for code packaging during deploy."
  type        = string
}
