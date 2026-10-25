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

variable "prophesy_repos" {
  description = "owner/repo of each Shifting Prophesy repository whose GitHub Actions may upload to the experiment's bucket: the two builds and the referee's record."
  type        = list(string)
  default     = ["aaronsrhodes2/shifting-prophesy-haiku", "aaronsrhodes2/shifting-prophesy-purple", "aaronsrhodes2/shifting-prophesy-referee"]
}

variable "prophesy_bucket" {
  description = "GCS bucket holding the Shifting Prophesy experiment: each build's newest site and the turn record (portal/prophesy.py reads it; deploy.yml sets PROPHESY_BUCKET to the same name)."
  type        = string
  default     = "skippy-tel-shifting-prophesy-dev"
}

variable "mentat_research_bucket" {
  description = "Private GCS bucket keeping every request to the public Mentat and its whole answer, forever (mentat/research.py; deploy.yml sets MENTAT_RESEARCH_BUCKET to the same name)."
  type        = string
  default     = "skippy-tel-mentat-research"
}

variable "tabletop_bucket" {
  description = "Private GCS bucket holding the portal's Tabletop boards and the pictures pinned on them (portal/tabletop.py; deploy.yml sets TABLETOP_BUCKET to the same name)."
  type        = string
  default     = "skippy-tel-tabletop"
}
