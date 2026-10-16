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

variable "doomloop_repo" {
  description = "owner/repo whose GitHub Actions may upload Doom Loop dev builds."
  type        = string
  default     = "aaronsrhodes2/doom-loop"
}

variable "doomloop_bucket" {
  description = "GCS bucket holding the newest Doom Loop dev build (portal/doomloop.py reads it; deploy.yml sets DOOMLOOP_BUCKET to the same name)."
  type        = string
  default     = "skippy-tel-doomloop-dev"
}

variable "tabletop_bucket" {
  description = "Private GCS bucket holding the portal's Tabletop boards and the pictures pinned on them (portal/tabletop.py; deploy.yml sets TABLETOP_BUCKET to the same name)."
  type        = string
  default     = "skippy-tel-tabletop"
}
