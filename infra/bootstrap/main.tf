# One-time bootstrap: everything CI/CD needs to deploy without anyone
# typing commands afterwards. Run once from Cloud Shell via bootstrap.sh.
#
#   • APIs
#   • a GCS bucket for Terraform state (so CI can run `terraform apply`)
#   • an Artifact Registry repo for the portal image
#   • skippy-deployer: the service account skippy-core deploys as (core/cd)
#   • Workload Identity Federation: skippy-core's own signed tokens can
#     impersonate skippy-deployer. No key file exists anywhere. (GitHub is
#     not trusted any more: docs/no-github.md.)
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

# ── The deployer identity skippy-core uses ────────────────────────────────────

resource "google_service_account" "deployer" {
  account_id   = "skippy-deployer"
  display_name = "skippy-core deployer (skippy-tel)"
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

# ── Mentat's research data (the Captain, 2026-10-23: "log everything mentat outputs, it's our research data") ──
#
# The public Mentat writes every request and its whole answer here as batches of JSON lines (mentat/research.py). It may only
# CREATE objects: nothing it wrote can be overwritten or deleted by it, and nothing here is ever deleted by a lifecycle rule.
resource "google_storage_bucket" "mentat_research" {
  name                        = var.mentat_research_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false # research data: never deleted by a terraform destroy
  depends_on                  = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "mentat_writes_research" {
  bucket = google_storage_bucket.mentat_research.name
  role   = "roles/storage.objectCreator" # create only: no read, overwrite or delete
  member = "serviceAccount:${google_service_account.mentat.email}"
}

# ── Workload Identity Federation: the pool (skippy-core's provider is below) ───
# The pool is still NAMED "github" (renaming a pool destroys it and every provider in it); it holds only skippy-core's provider now.
# GitHub's own provider and its binding to skippy-deployer were removed 2026-10-25 (docs/no-github.md).

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "skippy-core"
  depends_on                = [google_project_service.apis]
}

# ── skippy-core → skippy-deployer: keyless (docs/no-github.md, phase 3) ──────────────────────
# skippy-core deploys the portal, Mentat cloud and the games VM's Terraform itself. It signs a short-lived token with its own
# key (/etc/skippy-core/gcp-oidc/signing.pem, root-only on the box; never leaves it) and swaps it here for skippy-deployer's
# credentials. The project forbids service-account keys (iam.disableServiceAccountKeyCreation), and this needs none: Google
# holds only the PUBLIC half (skippy-core-jwks.json, made by core/cd/oidc.py keygen). There is no issuer URL to reach: the
# keys are uploaded with the provider. Rotating = a new key pair on the box, a new skippy-core-jwks.json, a bootstrap run.

resource "google_iam_workload_identity_pool_provider" "skippy_core" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "skippy-core"
  display_name                       = "skippy-core deployer"
  attribute_mapping = {
    "google.subject" = "assertion.sub"
  }
  # Only the deployer's own subject: a token skippy-core signs for anything else gets nothing.
  attribute_condition = "assertion.sub == \"skippy-core-deployer\""
  oidc {
    issuer_uri = "https://skippy-core.skippy-tel.invalid" # never fetched: the keys come from jwks_json
    jwks_json  = file("${path.module}/skippy-core-jwks.json")
  }
}

resource "google_service_account_iam_member" "skippy_core_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/skippy-core-deployer"
}

# The house's git, off site (docs/no-github.md): skippy-core puts a nightly `git bundle` of every repo here, as skippy-deployer.
# Versioned, so a bad night never overwrites a good one; old versions go after 90 days.
resource "google_storage_bucket" "git_backup" {
  name                        = "${var.project}-git-backup"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 90
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "deployer_writes_git_backup" {
  bucket = google_storage_bucket.git_backup.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deployer.email}"
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
# skippy-core (core/cd, publish.json) builds each push of doom-loop and uploads it here; the portal (runtime SA) reads it at request time, so a new build
# shows up at /doomloop/ with no portal redeploy (portal/doomloop.py). The uploader is skippy-deployer, as skippy-core: it can write
# this one bucket and nothing GitHub-side exists any more.

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

resource "google_storage_bucket_iam_member" "deployer_writes_doomloop" {
  bucket = google_storage_bucket.doomloop_dev.name
  role   = "roles/storage.objectAdmin" # overwrite and delete the previous build's files, in this bucket only
  member = "serviceAccount:${google_service_account.deployer.email}"
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
  # Skippy's chats (portal/chat_history.py, chat-history/<one-way id>.json): a guest's expire after 30 days and the portal's
  # hourly sweep analyses each one before deleting it. This is only the backstop, far behind that: a file no one has written
  # for 60 days goes (each save writes a new object, so age is "since the last save"), and with it its old versions. The
  # Captain's file is written whenever he chats, so in practice it stays.
  lifecycle_rule {
    condition {
      age            = 60
      matches_prefix = ["chat-history/"]
    }
    action { type = "Delete" }
  }
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 1
      with_state                 = "ARCHIVED"
      matches_prefix             = ["chat-history/"]
    }
    action { type = "Delete" }
  }
  # Pictures Skippy drew (portal/pictures.py): a guest's go after 60 days, like their chats' backstop; the Captain's
  # (chat-pictures/keep/) stay. A pending job's note is gone two days on (the portal gives up on a job after 6 hours).
  lifecycle_rule {
    condition {
      age            = 60
      matches_prefix = ["chat-pictures/guest/"]
    }
    action { type = "Delete" }
  }
  lifecycle_rule {
    condition {
      age            = 2
      matches_prefix = ["chat-pictures/pending/"]
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

# ── What skippy-core hands the games VM (the Garry's Mod addon: gmod_addon.sh) ─────────────────────────────────────
# skippy-core (core/cd, publish.json) puts a tarball of sigma-ground's stage/gmod/addon here whenever it changes; the games VM fetches it
# at every boot and restart (infra/gcp-games/gmod_addon.sh) with its own token. The VM runs as the project's default compute account.
resource "google_storage_bucket" "game_assets" {
  name                        = "${var.project}-game-assets"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  depends_on                  = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "deployer_writes_game_assets" {
  bucket = google_storage_bucket.game_assets.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_storage_bucket_iam_member" "games_vm_reads_game_assets" {
  bucket = google_storage_bucket.game_assets.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}
