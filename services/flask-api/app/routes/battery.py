"""GET /battery — PC's own battery / AC state.

Contract: { level: 0..100, source: "ac"|"battery"|"ups", plugged: bool }
Phone polls every 60s and aggregates with other Skippy nodes.

Desktops without batteries (most PCs) report level=100, source="ac",
plugged=true. UPS detection deferred — Windows exposes UPS via WMI but
not via psutil; future work.
"""
import psutil
from flask import Blueprint
from app.version import envelope

bp = Blueprint("battery", __name__)


@bp.get("/battery")
def battery():
    b = psutil.sensors_battery()
    if b is None:
        # Desktop with no battery — assume wall power.
        return envelope({"level": 100, "source": "ac", "plugged": True})
    return envelope({
        "level": int(round(b.percent)),
        "source": "battery" if not b.power_plugged else "ac",
        "plugged": bool(b.power_plugged),
    })
