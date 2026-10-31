"""The Car app (`/car`, the Captain only): his Tesla as TeslaMate last saw it, one bridge screen of glyph controls.

The data comes in the home report (skippy-pc's reporter asks skippy-core's car reader, core/car, which keeps TeslaMate's latest MQTT
values), so opening this page never wakes the car and never calls Tesla. Read only: commands (climate, locks, the charge limit) are
Tesla's Fleet API, saved for later (docs/tesla.md). Location is his alone: no grant opens this app.

  GET /car         the page
  GET /car/state   the same as JSON, refreshed while the page is open
  GET /.well-known/appspecific/com.tesla.3p.public-key.pem   public: the Fleet API app's public key (docs/tesla.md). Tesla reads it from
                   makerportal.live to register the domain, and the car checks commands against it. Its private half is on skippy-core
                   only (/etc/skippy-core/tesla-fleet/private.pem, root, made 2026-10-30).
  GET /car/oauth/callback   public: where Tesla sends the Captain back after he authorizes the app (the app's redirect URI). The page only
                   says it's done; it never shows or keeps the one-time code in the address: tools/tesla_fleet.py reads it from the browser
                   and swaps it for tokens at home, so the portal never holds the client secret or his tokens.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request

import auth

FLEET_PUBLIC_KEY = Path(__file__).resolve().parent / "tesla-fleet-public-key.pem"
KM_TO_MI = 0.621371          # a definition (1 mi = 1.609344 km), not a measurement
LIVE = {"online", "driving", "charging", "updating"}     # TeslaMate's states that mean the car is awake


def car_of(capability: dict | None) -> dict | None:
    cars = (capability or {}).get("cars") or []
    return cars[0] if cars and isinstance(cars[0], dict) else None


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _miles(km) -> str:
    km = _num(km)
    return f"{round(km * KM_TO_MI)} mi" if km is not None else "?"


def _temp(c) -> str:
    c = _num(c)
    return f"{round(c * 9 / 5 + 32)}°F" if c is not None else "?"


def view(capability: dict | None, online: bool) -> dict:
    """What the page shows: `state` (no_home, no_teslamate, no_car, ok), the car's name and a list of units
    (key, glyph, text, lamp, label). Words only for names and numbers; everything else is a glyph and a lamp."""
    cap = capability or {}
    if not online:
        return {"state": "no_home", "units": []}
    if not cap.get("mqtt"):
        return {"state": "no_teslamate", "units": []}
    car = car_of(cap)
    if car is None:
        return {"state": "no_car", "sign_in": cap.get("sign_in") or "", "units": []}
    st = str(car.get("state") or "?")
    level = _num(car.get("battery_level"))
    limit = _num(car.get("charge_limit_soc"))
    plugged, charging = car.get("plugged_in") is True, st == "charging"
    power = _num(car.get("charger_power"))
    to_full = _num(car.get("time_to_full_charge"))
    closed = not any(car.get(k) for k in ("doors_open", "windows_open", "trunk_open", "frunk_open"))
    units = [
        {"key": "state", "glyph": "car", "text": st, "label": "Car state",
         "lamp": "on" if st in LIVE else ("idle" if st in ("asleep", "suspended") else "off")},
        {"key": "battery", "glyph": "battery", "label": "Battery",
         "text": (f"{level}%" if level is not None else "?") + (f" / {limit}%" if limit is not None else ""),
         "lamp": "warn" if level is not None and level < 20 else "on"},
        {"key": "range", "glyph": "orbit", "text": _miles(car.get("est_battery_range_km")), "label": "Estimated range", "lamp": ""},
        {"key": "charge", "glyph": "bolt", "label": "Charging",
         "text": (f"{power} kW" + (f" · {to_full:g} h" if to_full else "") if charging and power is not None else
                  ("plugged" if plugged else "unplugged")),
         "lamp": "on" if charging else ("idle" if plugged else "")},
        {"key": "locks", "glyph": "shield", "label": "Locks and doors",
         "text": ("locked" if car.get("locked") else "unlocked") + ("" if closed else " · open"),
         "lamp": "on" if car.get("locked") and closed else "warn"},
        {"key": "climate", "glyph": "thermo", "label": "Inside / outside",
         "text": f"{_temp(car.get('inside_temp'))} / {_temp(car.get('outside_temp'))}",
         "lamp": "on" if car.get("is_climate_on") else ""},
        {"key": "where", "glyph": "globe", "label": "Where", "text": str(car.get("geofence") or ("driving" if car.get("shift_state") in ("D", "R") else "away")),
         "lamp": ""},
        {"key": "sentry", "glyph": "eye", "label": "Sentry mode", "text": "sentry on" if car.get("sentry_mode") else "sentry off",
         "lamp": "on" if car.get("sentry_mode") else ""},
    ]
    return {"state": "ok", "name": str(car.get("name") or "Car"), "updated_s": _num(car.get("updated_s")),
            "version": car.get("version"), "update": bool(car.get("update_available")), "units": units}


def summary(capability: dict | None) -> dict | None:
    """Skippy's car_status tool: the car's facts in plain units (miles, °F, kW), for a model to say back. Location is the
    geofence name and coordinates: the tool is the Captain's alone (tool_policy "car")."""
    car = car_of(capability)
    if car is None:
        return None
    km = _num(car.get("est_battery_range_km"))
    out = {k: car.get(k) for k in ("name", "state", "since", "battery_level", "usable_battery_level", "charge_limit_soc", "plugged_in",
                                    "charger_power", "time_to_full_charge", "charge_energy_added", "locked", "sentry_mode",
                                    "doors_open", "windows_open", "trunk_open", "frunk_open", "is_climate_on", "geofence",
                                    "latitude", "longitude", "version", "update_available", "updated_s") if car.get(k) is not None}
    if km is not None:
        out["estimated_range_mi"] = round(km * KM_TO_MI)
    for k in ("inside_temp", "outside_temp"):
        if _num(car.get(k)) is not None:
            out[k + "_f"] = round(car[k] * 9 / 5 + 32)
    if _num(car.get("odometer")) is not None:
        out["odometer_mi"] = round(car["odometer"] * KM_TO_MI)
    return out


def init_app(app, home) -> None:
    bp = Blueprint("car", __name__)

    def current() -> dict:
        return view(home.car(), home.online())

    @bp.get("/car")
    @auth.admin_required
    def page():
        return render_template("car.html", car=current())

    @bp.get("/car/state")
    @auth.admin_required
    def state():
        resp = jsonify(current())
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @bp.get("/car/oauth/callback")
    def fleet_callback():
        failed = bool(request.args.get("error"))
        resp = app.response_class(
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Tesla</title>"
            "<body style='background:#000;color:#00ff88;font:16px ui-monospace,monospace;display:grid;place-items:center;height:90vh'>"
            + ("<p style='color:#ff4444'>Tesla said no. Tell Skippy.</p>" if failed else "<p>Tesla said yes. Skippy takes it from here.</p>"),
            mimetype="text/html")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"          # the code in this address never leaves in a Referer
        return resp

    @bp.get("/.well-known/appspecific/com.tesla.3p.public-key.pem")
    def fleet_public_key():
        try:
            pem = FLEET_PUBLIC_KEY.read_text(encoding="ascii")
        except OSError:
            abort(404)
        resp = app.response_class(pem, mimetype="application/x-pem-file")
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    app.register_blueprint(bp)
