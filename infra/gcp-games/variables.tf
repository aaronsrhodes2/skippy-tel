terraform {
  required_version = ">= 1.6"
  # State lives in the bucket infra/bootstrap creates, so GitHub Actions can
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
