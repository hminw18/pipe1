# Foundation for the Pipe1 web app (docs/Web_Architecture.md §3).
# Not yet included: Cloud Run services, Cloud Tasks queues, load balancer, Cloud Armor,
# IAP. They are added once the API and worker images exist.

locals {
  name = "pipe1-${var.environment}"

  services = [
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "iap.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudtasks.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.services)

  service            = each.value
  disable_on_destroy = false
}

# --- Network: private IP for Cloud SQL ------------------------------------------

resource "google_compute_network" "main" {
  name                    = local.name
  auto_create_subnetworks = false

  depends_on = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "main" {
  name                     = local.name
  network                  = google_compute_network.main.id
  region                   = var.region
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true
}

resource "google_compute_global_address" "private_services" {
  name          = "${local.name}-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

# --- Cloud SQL for PostgreSQL ----------------------------------------------------

resource "google_sql_database_instance" "main" {
  name                = local.name
  database_version    = "POSTGRES_17"
  region              = var.region
  deletion_protection = var.deletion_protection

  settings {
    edition           = "ENTERPRISE"
    tier              = var.db_tier
    availability_type = var.db_high_availability ? "REGIONAL" : "ZONAL"

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.main.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "18:00" # 03:00 KST
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "pipe1" {
  name     = "pipe1"
  instance = google_sql_database_instance.main.name
}

# Password roles for now; moving to IAM database authentication is a follow-up.
resource "random_password" "db" {
  for_each = toset(["pipe1_migrator", "pipe1_app"])

  length  = 40
  special = false
}

resource "google_sql_user" "db" {
  for_each = random_password.db

  name     = each.key
  instance = google_sql_database_instance.main.name
  password = each.value.result
}

resource "google_secret_manager_secret" "db_password" {
  for_each = random_password.db

  secret_id = "${local.name}-db-${replace(each.key, "_", "-")}-password"
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "db_password" {
  for_each = random_password.db

  secret      = google_secret_manager_secret.db_password[each.key].id
  secret_data = each.value.result
}

# --- Cloud Storage: videos, captures, exports --------------------------------------

resource "google_storage_bucket" "media" {
  name                        = "${var.project_id}-${local.name}-media"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "AbortIncompleteMultipartUpload"
    }
  }

  # Browsers upload (signed resumable sessions) and stream video (signed URLs, range
  # requests) directly from the bucket; only the app's own origin may do so.
  dynamic "cors" {
    for_each = length(var.app_origins) > 0 ? [1] : []
    content {
      origin          = var.app_origins
      method          = ["GET", "HEAD", "PUT", "POST"]
      response_header = ["Content-Type", "Content-Range", "Range", "Location", "x-goog-resumable"]
      max_age_seconds = 3600
    }
  }

  depends_on = [google_project_service.enabled]
}

# --- KMS: encrypts stored TOTP secrets ----------------------------------------------

resource "google_kms_key_ring" "main" {
  name     = local.name
  location = var.region

  depends_on = [google_project_service.enabled]
}

resource "google_kms_crypto_key" "totp" {
  name            = "totp-secrets"
  key_ring        = google_kms_key_ring.main.id
  rotation_period = "7776000s" # 90 days

  lifecycle {
    prevent_destroy = true
  }
}

# --- CSRF token signing key (docs/Web_Auth_Spec.md §7) ----------------------------

resource "random_password" "csrf_key" {
  length  = 64
  special = false
}

resource "google_secret_manager_secret" "csrf_key" {
  secret_id = "${local.name}-csrf-key"
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "csrf_key" {
  secret      = google_secret_manager_secret.csrf_key.id
  secret_data = random_password.csrf_key.result
}

resource "google_secret_manager_secret_iam_member" "api_csrf_key" {
  secret_id = google_secret_manager_secret.csrf_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.api.member
}

# --- Secrets added by hand -------------------------------------------------------
# Terraform creates the secret; the value is added outside Terraform so it never
# lands in state:
#   gcloud secrets versions add pipe1-dev-resend-api-key --data-file=-

resource "google_secret_manager_secret" "resend_api_key" {
  secret_id = "${local.name}-resend-api-key"
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# --- Container images --------------------------------------------------------------

resource "google_artifact_registry_repository" "images" {
  repository_id = local.name
  location      = var.region
  format        = "DOCKER"

  depends_on = [google_project_service.enabled]
}

# --- Service accounts and least-privilege grants -----------------------------------

resource "google_service_account" "api" {
  account_id   = "${local.name}-api"
  display_name = "Pipe1 ${var.environment} API (Cloud Run)"
}

resource "google_service_account" "worker" {
  account_id   = "${local.name}-worker"
  display_name = "Pipe1 ${var.environment} background worker"
}

locals {
  runtime_accounts = {
    api    = google_service_account.api.member
    worker = google_service_account.worker.member
  }
}

resource "google_project_iam_member" "cloudsql_client" {
  for_each = local.runtime_accounts

  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = each.value
}

resource "google_storage_bucket_iam_member" "media_object_admin" {
  for_each = local.runtime_accounts

  bucket = google_storage_bucket.media.name
  role   = "roles/storage.objectAdmin"
  member = each.value
}

resource "google_secret_manager_secret_iam_member" "app_db_password" {
  for_each = local.runtime_accounts

  secret_id = google_secret_manager_secret.db_password["pipe1_app"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}


# Only the API handles MFA enrollment and login.
resource "google_kms_crypto_key_iam_member" "api_totp" {
  crypto_key_id = google_kms_crypto_key.totp.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_service_account.api.member
}
