# Cloud Run services, Cloud Tasks, migrations, and the HTTPS edge
# (docs/Web_Architecture.md §2-§3, docs/Web_Auth_Spec.md §8, §10).
#
# Services are created only once images exist (var.api_image / var.worker_image), and the
# load balancer only once a domain is chosen (var.domain), so the foundation in main.tf can
# be applied first.

locals {
  deploy_services = var.api_image != "" && var.worker_image != ""
  deploy_edge     = local.deploy_services && var.domain != ""
  app_origin      = var.domain != "" ? "https://${var.domain}" : "https://localhost"
  sql_connection  = google_sql_database_instance.main.connection_name
  tasks_queue     = "projects/${var.project_id}/locations/${var.region}/queues/${google_cloud_tasks_queue.tasks.name}"
  # Deterministic Cloud Run URLs, used both to call a service and as the OIDC audience the
  # service verifies, so the two always match.
  run_url_suffix = "${data.google_project.current.number}.${var.region}.run.app"
  worker_url     = "https://${local.name}-worker-${local.run_url_suffix}"
  mailer_url     = "https://${local.name}-mailer-${local.run_url_suffix}"
  database_url   = "postgresql+psycopg://pipe1_app@${google_sql_database_instance.main.private_ip_address}/pipe1?sslmode=require"

  common_env = {
    PIPE1_ENVIRONMENT              = var.environment
    PIPE1_APP_ORIGIN               = local.app_origin
    PIPE1_STORAGE_BACKEND          = "gcs"
    PIPE1_MEDIA_BUCKET             = google_storage_bucket.media.name
    PIPE1_JOB_QUEUE                = "cloud_tasks"
    PIPE1_MEDIA_CLIENT             = "worker"
    PIPE1_EMAIL_QUEUE              = "cloud_tasks"
    PIPE1_EMAIL_SENDER             = "sendgrid"
    PIPE1_EMAIL_FROM               = var.email_from
    PIPE1_TOTP_KMS_KEY             = google_kms_crypto_key.totp.id
    PIPE1_TASKS_SERVICE_ACCOUNT    = google_service_account.tasks.email
    PIPE1_CLOUD_TASKS_QUEUE        = local.tasks_queue
    PIPE1_MAILER_URL               = local.mailer_url
    PIPE1_DATABASE_URL             = local.database_url
    PIPE1_API_SERVICE_ACCOUNT      = google_service_account.api.email
    PIPE1_BREACHED_PASSWORD_SOURCE = var.breached_password_source
    # Google load balancer appends "<client>,<lb>" to X-Forwarded-For.
    PIPE1_TRUSTED_PROXY_HOPS = "2"
  }
}

variable "api_image" {
  description = "API container image (Artifact Registry). Empty until the first build."
  type        = string
  default     = ""
}

variable "worker_image" {
  description = "Worker container image (Artifact Registry). Empty until the first build."
  type        = string
  default     = ""
}

variable "domain" {
  description = "Public hostname of the app, e.g. app.example.com. Empty until chosen."
  type        = string
  default     = ""
}

variable "email_from" {
  description = "Sender for transactional email, on the SendGrid-authenticated domain."
  type        = string
  default     = "Pipe1 <no-reply@example.com>"
}

variable "sendgrid_configured" {
  description = "Set true once a SendGrid API key version exists in the sendgrid secret; the mailer service is created only then (until then email tasks wait and retry)."
  type        = bool
  default     = false
}

variable "breached_password_source" {
  description = "hibp or offline (docs/Web_Auth_Spec.md §4)."
  type        = string
  default     = "hibp"
}

variable "platform_admin_google_accounts" {
  description = "Google identities allowed through IAP to the platform-admin API, e.g. user:ops@example.com."
  type        = list(string)
  default     = []
}

variable "github_repository" {
  description = "owner/name of the GitHub repository allowed to deploy via Workload Identity Federation."
  type        = string
  default     = "hminw18/pipe1"
}

# --- Service accounts not in main.tf ---------------------------------------------------

resource "google_service_account" "tasks" {
  account_id   = "${local.name}-tasks"
  display_name = "Pipe1 ${var.environment} Cloud Tasks invoker (signs OIDC tokens for the worker)"
}

resource "google_service_account" "deployer" {
  account_id   = "${local.name}-deployer"
  display_name = "Pipe1 ${var.environment} CI deployer (GitHub Actions via WIF)"
}

resource "google_service_account" "migrator" {
  account_id   = "${local.name}-migrator"
  display_name = "Pipe1 ${var.environment} database migrations job"
}

# --- Cloud Tasks ------------------------------------------------------------------------

resource "google_cloud_tasks_queue" "tasks" {
  name     = "${local.name}-tasks"
  location = var.region

  rate_limits {
    max_concurrent_dispatches = 20
  }
  # Jobs record their own failure and are not retried by the worker; email sends are.
  retry_config {
    max_attempts = 8
    min_backoff  = "5s"
    max_backoff  = "600s"
  }
  depends_on = [google_project_service.enabled]
}

# --- Network for the runtime ----------------------------------------------------------
# Worker: main subnet, no NAT, so it can reach only Google APIs (Private Google Access) and
# the database: a crafted video cannot make it talk to the internet.
# API: its own subnet with Cloud NAT, for the breached-password API; its calls to the
# internal-only worker go through the VPC.

resource "google_compute_subnetwork" "api" {
  name                     = "${local.name}-api"
  network                  = google_compute_network.main.id
  region                   = var.region
  ip_cidr_range            = "10.11.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_router" "main" {
  name    = local.name
  network = google_compute_network.main.id
  region  = var.region
}

resource "google_compute_router_nat" "api" {
  name                               = "${local.name}-api"
  router                             = google_compute_router.main.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.api.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}

# --- IAM for the runtime --------------------------------------------------------------

# Signing storage URLs via IAM signBlob (no key files on Cloud Run).
resource "google_service_account_iam_member" "self_sign" {
  for_each = {
    api    = google_service_account.api
    worker = google_service_account.worker
  }
  service_account_id = each.value.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value.member
}

# The API enqueues tasks that run as the tasks account.
resource "google_project_iam_member" "api_enqueue" {
  project = var.project_id
  role    = "roles/cloudtasks.enqueuer"
  member  = google_service_account.api.member
}

resource "google_service_account_iam_member" "api_acts_as_tasks" {
  service_account_id = google_service_account.tasks.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.api.member
}

resource "google_secret_manager_secret_iam_member" "migrator_db_password" {
  secret_id = google_secret_manager_secret.db_password["pipe1_migrator"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.migrator.member
}

resource "google_project_iam_member" "migrator_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = google_service_account.migrator.member
}

# --- Cloud Run: worker (ingress internal; invoked by Cloud Tasks and the API) ------------

resource "google_cloud_run_v2_service" "worker" {
  count               = local.deploy_services ? 1 : 0
  name                = "${local.name}-worker"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false

  template {
    service_account                  = google_service_account.worker.email
    timeout                          = "3600s"
    max_instance_request_concurrency = 1 # one media job per instance: CPU-heavy and isolated

    scaling {
      max_instance_count = 10
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
    }

    containers {
      image = var.worker_image
      resources {
        limits = { cpu = "4", memory = "8Gi" }
      }
      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name  = "PIPE1_WORKER_URL"
        value = local.worker_url
      }
      env {
        name = "PGPASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password["pipe1_app"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "PIPE1_CSRF_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.csrf_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

# Mailer: same worker image; only receives email tasks. No VPC, so it can reach SendGrid,
# and it has no database or media access to lose.
resource "google_cloud_run_v2_service" "mailer" {
  count               = local.deploy_services && var.sendgrid_configured ? 1 : 0
  name                = "${local.name}-mailer"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false

  template {
    service_account = google_service_account.mailer.email
    scaling {
      max_instance_count = 3
    }
    containers {
      image = var.worker_image
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name  = "PIPE1_WORKER_URL"
        value = local.mailer_url
      }
      env {
        name = "PIPE1_CSRF_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.csrf_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "PIPE1_SENDGRID_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.sendgrid_api_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

resource "google_service_account" "mailer" {
  account_id   = "${local.name}-mailer"
  display_name = "Pipe1 ${var.environment} mailer (SendGrid only)"
}

resource "google_secret_manager_secret_iam_member" "mailer_secrets" {
  for_each = {
    sendgrid = google_secret_manager_secret.sendgrid_api_key.id
    csrf     = google_secret_manager_secret.csrf_key.id
  }
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.mailer.member
}

resource "google_cloud_run_v2_service_iam_member" "mailer_invoker" {
  count    = local.deploy_services && var.sendgrid_configured ? 1 : 0
  name     = google_cloud_run_v2_service.mailer[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.tasks.member
}

resource "google_secret_manager_secret_iam_member" "worker_csrf_key" {
  secret_id = google_secret_manager_secret.csrf_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.worker.member
}

resource "google_cloud_run_v2_service_iam_member" "worker_invokers" {
  for_each = local.deploy_services ? {
    tasks = google_service_account.tasks.member
    api   = google_service_account.api.member
  } : {}
  name     = google_cloud_run_v2_service.worker[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.value
}

data "google_project" "current" {}

# --- Cloud Run: API (ingress only through the load balancer) -----------------------------

resource "google_cloud_run_v2_service" "api" {
  count               = local.deploy_services ? 1 : 0
  name                = "${local.name}-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false

  template {
    service_account = google_service_account.api.email
    timeout         = "120s"

    scaling {
      max_instance_count = 20
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.api.id
      }
    }

    containers {
      image = var.api_image
      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }
      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name  = "PIPE1_WORKER_URL"
        value = local.worker_url
      }
      env {
        name  = "PIPE1_STORAGE_SIGNER_EMAIL"
        value = google_service_account.api.email
      }
      env {
        name = "PGPASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password["pipe1_app"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "PIPE1_CSRF_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.csrf_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

# Public traffic reaches the API only through the load balancer (ingress setting above);
# the load balancer itself invokes as allUsers.
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  count    = local.deploy_edge ? 1 : 0
  name     = google_cloud_run_v2_service.api[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- Migrations job (runs as pipe1_migrator; triggered by CI before each deploy) ---------

resource "google_cloud_run_v2_job" "migrate" {
  count               = local.deploy_services ? 1 : 0
  name                = "${local.name}-migrate"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.migrator.email
      max_retries     = 0
      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.main.id
          subnetwork = google_compute_subnetwork.main.id
        }
      }
      containers {
        image   = var.api_image
        command = ["alembic", "upgrade", "head"]
        env {
          name  = "PIPE1_MIGRATION_DATABASE_URL"
          value = "postgresql+psycopg://pipe1_migrator@${google_sql_database_instance.main.private_ip_address}/pipe1?sslmode=require"
        }
        env {
          name = "PGPASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_password["pipe1_migrator"].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }
}

# --- Static frontend bucket ---------------------------------------------------------------

resource "google_storage_bucket" "web" {
  name                        = "${var.project_id}-${local.name}-web"
  location                    = var.region
  uniform_bucket_level_access = true

  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html" # single-page app routes
  }
  depends_on = [google_project_service.enabled]
}

# Served publicly through the load balancer's backend bucket only.
resource "google_storage_bucket_iam_member" "web_public" {
  bucket = google_storage_bucket.web.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# --- HTTPS load balancer, CDN, Cloud Armor, IAP ---------------------------------------------

resource "google_compute_security_policy" "edge" {
  count = local.deploy_edge ? 1 : 0
  name  = "${local.name}-edge"

  rule {
    priority    = 1000
    description = "OWASP CRS: SQL injection"
    action      = "deny(403)"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})"
      }
    }
  }
  rule {
    priority    = 1001
    description = "OWASP CRS: cross-site scripting"
    action      = "deny(403)"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1})"
      }
    }
  }
  rule {
    priority    = 2000
    description = "Per-IP rate limit on authentication endpoints"
    action      = "throttle"
    match {
      expr {
        expression = "request.path.startsWith('/api/auth/')"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 60
        interval_sec = 60
      }
    }
  }
  rule {
    priority    = 2100
    description = "Per-IP rate limit on the API"
    action      = "throttle"
    match {
      expr {
        expression = "request.path.startsWith('/api/')"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 1200
        interval_sec = 60
      }
    }
  }
  rule {
    priority    = 2147483647
    description = "Default allow"
    action      = "allow"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

resource "google_compute_region_network_endpoint_group" "api" {
  count                 = local.deploy_edge ? 1 : 0
  name                  = "${local.name}-api"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.api[0].name
  }
}

resource "google_compute_backend_service" "api" {
  count                 = local.deploy_edge ? 1 : 0
  name                  = "${local.name}-api"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.edge[0].id
  log_config {
    enable = true
  }
  backend {
    group = google_compute_region_network_endpoint_group.api[0].id
  }
}

# Same API, but behind IAP: platform-admin endpoints need a Google identity on the
# allowlist in addition to the app login.
resource "google_compute_backend_service" "platform" {
  count                 = local.deploy_edge ? 1 : 0
  name                  = "${local.name}-platform"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.edge[0].id
  log_config {
    enable = true
  }
  backend {
    group = google_compute_region_network_endpoint_group.api[0].id
  }
  iap {
    enabled = true
  }
}

resource "google_iap_web_backend_service_iam_member" "platform_admins" {
  for_each            = local.deploy_edge ? toset(var.platform_admin_google_accounts) : toset([])
  web_backend_service = google_compute_backend_service.platform[0].name
  role                = "roles/iap.httpsResourceAccessor"
  member              = each.value
}

resource "google_compute_backend_bucket" "web" {
  count       = local.deploy_edge ? 1 : 0
  name        = "${local.name}-web"
  bucket_name = google_storage_bucket.web.name
  enable_cdn  = true
}

resource "google_compute_url_map" "https" {
  count           = local.deploy_edge ? 1 : 0
  name            = "${local.name}-https"
  default_service = google_compute_backend_bucket.web[0].id

  host_rule {
    hosts        = [var.domain]
    path_matcher = "app"
  }
  path_matcher {
    name            = "app"
    default_service = google_compute_backend_bucket.web[0].id
    path_rule {
      paths   = ["/api/platform", "/api/platform/*"]
      service = google_compute_backend_service.platform[0].id
    }
    path_rule {
      paths   = ["/api", "/api/*"]
      service = google_compute_backend_service.api[0].id
    }
  }
}

resource "google_compute_managed_ssl_certificate" "app" {
  count = local.deploy_edge ? 1 : 0
  name  = "${local.name}-app"
  managed {
    domains = [var.domain]
  }
}

resource "google_compute_ssl_policy" "modern" {
  count           = local.deploy_edge ? 1 : 0
  name            = "${local.name}-tls"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_target_https_proxy" "app" {
  count            = local.deploy_edge ? 1 : 0
  name             = "${local.name}-https"
  url_map          = google_compute_url_map.https[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.app[0].id]
  ssl_policy       = google_compute_ssl_policy.modern[0].id
}

resource "google_compute_global_address" "app" {
  count = local.deploy_edge ? 1 : 0
  name  = "${local.name}-app"
}

resource "google_compute_global_forwarding_rule" "https" {
  count                 = local.deploy_edge ? 1 : 0
  name                  = "${local.name}-https"
  target                = google_compute_target_https_proxy.app[0].id
  port_range            = "443"
  ip_address            = google_compute_global_address.app[0].address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# HTTP → HTTPS redirect.
resource "google_compute_url_map" "redirect" {
  count = local.deploy_edge ? 1 : 0
  name  = "${local.name}-redirect"
  default_url_redirect {
    https_redirect = true
    strip_query    = false
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  count   = local.deploy_edge ? 1 : 0
  name    = "${local.name}-redirect"
  url_map = google_compute_url_map.redirect[0].id
}

resource "google_compute_global_forwarding_rule" "http" {
  count                 = local.deploy_edge ? 1 : 0
  name                  = "${local.name}-http"
  target                = google_compute_target_http_proxy.redirect[0].id
  port_range            = "80"
  ip_address            = google_compute_global_address.app[0].address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# --- GitHub Actions deploys via Workload Identity Federation (no key files) -------------

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "${local.name}-github"
  depends_on                = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Only this repository's main branch may deploy.
  attribute_condition = "assertion.repository == '${var.github_repository}' && assertion.ref == 'refs/heads/main'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "deployer" {
  for_each = toset([
    "roles/run.developer",
    "roles/artifactregistry.writer",
    "roles/cloudbuild.builds.editor",
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.deployer.member
}

resource "google_storage_bucket_iam_member" "deployer_web" {
  bucket = google_storage_bucket.web.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.deployer.member
}

# The deployer deploys revisions that run as the runtime accounts.
resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each = {
    api      = google_service_account.api.name
    worker   = google_service_account.worker.name
    migrator = google_service_account.migrator.name
  }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}
