terraform {
  required_version = ">= 1.6"
  # State lives in the bucket infra/bootstrap creates, so skippy-core's deployer can
  # apply this on every push. Bucket passed at init:
  #   terraform init -backend-config="bucket=skippy-tel-tfstate"
  backend "gcs" {
    prefix = "gcp-games"
  }
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

variable "project" {
  description = "GCP project id to host the game server in."
  type        = string
}

variable "region" {
  description = "Region. us-west1 (Oregon) is the cheapest region near the Bay Area; us-west2 (LA) is closer but pricier."
  type        = string
  default     = "us-west1"
}

variable "zone" {
  type    = string
  default = "us-west1-b"
}

variable "name" {
  description = "Base name for the VM, disk, IP and firewall rule."
  type        = string
  default     = "skippy-games"
}

variable "data_disk_gb" {
  description = "World data disk. Server installs live here too (Satisfactory ~10 GB, Dragonwilds ~5 GB, Valheim ~2 GB)."
  type        = number
  default     = 60
}

variable "snapshot_retention_days" {
  type    = number
  default = 7
}

variable "max_hours" {
  description = "Hard ceiling on one session: the watchdog stops the VM this many hours after boot, whatever the traffic says (0 = no ceiling). Ignored while a session's idle-minutes is 0 (never sleep)."
  type        = number
  default     = 8
}

variable "billing_account" {
  description = "Billing account id (XXXXXX-XXXXXX-XXXXXX) to create the monthly budget alert on. Empty = no budget (nothing is created). The deployer service account needs billing.budgets permission on the account (see docs/cicd.md)."
  type        = string
  default     = ""
}

variable "budget_monthly_usd" {
  description = "Monthly amount in USD the budget alerts against (email at 50%, 90% and 100% of it)."
  type        = number
  default     = 30
}

variable "budget_alert_email" {
  description = "Also email this address at each threshold (through a Cloud Monitoring email channel). Empty = only the billing account's own admins and users are emailed, which is the budget's default."
  type        = string
  default     = ""
}
