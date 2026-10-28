output "sql_instance_connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "sql_private_ip" {
  value = google_sql_database_instance.main.private_ip_address
}

output "media_bucket" {
  value = google_storage_bucket.media.name
}

output "artifact_registry_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "api_service_account" {
  value = google_service_account.api.email
}

output "worker_service_account" {
  value = google_service_account.worker.email
}

output "totp_kms_key" {
  value = google_kms_crypto_key.totp.id
}

# Repository variables for .github/workflows/deploy.yml.
output "wif_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}

output "web_bucket" {
  value = google_storage_bucket.web.name
}

output "load_balancer_ip" {
  description = "Point the domain's A record here."
  value       = local.deploy_edge ? google_compute_global_address.app[0].address : null
}
