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

# The portal runs as skippy-agent (created earlier, holds roles/compute.admin
# for the games VM). Deploying a service that runs as it = acting as it.
data "google_service_account" "runtime" {
  account_id = "skippy-agent"
  depends_on = [google_project_service.apis]
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
  # Only workflows in this one repository can use this provider at all.
  attribute_condition = "assertion.repository == \"${var.github_repo}\""
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
