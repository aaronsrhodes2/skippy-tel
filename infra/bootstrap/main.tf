# One-time bootstrap: everything CI/CD needs to deploy without anyone
# typing commands afterwards. Run once from Cloud Shell via bootstrap.sh.
#
#   • APIs
#   • a GCS bucket for Terraform state (so CI can run `terraform apply`)
#   • an Artifact Registry repo for the portal image
#   • skippy-deployer: the service account GitHub Actions acts as
#   • Workload Identity Federation: GitHub's OIDC tokens for THIS repo can
#     impersonate skippy-deployer. No key file exists anywhere.
#   • Secret Manager secrets the portal reads at runtime
#
# State for this module stays local in Cloud Shell (~/skippy-tel/infra/bootstrap);
# it changes rarely and holds the generated Flask session key.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

data "google_project" "this" {}

locals {
  apis = [
    "artifactregistry", "cloudresourcemanager", "compute", "iam", "iamcredentials",
    "run", "secretmanager", "serviceusage", "storage", "sts",
  ]
  deployer_roles = [
    "roles/run.admin",                      # deploy the portal
    "roles/artifactregistry.writer",        # push the portal image
    "roles/compute.admin",                  # terraform apply infra/gcp-games
    "roles/serviceusage.serviceUsageAdmin", # google_project_service in infra/gcp-games
  ]
  secrets = [
    "portal-flask-secret",     # generated here
    "portal-google-client-id", # from the OAuth client you create (bootstrap.sh asks)
    "portal-google-client-secret",
    "anthropic-api-key", # optional: enables Skippy chat
    "discord-bot-token", # optional: enables Discord setup from the admin page
    # NOT here: github-actions-token (the admin page's recovery buttons). It was created with gcloud on 2026-09-30
    # (docs/SECRETS.md) because Cloud Shell had no GitHub credential to pull this file; adding it here now would make
    # the next apply fail with "already exists" (terraform import it first if you ever move it in).
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = "${each.key}.googleapis.com"
  disable_on_destroy = false
}

# ── Terraform state for CI ────────────────────────────────────────────────────

resource "google_storage_bucket" "tfstate" {
  name                        = "${var.project}-tfstate"
  location                    = "US"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
  depends_on = [google_project_service.apis]
}

# ── Portal image registry ─────────────────────────────────────────────────────

resource "google_artifact_registry_repository" "skippy" {
  location      = var.region
  repository_id = "skippy"
  format        = "DOCKER"
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
  depends_on = [google_project_service.apis]
}

# ── The deployer identity GitHub Actions uses ─────────────────────────────────

resource "google_service_account" "deployer" {
  account_id   = "skippy-deployer"
  display_name = "GitHub Actions deployer (skippy-tel)"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(local.deployer_roles)
  project  = var.project
  role     = each.key
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_storage_bucket_iam_member" "deployer_state" {
  bucket = google_storage_bucket.tfstate.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deployer.email}"
}

# The portal runs as skippy-agent (created earlier; now holds only the
# portal_vm role below). Deploying a service that runs as it = acting as it.
data "google_service_account" "runtime" {
  account_id = "skippy-agent"
  depends_on = [google_project_service.apis]
}

# What the portal actually does to the games VM (portal/gcp.py): read it and its
# serial logs, write its metadata, resize, start and stop it, and wait on those
# operations. Replaces roles/compute.admin, which also let it create, delete and
# reconfigure anything in the project. bootstrap.sh removes compute.admin once
# this is in place.
resource "google_project_iam_custom_role" "portal_vm" {
  role_id     = "skippyPortalVm"
  title       = "Skippy portal: operate the games VM"
  description = "Start/stop/resize the games VM, read its logs, write its metadata."
  permissions = [
    "compute.instances.get",
    "compute.instances.getSerialPortOutput",
    "compute.instances.setMetadata",
    "compute.instances.setMachineType",
    "compute.instances.start",
    "compute.instances.stop",
    "compute.machineTypes.get",
    "compute.zoneOperations.get",
  ]
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "runtime_operates_vm" {
  project = var.project
  role    = google_project_iam_custom_role.portal_vm.id
  member  = "serviceAccount:${data.google_service_account.runtime.email}"
}

resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  service_account_id = data.google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# Mentat (sigma-ground's physics tools) is a PUBLIC service that evaluates
# user-supplied maths (sympy), so it runs as an identity with no roles at all:
# code that escapes a tool can't touch the game VM, secrets or anything else.
resource "google_service_account" "mentat" {
  account_id   = "skippy-mentat"
  display_name = "Mentat public physics service (no permissions)"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account_iam_member" "deployer_acts_as_mentat" {
  service_account_id = google_service_account.mentat.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# ── Workload Identity Federation: GitHub → skippy-deployer ────────────────────

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Only workflows in this one repository, running from main, can use this
  # provider at all: a workflow pushed on any other branch gets nothing.
  attribute_condition = "assertion.repository == \"${var.github_repo}\" && assertion.ref == \"refs/heads/main\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# ── Runtime secrets for the portal ────────────────────────────────────────────

resource "google_secret_manager_secret" "portal" {
  for_each  = toset(local.secrets)
  secret_id = each.key
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "random_password" "flask" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret_version" "flask" {
  secret      = google_secret_manager_secret.portal["portal-flask-secret"].id
  secret_data = random_password.flask.result
}

resource "google_secret_manager_secret_iam_member" "runtime_reads" {
  for_each  = google_secret_manager_secret.portal
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_service_account.runtime.email}"
}

# ── Doom Loop dev channel (the Captain's work-in-progress browser game) ───────
#
# doom-loop's CI uploads each build of the game here; the portal (runtime SA) reads it at request time, so a new build
# shows up at /doomloop/ with no portal redeploy (portal/doomloop.py). The uploader is NOT skippy-deployer: that identity
# holds run.admin and compute.admin, so widening ITS trust to another repo would hand those roles to that repo's CI.
# doomloop-publisher can only write objects in this one bucket, and a second provider in the same pool trusts only
# doom-loop's main branch. skippy-deployer's own trust above is untouched.

resource "google_storage_bucket" "doomloop_dev" {
  name                        = var.doomloop_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true # dev builds only; the game's source of truth is its repo
  lifecycle_rule {
    condition { age = 30 }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.apis]
}

resource "google_service_account" "doomloop_publisher" {
  account_id   = "doomloop-publisher"
  display_name = "doom-loop CI: uploads dev builds of the game (one bucket, nothing else)"
  depends_on   = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "doomloop_publisher_writes" {
  bucket = google_storage_bucket.doomloop_dev.name
  role   = "roles/storage.objectAdmin" # overwrite and delete the previous build's files, in this bucket only
  member = "serviceAccount:${google_service_account.doomloop_publisher.email}"
}

resource "google_storage_bucket_iam_member" "portal_reads_doomloop" {
  bucket = google_storage_bucket.doomloop_dev.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${data.google_service_account.runtime.email}"
}

# ── The Tabletop (the portal's shared RPG boards, /tabletop) ─────────────────
#
# The boards and the players' pictures live here and nowhere else (Cloud Run's disk is gone on every restart). Only the
# portal's runtime identity can read or write it; the portal serves the pictures to people holding the "tabletop" grant.
# Versioning keeps a deleted or overwritten file for 30 days, so a card taken down by mistake can still be fetched back.

resource "google_storage_bucket" "tabletop" {
  name                        = var.tabletop_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false # the campaign's scrapbook: never deleted by a terraform destroy
  versioning { enabled = true }
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
      with_state                 = "ARCHIVED"
    }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "portal_uses_tabletop" {
  bucket = google_storage_bucket.tabletop.name
  role   = "roles/storage.objectAdmin" # read, write and delete the boards' files, in this bucket only
  member = "serviceAccount:${data.google_service_account.runtime.email}"
}

resource "google_iam_workload_identity_pool_provider" "doomloop" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "doomloop"
  display_name                       = "GitHub OIDC (doom-loop)"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Only workflows in doom-loop, running from main.
  attribute_condition = "assertion.repository == \"${var.doomloop_repo}\" && assertion.ref == \"refs/heads/main\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "doomloop_impersonates_publisher" {
  service_account_id = google_service_account.doomloop_publisher.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.doomloop_repo}"
}
