"""Reverse-geocode + weather endpoints for the zHUD.

Both backed by free public services with no API key required:

  - Nominatim (OpenStreetMap) for /geocode
  - api.weather.gov (NOAA) for /weather

Aggressive in-memory caching: 10 minutes by rounded lat/lon
(2 decimal places ≈ 1 km bucket). The phone polls these at 60 s
cadence so the cache absorbs the load and we stay well within
both providers' acceptable-use policies.

Both endpoints fail-open with HTTP 503 + ``{"error": "..."}``
when the upstream is unreachable. Phone-side polling treats any
non-200 as "keep showing the last known value, retry next tick".
"""
from __future__ import annotations

import time
from typing import Optional

import httpx
from flask import Blueprint, jsonify, request

bp = Blueprint("geo", __name__)

# ── Config ────────────────────────────────────────────────────────────────

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_NOAA_POINTS   = "https://api.weather.gov/points/{lat},{lon}"
_USER_AGENT    = "SkippyTel/1.0 (zHUD; +https://example.invalid/skippy)"
_CACHE_TTL_S   = 10 * 60  # 10 minutes

# Rounded-coord cache: { (lat_2dp, lon_2dp): (expiry_ts, payload) }
_geo_cache:     dict[tuple[float, float], tuple[float, dict]] = {}
_weather_cache: dict[tuple[float, float], tuple[float, dict]] = {}


def _bucket(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 2), round(lon, 2))


def _cache_get(cache: dict, key) -> Optional[dict]:
    entry = cache.get(key)
    if entry is None:
        return None
    expiry, payload = entry
    if time.time() > expiry:
        cache.pop(key, None)
        return None
    return payload


def _cache_put(cache: dict, key, payload: dict) -> None:
    cache[key] = (time.time() + _CACHE_TTL_S, payload)


# ── /geocode ──────────────────────────────────────────────────────────────

@bp.post("/geocode")
def geocode():
    """POST {lat, lon} → {city, region, country}.

    Backed by Nominatim. The 10-min cache + 60s phone poll cadence keeps
    us comfortably inside Nominatim's "no more than one request per
    second per IP" guidance even for a moving Captain.
    """
    body = request.get_json(silent=True) or {}
    try:
        lat = float(body["lat"])
        lon = float(body["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="lat and lon required (numeric)"), 400

    key = _bucket(lat, lon)
    cached = _cache_get(_geo_cache, key)
    if cached is not None:
        return jsonify(cached)

    try:
        resp = httpx.get(
            _NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 12},
            headers={"User-Agent": _USER_AGENT},
            timeout=4.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"nominatim: {e}"), 503

    addr = data.get("address", {}) or {}
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("hamlet")
        or addr.get("suburb")
        or addr.get("county")
    )
    out = {
        "city":    city,
        "region":  addr.get("state") or addr.get("region"),
        "country": addr.get("country_code", "").upper() or None,
    }
    _cache_put(_geo_cache, key, out)
    return jsonify(out)


# ── /weather ──────────────────────────────────────────────────────────────

@bp.get("/weather")
def weather():
    """GET ?lat=&lon= → {tempF, condition, ts}.

    Two-step NOAA fetch: /points → grid endpoint → forecast. NOAA only
    serves the contiguous US (and territories). Outside that footprint
    we return 503 — the candy will hold its previous value.
    """
    try:
        lat = float(request.args.get("lat", ""))
        lon = float(request.args.get("lon", ""))
    except ValueError:
        return jsonify(error="lat and lon required (numeric)"), 400

    key = _bucket(lat, lon)
    cached = _cache_get(_weather_cache, key)
    if cached is not None:
        return jsonify(cached)

    try:
        with httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=4.0) as cx:
            points = cx.get(_NOAA_POINTS.format(lat=lat, lon=lon))
            points.raise_for_status()
            stations_url = points.json()["properties"]["observationStations"]

            stations = cx.get(stations_url)
            stations.raise_for_status()
            features = stations.json().get("features") or []
            if not features:
                return jsonify(error="no NOAA station nearby"), 503
            station_id = features[0]["properties"]["stationIdentifier"]

            obs = cx.get(f"https://api.weather.gov/stations/{station_id}/observations/latest")
            obs.raise_for_status()
            props = obs.json()["properties"]

        temp_c = (props.get("temperature") or {}).get("value")
        temp_f: Optional[int] = None
        if isinstance(temp_c, (int, float)):
            temp_f = int(round(temp_c * 9.0 / 5.0 + 32.0))

        out = {
            "tempF":     temp_f,
            "condition": props.get("textDescription"),
            "ts":        int(time.time()),
        }
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"noaa: {e}"), 503

    _cache_put(_weather_cache, key, out)
    return jsonify(out)
