locals {
  services = toset([
    "secretmanager.googleapis.com",
    "cloudfunctions.googleapis.com",
    "run.googleapis.com",
    "eventarc.googleapis.com",
    "cloudbuild.googleapis.com",
    "storage.googleapis.com",
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "pubsub.googleapis.com",
    "firestore.googleapis.com"
  ])
}

resource "google_project_service" "enabled_apis" {
  for_each = local.services
  project  = var.project_id
  service  = each.key

  disable_dependent_services = false
  disable_on_destroy         = false
}

resource "time_sleep" "wait_for_apis" {
  depends_on      = [google_project_service.enabled_apis]
  create_duration = "60s"
}

resource "google_storage_bucket" "rag_document_source" {
  name                        = lower(var.bucket_name)
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  depends_on                  = [time_sleep.wait_for_apis]
}

resource "google_storage_bucket" "function_source" {
  name                        = "${var.project_id}-${var.function_name}-source"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
}

resource "google_storage_bucket" "agent_engine_staging" {
  name                        = lower(var.agent_engine_staging_bucket_name)
  location                    = var.agent_engine_region
  uniform_bucket_level_access = true
  force_destroy               = true
  depends_on                  = [time_sleep.wait_for_apis]
}

data "archive_file" "function_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src/ingestor"
  output_path = "${path.module}/../src/ingestor.zip"
}

resource "google_storage_bucket_object" "function_zip" {
  name   = "ingestor-${data.archive_file.function_zip.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.function_zip.output_path
}

resource "google_service_account" "rag_ingestor_sa" {
  account_id   = var.service_account_id
  display_name = "RAG Ingestor Service Account"
}

resource "google_project_iam_member" "eventarc_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.rag_ingestor_sa.email}"
}

resource "google_project_iam_member" "run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.rag_ingestor_sa.email}"
}

resource "google_project_iam_member" "secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.rag_ingestor_sa.email}"
}

resource "google_project_iam_member" "aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.rag_ingestor_sa.email}"
}

# Stores extracted paper metadata (title/authors/journal), keyed by a hash of
# the GCS URI. Written by the ingestor at ingest time, read by the agent at
# query time to enrich citations — both run as rag_ingestor_sa.
resource "google_firestore_database" "paper_metadata" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"
  depends_on  = [time_sleep.wait_for_apis]
}

resource "google_project_iam_member" "firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.rag_ingestor_sa.email}"
}

resource "google_secret_manager_secret" "rag_corpus_id" {
  secret_id = var.secret_id
  replication {
    auto {}
  }
  depends_on = [time_sleep.wait_for_apis]
}

data "google_storage_project_service_account" "gcs_account" {
  project    = var.project_id
  depends_on = [time_sleep.wait_for_apis]
}

resource "google_project_iam_member" "gcs_pubsub_publishing" {
  project    = var.project_id
  role       = "roles/pubsub.publisher"
  member     = "serviceAccount:${data.google_storage_project_service_account.gcs_account.email_address}"
  depends_on = [time_sleep.wait_for_apis]
}

resource "google_cloudfunctions2_function" "ingestor" {
  name        = var.function_name
  location    = var.region
  description = "Ingests PDFs from GCS into Vertex AI RAG Corpus"

  build_config {
    runtime     = "python310"
    entry_point = "process_file"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.function_zip.name
      }
    }
  }

  service_config {
    max_instance_count    = 1
    min_instance_count    = 0
    available_memory      = "512M"
    timeout_seconds       = 540
    service_account_email = google_service_account.rag_ingestor_sa.email
    environment_variables = {
      GCP_SECRET_ID         = var.secret_id
      GOOGLE_CLOUD_PROJECT  = var.project_id
      GOOGLE_CLOUD_LOCATION = var.region
      GEMINI_MODEL_NAME     = var.gemini_model_name
    }
  }

  event_trigger {
    event_type            = "google.cloud.storage.object.v1.finalized"
    trigger_region        = var.region
    service_account_email = google_service_account.rag_ingestor_sa.email
    event_filters {
      attribute = "bucket"
      value     = google_storage_bucket.rag_document_source.name
    }
    retry_policy = "RETRY_POLICY_DO_NOT_RETRY"
  }

  depends_on = [
    google_project_iam_member.eventarc_receiver,
    google_project_iam_member.run_invoker,
    google_project_iam_member.secret_accessor,
    google_project_iam_member.aiplatform_user,
    google_project_iam_member.gcs_pubsub_publishing,
    google_project_iam_member.firestore_user,
    google_firestore_database.paper_metadata,
    time_sleep.wait_for_apis
  ]
}

resource "null_resource" "init_rag_corpus" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = "echo 'Intended logic: uv run python scripts/init_rag_corpus.py'"
  }

  depends_on = [
    google_secret_manager_secret.rag_corpus_id
  ]
}
