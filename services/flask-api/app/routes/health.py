"""GET /health — sub-100ms liveness check.

Phone polls this every 10s (TransportLayer.kt:91). Latency drives the
RightBar SignalStack: <50ms = 4 bars, <150ms = 3, <400ms = 2,
reachable = 1, unreachable = 0. Keep this hot.

Model availability policy — no API calls that cost money:
  - Ollama: GET /api/tags (free local Docker call) confirms the daemon is
    running and which models are loaded.
  - Paid tiers (Anthropic, Gemini, ElevenLabs): key-present check only.
    A configured key means the tier CAN be invoked; it does not ping the
    remote API. We never send inference requests from health checks.

Includes a non-blocking UPS snapshot if the host monitor is reachable.
"""
import json
import datetime
from pathlib import Path

import httpx
from flask import Blueprint
from app.version import envelope

bp = Blueprint("health", __name__)

UPS_MONITOR_URL  = "http://host.docker.internal:9338"
CLAUDE_BRIDGE_URL = "http://host.docker.internal:9337"
SNAPSHOT_PATH    = Path("/drive-state/health_snapshot.json")


def _ups_snapshot() -> dict | None:
    """Try to get UPS status; return None if monitor is offline."""
    try:
        resp = httpx.get(f"{UPS_MONITOR_URL}/status", timeout=1.0)
        data = resp.json()
        return {
            "on_ac":  data.get("on_ac"),
            "charge": data.get("charge"),
            "runtime": data.get("runtime"),
            "alert":  data.get("alert"),
        }
    except Exception:
        return None


def _ollama_status() -> dict:
    """GET /api/tags — free local call, confirms Ollama is up and loaded."""
    from app.config import settings  # noqa: PLC0415
    try:
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
        data = r.json()
        models = [m["name"] for m in data.get("models", [])]
        return {"available": True, "models": models}
    except Exception:
        return {"available": False, "models": []}


def _write_snapshot(payload: dict) -> None:
    """Overwrite the last-known health state on every poll.

    The file always reflects the most recent /health result, whether ok or
    degraded. On degraded, model_watchdog already appends to model_health.log;
    this gives a quick `cat health_snapshot.json` view of the last tick.
    """
    try:
        snapshot = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            **payload,
        }
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2))
    except Exception:
        pass


def _bridge_status() -> bool:
    """Quick liveness ping for the host-side Claude Code bridge (port 9337).
    Returns True if the bridge is reachable and healthy, False otherwise.
    Uses a tight 0.5s timeout — health should never block the phone's poll."""
    try:
        r = httpx.get(f"{CLAUDE_BRIDGE_URL}/health", timeout=0.5)
        return r.status_code == 200
    except Exception:
        return False


def _tier_keys() -> dict:
    """Key presence only — no API calls, no cost."""
    from app.config import settings  # noqa: PLC0415
    return {
        "claude":     bool(settings.anthropic_api_key),
        "nagatha":    bool(settings.gemini_api_key),
        "elevenlabs": bool(settings.elevenlabs_api_key),
    }


@bp.get("/health")
def health():
    from app import model_watchdog as mw  # noqa: PLC0415
    hang_events = mw.check_and_reroll()
    status = "degraded" if hang_events else "ok"
    payload: dict = {"status": status, "service": "skippytel"}
    if hang_events:
        payload["hung_tiers"] = hang_events
    payload["models"] = {
        "ollama": _ollama_status(),
        "tiers":  _tier_keys(),
    }
    payload["bridge"] = _bridge_status()
    ups = _ups_snapshot()
    if ups is not None:
        payload["ups"] = ups
    _write_snapshot(payload)
    return envelope(payload)
