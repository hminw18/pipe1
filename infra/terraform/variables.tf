variable "project_id" {
  description = "GCP project to deploy into."
  type        = string
}

variable "environment" {
  description = "Environment name used in resource names (dev, prod)."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "region" {
  description = "Region for all regional resources. Seoul keeps data in Korea."
  type        = string
  default     = "asia-northeast3"
}

variable "app_origins" {
  description = "Origins allowed to upload to and play from the media bucket (e.g. https://app.example.com). Empty until a domain is chosen."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for o in var.app_origins : startswith(o, "https://")])
    error_message = "app_origins must be https:// origins."
  }
}

variable "db_tier" {
  description = "Cloud SQL machine tier. Shared-core tiers are fine for dev only."
  type        = string
  default     = "db-f1-micro"
}

variable "db_high_availability" {
  description = "Regional (HA) Cloud SQL instance. Enable for prod."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Protect the database from terraform destroy. Keep true outside throwaway tests."
  type        = bool
  default     = true
}
