"""
UPS Monitor — host-side Windows service for SkippyTel.

Runs on the Windows HOST (not in Docker) at port 9338.
Polls the CyberPower ABMT1500 via WMI every 30 seconds and:
  - Serves GET /status  → current UPS state as JSON
  - Serves GET /health  → liveness probe
  - POSTs alerts to SkippyTel when power state changes (AC → battery, low, etc.)

SkippyTel's flask-api queries http://host.docker.internal:9338/status.

Usage:
    python monitor.py
    python monitor.py --port 9338 --skippy-url http://localhost:3003

No extra dependencies beyond stdlib + flask.
    pip install flask requests
"""

import argparse
import json
import logging
import subprocess
import threading
import time
from datetime import datetime, timezone

import flask
import requests

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_PORT       = 9338
DEFAULT_SKIPPY_URL = "http://localhost:3003"
POLL_INTERVAL      = 30   # seconds between WMI polls
LOW_BATTERY_PCT    = 25   # warn below this
CRITICAL_BATTERY_PCT = 10

# Win32_Battery.BatteryStatus codes
BATTERY_STATUS_NAMES = {
    1:  "discharging",      # on battery power
    2:  "ac",               # plugged in
    3:  "fully_charged",
    4:  "low",
    5:  "critical",
    6:  "charging",
    7:  "charging_high",
    8:  "charging_low",
    9:  "charging_critical",
    10: "unknown",
    11: "partially_charged",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ups-monitor] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
_lock         = threading.Lock()
_last_status  = {}           # most recent WMI result
_prev_ac      = True         # True = on AC; False = on battery
_skippy_url   = DEFAULT_SKIPPY_URL


# ── WMI poll ──────────────────────────────────────────────────────────────────

def _query_wmi() -> dict:
    """Call PowerShell to read Win32_Battery via WMI. Returns dict or raises."""
    ps = (
        "Get-WmiObject -Class Win32_Battery | "
        "Select-Object Name, BatteryStatus, EstimatedChargeRemaining, EstimatedRunTime | "
        "ConvertTo-Json"
    )
    result = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"WMI query failed: {result.stderr.strip()}")
    raw = json.loads(result.stdout.strip())
    # If multiple batteries, WMI returns a list; take first.
    if isinstance(raw, list):
        raw = raw[0]
    return raw


def _build_status(raw: dict) -> dict:
    code     = raw.get("BatteryStatus", 0)
    charge   = raw.get("EstimatedChargeRemaining", -1)
    runtime  = raw.get("EstimatedRunTime", -1)
    name     = raw.get("Name", "unknown")
    status   = BATTERY_STATUS_NAMES.get(code, f"unknown({code})")
    on_ac    = code not in (1, 4, 5)   # discharging / low / critical = on battery

    alert = None
    if not on_ac:
        if charge <= CRITICAL_BATTERY_PCT:
            alert = "critical"
        elif charge <= LOW_BATTERY_PCT:
            alert = "low"
        else:
            alert = "on_battery"

    return {
        "name":     name,
        "status":   status,
        "on_ac":    on_ac,
        "charge":   charge,        # percent
        "runtime":  runtime,       # minutes
        "alert":    alert,         # None | "on_battery" | "low" | "critical"
        "ts":       datetime.now(timezone.utc).isoformat(),
    }


# ── Alert push ────────────────────────────────────────────────────────────────

def _push_alert(status: dict):
    """POST a UPS alert to SkippyTel so it can surface a SYSTEM bubble on the phone."""
    try:
        requests.post(
            f"{_skippy_url}/ups/alert",
            json=status,
            timeout=5,
        )
        log.info("Alert pushed to SkippyTel: %s", status["alert"])
    except Exception as exc:
        log.warning("Could not push alert to SkippyTel: %s", exc)


# ── Background poller ─────────────────────────────────────────────────────────

def _poll_loop():
    global _prev_ac
    while True:
        try:
            raw    = _query_wmi()
            status = _build_status(raw)
            with _lock:
                _last_status.clear()
                _last_status.update(status)

            on_ac = status["on_ac"]
            if on_ac != _prev_ac:
                log.info(
                    "Power state changed: %s → %s (charge=%s%%)",
                    "AC" if _prev_ac else "battery",
                    "AC" if on_ac else "battery",
                    status["charge"],
                )
                _push_alert(status)
                _prev_ac = on_ac
            elif status["alert"] in ("low", "critical"):
                log.warning("UPS alert: %s at %s%%", status["alert"], status["charge"])
                _push_alert(status)

        except Exception as exc:
            log.error("WMI poll error: %s", exc)
            with _lock:
                _last_status.update({"error": str(exc), "ts": datetime.now(timezone.utc).isoformat()})

        time.sleep(POLL_INTERVAL)


# ── Flask app ─────────────────────────────────────────────────────────────────

app = flask.Flask(__name__)


@app.route("/status")
def status():
    with _lock:
        return flask.jsonify(dict(_last_status))


@app.route("/health")
def health():
    with _lock:
        ok = "error" not in _last_status and bool(_last_status)
    return flask.jsonify({"status": "ok" if ok else "degraded"}), 200


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SkippyTel UPS monitor")
    parser.add_argument("--port",       type=int, default=DEFAULT_PORT)
    parser.add_argument("--skippy-url", default=DEFAULT_SKIPPY_URL)
    args = parser.parse_args()

    _skippy_url = args.skippy_url

    # Do one immediate poll before starting HTTP server so /status is never empty.
    try:
        raw    = _query_wmi()
        status = _build_status(raw)
        _last_status.update(status)
        log.info("UPS: %s — %s%% charge, %s min runtime",
                 status["status"], status["charge"], status["runtime"])
    except Exception as exc:
        log.error("Initial WMI poll failed: %s", exc)
        _last_status["error"] = str(exc)

    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()

    log.info("UPS monitor listening on port %s", args.port)
    app.run(host="0.0.0.0", port=args.port, debug=False)
