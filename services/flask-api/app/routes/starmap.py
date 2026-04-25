"""GET /starmap?lat=&lng=&t_iso= — celestial bodies above the horizon.

STUB. Returns 501 until the physics-mcp service is built. Endpoint
exists so the phone's Star Map module fails gracefully (degraded
state) rather than crashing on a missing route.

Future contract: list of { name, type, az_deg, alt_deg, mag } for
stars/planets above horizon at the given lat/lng/time. Cacheable for
~60s. Wraps services/physics-mcp/ — no duplicated ephemeris math.
"""
from flask import Blueprint, request
from app.version import envelope, error

bp = Blueprint("starmap", __name__)


@bp.get("/starmap")
def starmap():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    t_iso = request.args.get("t_iso")
    if lat is None or lng is None or not t_iso:
        return error("lat, lng, t_iso required", error_type="bad_request", status=400)
    return error(
        "physics-mcp not yet wired; /starmap is stubbed",
        error_type="not_implemented",
        status=501,
    )
