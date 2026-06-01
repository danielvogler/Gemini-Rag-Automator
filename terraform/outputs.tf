output "function_uri" {
  value = google_cloudfunctions2_function.ingestor.service_config[0].uri
}

output "bucket_name" {
  value = google_storage_bucket.rag_document_source.name
}

output "secret_name" {
  value = google_secret_manager_secret.rag_corpus_id.name
}

output "agent_engine_staging_bucket" {
  value       = "gs://${google_storage_bucket.agent_engine_staging.name}"
  description = "GCS URI of the Agent Engine staging bucket (use as STAGING_BUCKET in deploy)."
}
