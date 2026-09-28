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
