variable "project" {
  type    = string
  default = "skippy-tel"
}

variable "region" {
  type    = string
  default = "us-west1"
}

variable "github_repo" {
  description = "owner/repo whose GitHub Actions may deploy."
  type        = string
  default     = "aaronsrhodes2/skippy-tel"
}
