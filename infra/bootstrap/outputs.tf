output "project_number" {
  value = data.google_project.this.number
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}

output "portal_url" {
  description = "Cloud Run's deterministic URL. The OAuth redirect URI is this + /auth/callback."
  value       = "https://skippy-portal-${data.google_project.this.number}.${var.region}.run.app"
}

output "state_bucket" {
  value = google_storage_bucket.tfstate.name
}

output "doomloop_bucket" {
  value = google_storage_bucket.doomloop_dev.name
}

output "skippy_core_provider" {
  description = "skippy-core's keyless trust (core/cd/oidc.py's token audience is //iam.googleapis.com/ + this)"
  value       = google_iam_workload_identity_pool_provider.skippy_core.name
}

output "git_backup_bucket" {
  description = "Where skippy-core puts the nightly git bundles (docs/no-github.md)"
  value       = google_storage_bucket.git_backup.name
}

output "game_assets_bucket" {
  description = "Where skippy-core puts the Garry's Mod addon for the games VM (infra/gcp-games/gmod_addon.sh reads it)"
  value       = google_storage_bucket.game_assets.name
}
