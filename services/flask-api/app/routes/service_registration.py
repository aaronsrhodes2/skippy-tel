"""
SkippyTel dynamic service registration — POST/DELETE /services/...

Allows external services (Mac Bilby, future daemons) to self-register at
startup rather than being hardcoded in services.py. Phone picks them up on
the next 5-minute GET /services poll with no config changes on either side.

Protocol (from SKIPPYTEL_BRIEF.md §5):

  POST   /services/register          — register or re-register
  POST   /services/heartbeat/<id>    — keep-alive (every 60s)
  DELETE /services/<id>              — clean deregister

All endpoints require:
  X-Skippy-Token: <SKIPPY_REGISTRATION_TOKEN from .env>

Registration body:
  {
    "id":           "bilby",
    "name":         "Bilby",
    "tagline":      "...",
    "base_url":     "http://skippy-mac:7334",
    "display":      { "mode": "overlay", "hud_zone": "topcenter" },
    "voice_triggers": ["what's playing", ...],
    "endpoints":    { "status": "/bilby/status", "next": "/bilby/next" },
    "ttl_seconds":  90
  }

Semantics:
  - Registrations persisted to state/registered_services.json — survive
    container restarts.
  - Miss two consecutive heartbeats → available: false auto-set.
  - GET /services merges static (services.py) + dynamic (this module).
    Dynamic registration wins on id collision.
  - SKIPPY_REGISTRATION_TOKEN env var. Tailscale is the outer auth layer;
    this token is tamper-proofing only.

Quarantine holds: this module does not import from zPhone / SkippyDroid /
SkippyGlassesMac or any DJ project.
"""

import json
import os
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

bp = Blueprint("service_registration", __name__)

# ── Storage ───────────────────────────────────────────────────────────────────

_STATE_DIR  = Path("/app/state")
_STATE_FILE = _STATE_DIR / "registered_services.json"
_lock = threading.Lock()


def _load() -> dict:
    """Return {id: registration_record} from disk. Caller must hold _lock."""
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(records: dict) -> None:
    """Persist registration records to disk. Caller must hold _lock."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(records, indent=2))


# ── Auth ──────────────────────────────────────────────────────────────────────

def _token() -> str:
    return os.getenv("SKIPPY_REGISTRATION_TOKEN", "")


def _authorized(req) -> bool:
    token = _token()
    if not token:
        # No token configured → deny all registration requests
        return False
    return req.headers.get("X-Skippy-Token", "") == token


# ── Public API (consumed by GET /services in services.py) ─────────────────────

def get_dynamic_services() -> list[dict]:
    """
    Return all registered services as live ServiceManifest dicts, merged
    into the static manifest by services.py.

    Availability is determined by the heartbeat state:
      - If last_heartbeat_at is within 2× ttl_seconds from now → available: true
      - Otherwise → available: false  (missed heartbeats)
    """
    now = time.time()
    with _lock:
        records = _load()

    result = []
    for sid, rec in records.items():
        ttl     = rec.get("ttl_seconds", 90)
        last_hb = rec.get("last_heartbeat_at", rec.get("registered_at", 0))
        alive   = (now - last_hb) < (ttl * 2)

        manifest = {
            "id":            sid,
            "name":          rec.get("name", sid),
            "tagline":       rec.get("tagline", ""),
            "available":     alive,
            "status_detail": "heartbeat OK" if alive else "heartbeat missed — service offline",
            "base_url":      rec.get("base_url"),
            "display":       rec.get("display", {
                "mode": "background", "hud_zone": None, "companion_app": None
            }),
            "voice_triggers": rec.get("voice_triggers", []),
            "endpoints":     rec.get("endpoints", {}),
        }
        result.append(manifest)

    return result


# ── Endpoints ─────────────────────────────────────────────────────────────────

@bp.post("/services/register")
def register_service():
    """
    Register or re-register an external service.

    Required header: X-Skippy-Token
    Body: JSON registration object (see module docstring).
    Returns: 204 on success, 400 on bad body, 401 on bad token, 503 if
             SKIPPY_REGISTRATION_TOKEN is not configured.
    """
    if not _token():
        return jsonify({
            "error": "Registration is disabled — SKIPPY_REGISTRATION_TOKEN not set in .env",
            "spec_version": "1",
        }), 503

    if not _authorized(request):
        return jsonify({"error": "Unauthorized", "spec_version": "1"}), 401

    body = request.get_json(silent=True)
    if not body or "id" not in body:
        return jsonify({
            "error": "Request body must be JSON with at least an 'id' field",
            "spec_version": "1",
        }), 400

    sid = str(body["id"]).strip().lower()
    if not sid:
        return jsonify({"error": "'id' must be a non-empty string", "spec_version": "1"}), 400

    now = time.time()
    record = {
        "id":               sid,
        "name":             body.get("name", sid),
        "tagline":          body.get("tagline", ""),
        "base_url":         body.get("base_url"),
        "display":          body.get("display", {"mode": "background", "hud_zone": None, "companion_app": None}),
        "voice_triggers":   body.get("voice_triggers", []),
        "endpoints":        body.get("endpoints", {}),
        "ttl_seconds":      int(body.get("ttl_seconds", 90)),
        "registered_at":    now,
        "last_heartbeat_at": now,
    }

    with _lock:
        records = _load()
        records[sid] = record
        _save(records)

    return "", 204


@bp.post("/services/heartbeat/<service_id>")
def heartbeat(service_id: str):
    """
    Keep-alive for a registered service. Call every 60s.

    Missing two consecutive heartbeats (> 2× ttl_seconds since last heartbeat)
    causes available to flip false on the next GET /services poll.

    Returns: 204 on success, 401 on bad token, 404 if not registered,
             503 if SKIPPY_REGISTRATION_TOKEN not set.
    """
    if not _token():
        return jsonify({"error": "Registration disabled", "spec_version": "1"}), 503

    if not _authorized(request):
        return jsonify({"error": "Unauthorized", "spec_version": "1"}), 401

    sid = service_id.strip().lower()
    with _lock:
        records = _load()
        if sid not in records:
            return jsonify({
                "error": f"Service '{sid}' is not registered — call /services/register first",
                "spec_version": "1",
            }), 404
        records[sid]["last_heartbeat_at"] = time.time()
        _save(records)

    return "", 204


@bp.delete("/services/<service_id>")
def deregister_service(service_id: str):
    """
    Clean deregister on SIGTERM / graceful shutdown.

    Returns: 204 on success (including if the service was already gone),
             401 on bad token, 503 if token not set.
    """
    if not _token():
        return jsonify({"error": "Registration disabled", "spec_version": "1"}), 503

    if not _authorized(request):
        return jsonify({"error": "Unauthorized", "spec_version": "1"}), 401

    sid = service_id.strip().lower()
    with _lock:
        records = _load()
        records.pop(sid, None)
        _save(records)

    return "", 204
