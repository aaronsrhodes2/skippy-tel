"""
UPS routes — CyberPower ABMT1500 status via host-side monitor.

The UPS monitor runs on the Windows HOST at port 9338 (outside Docker).
These routes proxy to it via host.docker.internal.

GET  /ups/status   → current UPS state (charge, runtime, on_ac, alert)
POST /ups/alert    → called by the monitor when power state changes;
                     pushes a SYSTEM notification to connected phones
                     (via a shared in-memory alert queue the SSE stream
                     can consume in a future phase)
"""

import logging
from datetime import datetime, timezone

import httpx
from flask import Blueprint, jsonify, request

from app.config import settings

log = logging.getLogger(__name__)
bp  = Blueprint("ups", __name__)

# Host-side monitor URL — reachable from inside Docker via host.docker.internal
UPS_MONITOR_URL = "http://host.docker.internal:9338"

# In-memory alert queue: latest alert for the /ups/status response.
# Phase 2: feed this into an SSE stream so the phone gets instant push.
_latest_alert: dict = {}


# ── GET /ups/status ───────────────────────────────────────────────────────────

@bp.get("/ups/status")
def ups_status():
    """Proxy the host-side UPS monitor and return status JSON."""
    try:
        resp = httpx.get(f"{UPS_MONITOR_URL}/status", timeout=3.0)
        data = resp.json()
    except Exception as exc:
        log.warning("UPS monitor unreachable: %s", exc)
        data = {"error": "ups_monitor_offline", "detail": str(exc)}

    # Merge in any pending alert so the phone gets context.
    if _latest_alert:
        data["last_alert"] = _latest_alert

    return jsonify(data)


# ── POST /ups/alert ───────────────────────────────────────────────────────────

@bp.post("/ups/alert")
def ups_alert():
    """
    Receive a power-state change push from the host monitor.
    Logs it and stores it for the next /ups/status poll.
    Phase 2: fan this out to SSE / WebSocket subscribers.
    """
    global _latest_alert
    body = request.get_json(silent=True) or {}
    _latest_alert = {**body, "received_at": datetime.now(timezone.utc).isoformat()}

    alert  = body.get("alert", "unknown")
    charge = body.get("charge", "?")
    on_ac  = body.get("on_ac", True)

    if not on_ac:
        log.warning("⚡ UPS ALERT: %s — %s%% charge, %s min runtime",
                    alert, charge, body.get("runtime", "?"))
    else:
        log.info("UPS restored to AC power — %s%% charge", charge)

    return jsonify({"ok": True})
