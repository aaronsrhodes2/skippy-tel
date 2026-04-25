"""GET /health — sub-100ms liveness check.

Phone polls this every 10s (TransportLayer.kt:91). Latency drives the
RightBar SignalStack: <50ms = 4 bars, <150ms = 3, <400ms = 2,
reachable = 1, unreachable = 0. Keep this hot.

Includes a non-blocking UPS snapshot if the host monitor is reachable.
UPS data is fetched with a short timeout and omitted on failure so this
endpoint never slows down due to the monitor being offline.
"""
import httpx
from flask import Blueprint
from app.version import envelope

bp = Blueprint("health", __name__)

UPS_MONITOR_URL = "http://host.docker.internal:9338"


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


@bp.get("/health")
def health():
    payload: dict = {"status": "ok", "service": "skippytel"}
    ups = _ups_snapshot()
    if ups is not None:
        payload["ups"] = ups
    return envelope(payload)
