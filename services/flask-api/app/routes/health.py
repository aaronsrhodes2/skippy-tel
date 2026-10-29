"""GET /health — sub-100ms liveness check.

Phone polls this every 10s (TransportLayer.kt:91). Latency drives the
RightBar SignalStack: <50ms = 4 bars, <150ms = 3, <400ms = 2,
reachable = 1, unreachable = 0. Keep this hot.

Model availability policy — no API calls that cost money:
  - Ollama: GET /api/tags (free local Docker call) confirms the daemon is
    running and which models are loaded.
  - Paid tiers (Gemini, ElevenLabs): key-present check only.
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
    """GET /api/tags — free local call, confirms Ollama is up and loaded.

    `default` is the model the default tier asks for and `default_present` says
    whether Ollama actually has it: a daemon that's up but missing that model
    answers every default chat with HTTP 404."""
    from app.config import settings  # noqa: PLC0415
    default = settings.ollama_model
    try:
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
        data = r.json()
        models = [m["name"] for m in data.get("models", [])]
        present = default in models or f"{default}:latest" in models
        return {"available": True, "models": models, "default": default, "default_present": present}
    except Exception:
        return {"available": False, "models": [], "default": default, "default_present": False}


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
        "nagatha": bool(settings.gemini_api_key),
    }


from app import ready as ready_mod  # noqa: E402

READY = ready_mod.Readiness("flask-api")        # main.py counts every POST (a chat, a translation, a battery report) as a job while it runs


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
    # PLATINUM section 24: not just "ok": ready to take a chat NOW (Ollama answers AND offers the default model, no tier hung), what it is
    # doing, and why not. The HTTP status stays 200 (phones and tools parse this body); `ready` is the word to read.
    ollama = payload["models"]["ollama"]
    llm_ok = bool(ollama.get("available") and ollama.get("default_present"))
    deps = {"ollama": {"ready": llm_ok, "detail": "" if llm_ok else (
        "the model server (skippy-core's Ollama shim) isn't answering" if not ollama.get("available")
        else f"the default model {ollama.get('default')} isn't offered")}}
    rep = READY.report(ready=not hang_events, deps=deps,
                       detail=("a model tier is hung: " + ", ".join(str(h) for h in hang_events)) if hang_events else "")
    payload.update({k: rep[k] for k in ("ready", "accepting", "jobs", "since", "stuck_s", "uptime_s", "detail", "deps")})
    ups = _ups_snapshot()
    if ups is not None:
        payload["ups"] = ups
    _write_snapshot(payload)
    return envelope(payload)
