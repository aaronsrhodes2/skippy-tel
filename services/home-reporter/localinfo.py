"""Weather and the commute, for the /me dashboard (and Skippy's voice): what it is like outside, and whether the drive between home and work is slow.

Weather: Open-Meteo (free, no key, open data) for home's town and work's town, plus the US National Weather Service's active alerts for each point.
Highway incidents: the California Highway Patrol's public incident feed (no key): crashes, hazards and closures on the roads between home and work
(SR 9, 17, 35, 85, 236, 237, US 101, I-280, inside a box around the two addresses). This is the always-on source of "there are delays".
Drive times (optional): TomTom Routing (a free developer key, TOMTOM_API_KEY) or Google Routes (MAPS_API_KEY, needs billing on its Google project) give the live
time and how much slower than free flow it is. Whichever works is used; if neither, the incidents still show and `note` says what is missing.

Settings (the vault `.env`, which the reporter container reads): HOME_ADDRESS, WORK_ADDRESS (default his work), TOMTOM_API_KEY, MAPS_API_KEY, LOCAL_TZ (default
America/Los_Angeles), COMMUTE_ROADS and COMMUTE_BOX (south,west,north,east) if the route changes. No coordinates or IPs are hard-coded: addresses are geocoded.
Polite with the services: everything is cached (weather 15 min; times and incidents 5 min in the commute hours, weekdays 6 to 10 and 15 to 19, else 30 min).
A failure keeps the last good answer, marked stale, and never raises.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_WORK = "250 E Caribbean Dr, Sunnyvale, CA"
DEFAULT_ROADS = "9,17,35,85,236,237,101,280"
DEFAULT_BOX = (37.05, -122.30, 37.50, -121.90)             # south, west, north, east: Boulder Creek to Sunnyvale and a margin
WEATHER_TTL, BUSY_TTL, QUIET_TTL = 900, 300, 1800
STALE_AFTER = 3 * 3600
DELAY_MIN_SECONDS, DELAY_FRACTION = 600, 0.4               # a delayed drive is 10 minutes AND 40 percent over free flow
CHP_URL = "https://media.chp.ca.gov/sa_xml/sa.xml"

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
       56: "freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers",
       86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}

# CHP log type code -> (plain words, how much it matters to a driver)
KINDS = [(r"^(1179|1180|1183|1182|20001|20002|23109)", "crash", "major"), (r"^(sig|1184|1141|closure|1013|flood|fire|1144)", "closure, flood or fire", "major"),
         (r"^(1125|1125a|hazard|1124)", "hazard", "minor"), (r"^(czp|mzp|tadv|esc|assist)", "roadwork or advisory", "info")]


def _fetch(url: str, data: dict | None = None, headers: dict | None = None, timeout: float = 10.0, raw: bool = False):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, method="POST" if data is not None else "GET",
                                 headers={"User-Agent": "skippy-home (aaron.s.rhodes@gmail.com)", **({"Content-Type": "application/json"} if data is not None else {}), **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return body if raw else json.loads(body or b"{}")


def _city(address: str) -> str:
    parts = [p.strip() for p in address.split(",")]
    parts = [p for p in parts if not re.fullmatch(r"\d{5}(-\d{4})?", p)]
    return ", ".join(p.split(" ")[0] if re.fullmatch(r"[A-Z]{2}\s*\d{5}", p) else p for p in parts[-2:]) if len(parts) >= 2 else address


class LocalInfo:
    def __init__(self, env=None, fetch=_fetch, clock=time.time):
        self.env = dict(os.environ if env is None else env)
        self.fetch, self.clock = fetch, clock
        self.work = (self.env.get("WORK_ADDRESS") or DEFAULT_WORK).strip()
        self.home = (self.env.get("HOME_ADDRESS") or "").strip()
        self.tomtom = (self.env.get("TOMTOM_API_KEY") or "").strip()
        self.google = (self.env.get("MAPS_API_KEY") or "").strip()
        self.tz = ZoneInfo(self.env.get("LOCAL_TZ") or "America/Los_Angeles")
        self.places = {"home": _city(self.home) if self.home else "", "work": _city(self.work)}
        roads = [r.strip() for r in (self.env.get("COMMUTE_ROADS") or DEFAULT_ROADS).split(",") if r.strip().isdigit()]
        self._road = re.compile(r"\b(?:SR|I|US|HWY)\s?-?(?:" + "|".join(roads) + r")\b", re.I) if roads else None
        box = [float(x) for x in (self.env.get("COMMUTE_BOX") or "").split(",") if x.strip()] if self.env.get("COMMUTE_BOX") else []
        self.box = tuple(box) if len(box) == 4 else DEFAULT_BOX
        self._weather: dict[str, dict] = {}
        self._coords: dict[str, tuple[float, float, str]] = {}
        self._commute: dict | None = None
        self._incidents: dict | None = None
        self._broken: set[str] = set()                     # providers that said "not allowed": do not hammer them

    def busy_hours(self) -> bool:
        now = datetime.fromtimestamp(self.clock(), self.tz)
        return now.weekday() < 5 and (6 <= now.hour < 10 or 15 <= now.hour < 19)

    def _ttl(self) -> int:
        return BUSY_TTL if self.busy_hours() else QUIET_TTL

    # ── weather ─────────────────────────────────────────────────────────────────
    def _locate(self, key: str) -> tuple[float, float, str]:
        if key not in self._coords:
            place = self.places[key]
            name, state = place.split(",")[0].strip(), (place.split(",")[-1].strip()[:2].upper() if "," in place else "")
            res = self.fetch("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": name, "count": 10, "country_code": "US", "language": "en"}))
            results = res.get("results") or []
            ok = [r for r in results if not state or str(r.get("admin1_id") or "") != "" and self._state_of(r) == state] or results
            if not ok:
                raise ValueError("place not found")
            hit = ok[0]
            self._coords[key] = (float(hit["latitude"]), float(hit["longitude"]), f"{hit['name']}, {self._state_of(hit) or hit.get('admin1', '')}")
        return self._coords[key]

    @staticmethod
    def _state_of(hit: dict) -> str:
        return {"California": "CA"}.get(str(hit.get("admin1") or ""), str(hit.get("admin1") or "")[:2].upper())

    def weather(self, key: str = "home") -> dict:
        """{ok, place, temp_f, feels_f, text, wind_mph, hi_f, lo_f, rain_chance, next_hours, alerts, age_s, stale} or {ok: False, error}."""
        if not self.places.get(key):
            return {"ok": False, "error": "no place set"}
        cached = self._weather.get(key)
        if cached and self.clock() - cached["at"] < WEATHER_TTL:
            return self._wview(cached)
        try:
            lat, lon, name = self._locate(key)
            q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
                                        "timezone": str(self.tz), "forecast_days": 2, "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                                        "hourly": "temperature_2m,precipitation_probability,weather_code",
                                        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
            w = self.fetch("https://api.open-meteo.com/v1/forecast?" + q)
            cur, hourly, daily = w.get("current") or {}, w.get("hourly") or {}, w.get("daily") or {}
            times = hourly.get("time") or []
            now_h = datetime.fromtimestamp(self.clock(), self.tz).strftime("%Y-%m-%dT%H:00")
            start = times.index(now_h) if now_h in times else 0
            nxt = []
            for i in range(start, min(start + 12, len(times)), 3):
                hour = datetime.fromisoformat(times[i]).strftime("%I %p").lstrip("0")
                nxt.append({"hour": hour, "temp_f": round(hourly["temperature_2m"][i]), "rain": hourly["precipitation_probability"][i], "text": WMO.get(hourly["weather_code"][i], "")})
            self._weather[key] = {"ok": True, "at": self.clock(), "place": name, "temp_f": round(cur.get("temperature_2m", 0)), "feels_f": round(cur.get("apparent_temperature", 0)),
                                  "text": WMO.get(cur.get("weather_code"), ""), "wind_mph": round(cur.get("wind_speed_10m", 0)),
                                  "hi_f": round((daily.get("temperature_2m_max") or [0])[0]), "lo_f": round((daily.get("temperature_2m_min") or [0])[0]),
                                  "rain_chance": (daily.get("precipitation_probability_max") or [0])[0], "next_hours": nxt, "alerts": self._alerts(lat, lon)}
        except (OSError, ValueError, KeyError, TypeError, IndexError, urllib.error.URLError) as e:
            if key not in self._weather:
                return {"ok": False, "error": f"weather unavailable ({type(e).__name__})"}
        return self._wview(self._weather[key])

    def _alerts(self, lat: float, lon: float) -> list[dict]:
        try:
            res = self.fetch(f"https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}", headers={"Accept": "application/geo+json"})
            return [{"event": str((f.get("properties") or {}).get("event") or ""), "headline": str((f.get("properties") or {}).get("headline") or "")[:200],
                     "severity": str((f.get("properties") or {}).get("severity") or "")} for f in (res.get("features") or [])[:3]]
        except (OSError, ValueError, urllib.error.URLError):
            return []                                         # alerts are a bonus: their absence is not an error

    def _wview(self, c: dict) -> dict:
        age = self.clock() - c["at"]
        return {**{k: v for k, v in c.items() if k != "at"}, "age_s": int(age), "stale": age > STALE_AFTER}

    # ── highway incidents (CHP) ─────────────────────────────────────────────────
    def incidents(self) -> dict:
        """{ok, items: [{time, kind, severity, where, detail, area}], age_s}: crashes and hazards on the road between home and work, worst first."""
        if self._incidents and self.clock() - self._incidents["at"] < self._ttl():
            return self._iview(self._incidents)
        try:
            root = ET.fromstring(self.fetch(CHP_URL, raw=True))
            items = []
            for log in root.iter("Log"):
                g = lambda t: (log.findtext(t) or "").strip().strip('"')           # noqa: E731
                where, desc = g("Location"), g("LocationDesc")
                try:
                    lat, lon = (float(x) / 1e6 for x in g("LATLON").split(":"))
                    lon = -abs(lon)
                except ValueError:
                    continue
                s, w, n, e = self.box
                if not (s <= lat <= n and w <= lon <= e) or not (self._road and self._road.search(where + " " + desc)):
                    continue
                code = g("LogType")
                kind, severity = next(((k, sev) for pat, k, sev in KINDS if re.match(pat, code, re.I)), (code.split("-", 1)[-1].strip().lower(), "info"))
                items.append({"time": g("LogTime"), "kind": kind, "severity": severity, "where": where, "detail": desc, "area": g("Area")})
            order = {"major": 0, "minor": 1, "info": 2}
            items.sort(key=lambda i: (order[i["severity"]], i["where"]))
            self._incidents = {"ok": True, "at": self.clock(), "items": items[:12]}
        except (OSError, ValueError, ET.ParseError, urllib.error.URLError) as e:
            if not self._incidents:
                return {"ok": False, "error": f"incidents unavailable ({type(e).__name__})"}
        return self._iview(self._incidents)

    def _iview(self, c: dict) -> dict:
        return {**{k: v for k, v in c.items() if k != "at"}, "age_s": int(self.clock() - c["at"])}

    # ── drive times (TomTom or Google) ──────────────────────────────────────────
    def _tomtom_leg(self, name: str, origin: str, dest: str) -> dict:
        def geocode(addr):
            r = self.fetch(f"https://api.tomtom.com/search/2/geocode/{urllib.parse.quote(addr)}.json?" + urllib.parse.urlencode({"key": self.tomtom, "limit": 1, "countrySet": "US"}))
            p = r["results"][0]["position"]
            return f"{p['lat']},{p['lon']}"
        a, b = geocode(origin), geocode(dest)
        r = self.fetch(f"https://api.tomtom.com/routing/1/calculateRoute/{a}:{b}/json?" + urllib.parse.urlencode({"key": self.tomtom, "traffic": "true", "travelMode": "car", "computeTravelTimeFor": "all"}))
        s = r["routes"][0]["summary"]
        live, free = float(s["travelTimeInSeconds"]), float(s.get("noTrafficTravelTimeInSeconds") or s["travelTimeInSeconds"])
        return self._leg(name, live, free, float(s.get("lengthInMeters", 0)), "")

    def _google_leg(self, name: str, origin: str, dest: str) -> dict:
        res = self.fetch("https://routes.googleapis.com/directions/v2:computeRoutes",
                         data={"origin": {"address": origin}, "destination": {"address": dest}, "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE",
                               "departureTime": datetime.fromtimestamp(self.clock() + 30, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"), "units": "IMPERIAL"},
                         headers={"X-Goog-Api-Key": self.google, "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.distanceMeters,routes.description"})
        r = (res.get("routes") or [None])[0]
        if not r:
            raise ValueError("no route")
        return self._leg(name, float(str(r["duration"]).rstrip("s")), float(str(r["staticDuration"]).rstrip("s")), float(r.get("distanceMeters", 0)), str(r.get("description") or ""))

    @staticmethod
    def _leg(name: str, live: float, free: float, meters: float, route: str) -> dict:
        delay = max(0.0, live - free)
        return {"name": name, "minutes": round(live / 60), "free_flow_minutes": round(free / 60), "delay_minutes": round(delay / 60), "miles": round(meters / 1609.34, 1),
                "route": route, "delayed": delay >= DELAY_MIN_SECONDS and delay >= free * DELAY_FRACTION}

    def _times(self) -> tuple[str, list[dict], str]:
        """(provider, legs, note): the first drive-time provider that answers."""
        if not self.home:
            return "", [], "Tell Skippy your home address (HOME_ADDRESS in the vault) to get drive times."
        tried = []
        for provider, key, leg in (("tomtom", self.tomtom, self._tomtom_leg), ("google", self.google, self._google_leg)):
            if not key or provider in self._broken:
                continue
            try:
                return provider, [leg("To work", self.home, self.work), leg("To home", self.work, self.home)], ""
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):                                                 # not allowed (billing off, key not enabled): stop asking
                    self._broken.add(provider)
                tried.append(f"{provider}: HTTP {e.code}")
            except (OSError, ValueError, KeyError, TypeError, IndexError, urllib.error.URLError) as e:
                tried.append(f"{provider}: {type(e).__name__}")
        if tried:
            return "", [], "Drive times are not available (" + "; ".join(tried) + ")."
        return "", [], "Live drive times need a TomTom key (free: TOMTOM_API_KEY) or billing on the Google Maps project (MAPS_API_KEY); the incidents below are from the CHP."

    def commute(self) -> dict:
        """{ok, provider, legs, incidents, alert, note, age_s}: always carries the CHP incidents; drive times when a provider works."""
        if self._commute and self.clock() - self._commute["at"] < self._ttl():
            return self._cview(self._commute)
        provider, legs, note = self._times()
        inc = self.incidents()
        items = inc.get("items", []) if inc.get("ok") else []
        self._commute = {"ok": True, "at": self.clock(), "provider": provider, "legs": legs, "incidents": items, "incidents_ok": bool(inc.get("ok")), "note": note,
                         "alert": self._alert(legs, items)}
        return self._cview(self._commute)

    @staticmethod
    def _alert(legs: list[dict], items: list[dict]) -> str:
        bad = [l for l in legs if l["delayed"]]
        if bad:
            worst = max(bad, key=lambda l: l["delay_minutes"])
            via = f" via {worst['route']}" if worst["route"] else ""
            return f"{worst['name']}: {worst['minutes']} min{via}, {worst['delay_minutes']} min slower than usual."
        major = [i for i in items if i["severity"] == "major"]
        if major:
            first = major[0]
            more = f" and {len(major) - 1} more" if len(major) > 1 else ""
            return f"Possible delays: {first['kind']} at {first['where']}{more}."
        return ""

    def _cview(self, c: dict) -> dict:
        return {**{k: v for k, v in c.items() if k != "at"}, "age_s": int(self.clock() - c["at"])}

    # ── what the report carries, and what Skippy says ───────────────────────────
    def snapshot(self) -> dict:
        if self.env.get("LOCAL_INFO") == "off":                                    # tests, and anyone who does not want it
            return {"disabled": True}
        return {"weather": {k: self.weather(k) for k in ("home", "work") if self.places.get(k)}, "commute": self.commute()}

    def speak_weather(self, key: str = "home") -> str:
        key = key if self.places.get(key) else "work"
        w = self.weather(key)
        if not w.get("ok"):
            return "I cannot get the weather right now."
        rain = f", {w['rain_chance']} percent chance of rain today" if w["rain_chance"] else ""
        alert = f" Weather alert: {w['alerts'][0]['event']}." if w["alerts"] else ""
        return f"In {w['place']} it is {w['temp_f']} degrees and {w['text']}, feels like {w['feels_f']}, wind {w['wind_mph']} miles an hour; high {w['hi_f']}, low {w['lo_f']}{rain}.{alert}"

    def speak_commute(self) -> str:
        c = self.commute()
        parts = []
        for leg in c["legs"]:
            slow = f", {leg['delay_minutes']} minutes slower than usual" if leg["delay_minutes"] >= 3 else ", no real delay"
            parts.append(f"{leg['name'].lower()}: {leg['minutes']} minutes{slow}")
        times = "; ".join(parts) + "." if parts else ""
        inc = [i for i in c["incidents"] if i["severity"] in ("major", "minor")][:3]
        roads = "; ".join(f"{i['kind']} at {i['where']}" for i in inc)
        tail = f" On your roads right now: {roads}." if roads else " No incidents reported on your roads."
        return (times + tail).strip() if (times or c["incidents_ok"]) else (c["note"] or "I cannot see the traffic right now.")
