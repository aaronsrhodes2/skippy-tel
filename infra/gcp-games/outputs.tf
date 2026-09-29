output "ip" {
  description = "Static IP players connect to."
  value       = google_compute_address.games.address
}

output "instance" {
  value = google_compute_instance.games.name
}

output "zone" {
  value = google_compute_instance.games.zone
}
