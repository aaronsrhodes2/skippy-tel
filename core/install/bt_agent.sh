#!/usr/bin/env bash
# A Bluetooth pairing agent that is always running on skippy-core (2026-11-01, the bedroom TV's Xbox controller).
# Without one, a bonded device that reconnects (the controller waking up) is refused: bluetoothd logs "No agent available for request
# type 2" / "device_confirm_passkey: Operation not permitted", and the controller just blinks. bt-agent (bluez-tools) with
# NoInputNoOutput answers those requests itself, so a bonded, trusted device reconnects on its own. Idempotent; run with sudo.
#   sudo bash core/install/bt_agent.sh
set -euo pipefail
command -v bt-agent >/dev/null || { apt-get update -qq && apt-get install -y -qq bluez-tools; }
cat > /etc/systemd/system/skippy-bt-agent.service <<'UNIT'
[Unit]
Description=Bluetooth pairing agent (NoInputNoOutput): bonded devices like the Xbox controller reconnect by themselves
After=bluetooth.service
Requires=bluetooth.service

[Service]
ExecStart=/usr/bin/bt-agent --capability=NoInputNoOutput
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now skippy-bt-agent.service
systemctl is-active skippy-bt-agent.service
