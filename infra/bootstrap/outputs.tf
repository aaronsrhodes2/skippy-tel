output "project_number" {
  value = data.google_project.this.number
}

output "workload_identity_provider" {
  description = "Paste into .github/workflows/deploy.yml (WIF_PROVIDER) — or just tell Claude."
  value       = google_iam_workload_identity_pool_provider.github.name
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

output "doomloop_workload_identity_provider" {
  description = "doom-loop's GitHub variable WIF_PROVIDER (its dev-deploy workflow authenticates with it)."
  value       = google_iam_workload_identity_pool_provider.doomloop.name
}

output "doomloop_publisher_service_account" {
  description = "doom-loop's GitHub variable WIF_SERVICE_ACCOUNT."
  value       = google_service_account.doomloop_publisher.email
}

output "doomloop_bucket" {
  value = google_storage_bucket.doomloop_dev.name
}

output "prophesy_workload_identity_provider" {
  description = "The three Shifting Prophesy repos' GitHub variable WIF_PROVIDER (shifting-prophesy-referee's setup/after-bootstrap.sh sets it)."
  value       = google_iam_workload_identity_pool_provider.prophesy.name
}

output "prophesy_publisher_service_account" {
  description = "Their GitHub variable WIF_SERVICE_ACCOUNT."
  value       = google_service_account.prophesy_publisher.email
}

output "prophesy_bucket" {
  value = google_storage_bucket.prophesy.name
}
