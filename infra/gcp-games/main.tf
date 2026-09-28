# Pop-up game server on GCP.
#
# One VM that is normally STOPPED (you pay only for disk + reserved IP).
# `scripts/games.sh up valheim dragonwilds` resizes it to fit the games,
# starts it, and the VM launches those games from games/docker-compose.yml.
# An idle watchdog on the VM saves the worlds and powers it off when nobody
# has played for `idle-minutes` (default 30).
#
# World data lives on a separate persistent disk with daily snapshots, so
# the VM (and even its boot disk) is disposable.

provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone
}

resource "google_project_service" "compute" {
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

# ── Network ───────────────────────────────────────────────────────────────────

resource "google_compute_address" "games" {
  name       = "${var.name}-ip"
  depends_on = [google_project_service.compute]
}

resource "google_compute_firewall" "games" {
  name          = "${var.name}-game-ports"
  network       = "default"
  target_tags   = [var.name]
  source_ranges = ["0.0.0.0/0"]

  allow {
    protocol = "udp"
    ports = [
      "2456-2457", # Valheim
      "34197",     # Factorio
      "7777",      # Satisfactory
      "7778",      # RuneScape: Dragonwilds
    ]
  }
  allow {
    protocol = "tcp"
    ports    = ["7777", "8888"] # Satisfactory API + reliable messaging
  }
  depends_on = [google_project_service.compute]
}

# ── World data disk (the only thing that matters) ─────────────────────────────

resource "google_compute_disk" "data" {
  name = "${var.name}-data"
  type = "pd-balanced"
  size = var.data_disk_gb

  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.compute]
}

resource "google_compute_resource_policy" "daily_snapshot" {
  name = "${var.name}-daily-snapshot"

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "10:00" # UTC ≈ 3am Pacific
      }
    }
    retention_policy {
      max_retention_days    = var.snapshot_retention_days
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
  }
}

resource "google_compute_disk_resource_policy_attachment" "data" {
  name = google_compute_resource_policy.daily_snapshot.name
  disk = google_compute_disk.data.name
}

# ── The VM ────────────────────────────────────────────────────────────────────

resource "google_compute_instance" "games" {
  name         = var.name
  machine_type = "e2-standard-4" # games.sh resizes per session
  tags         = [var.name]

  # Created stopped; games.sh starts it.
  desired_status            = "TERMINATED"
  allow_stopping_for_update = true

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.data.id
    device_name = "games-data"
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.games.address
    }
  }

  metadata = {
    startup-script  = file("${path.module}/startup.sh")
    shutdown-script = file("${path.module}/shutdown.sh")
    idle-watchdog   = file("${path.module}/idle-watchdog.sh")
    discord-notify  = file("${path.module}/discord-notify.py")
    compose-file    = file("${path.module}/../../games/docker-compose.yml")
    games           = "valheim"
    idle-minutes    = "30"
    # games-env (the secrets in games/.env) is uploaded by `games.sh env`,
    # never through OpenTofu, so passwords stay out of state files.
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
  }

  lifecycle {
    ignore_changes = [
      desired_status, # games.sh starts/stops it
      machine_type,   # games.sh sizes it per session
      metadata["games"],
      metadata["idle-minutes"],
      metadata["games-env"],
    ]
  }
}
