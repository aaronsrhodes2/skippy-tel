#!/usr/bin/env python3
"""The road door: how far and how long by car, and where an address is, from map data on this box (no cloud, no API fee, no key).

Two engines run beside it (core/compose.routes.yml), both built once from OpenStreetMap's California extract (Geofabrik):
Valhalla (roads: distance and drive time, without live traffic) and Nominatim (addresses and places -> a position). This door puts
them behind one small JSON API for skippy-pc (the morning briefing's leave-by time and today's miles, services/satellite/daily.py).

    GET  /geocode?q=<address>[&near=<lat>,<lon>][&bounded=1]
         {"lat": 37.77, "lon": -122.41, "label": "...", "approx": false, "source": "..."}   404 when nothing matches
         A calendar's location often starts with a business name or a suite ("Acme, 123 Main St Ste 4, Town, CA 95000, USA"): the
         full text is tried first, then with the suite gone, then with each leading part dropped in turn. `approx` is true when the
         street had to go (the answer is the town). `near` prefers results around a point; `bounded=1` keeps them within ~100 km of it.
    POST /route {"points": [[lat, lon], [lat, lon], ...]}
         {"km": 41.2, "minutes": 38, "legs": [{"km": ..., "minutes": ...}], "source": "..."}   one leg per hop, in order
    GET  /health   the readiness contract (ready.py): ready when both engines answer. The first start builds both from the map
                   (about an hour); until then it says what it's waiting on.

Figures are Valhalla's own (its km and seconds; minutes are its seconds / 60). Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ready import Readiness

PORT = int(os.environ.get("PORT", "7354"))
HOST = os.environ.get("HOST", "0.0.0.0")                    # inside the container; compose publishes it on 127.0.0.1 only
VALHALLA = os.environ.get("VALHALLA_URL", "http://valhalla:8002").rstrip("/")
NOMINATIM = os.environ.get("NOMINATIM_URL", "http://nominatim:8080").rstrip("/")
MAP = os.environ.get("ROUTES_MAP", "OpenStreetMap, California extract (Geofabrik)")
BOUND_DEG = 1.0                                           # bounded=1: a box this many degrees each way around `near` (~110 km north-south)
NEAR_DEG = 0.5                                            # near=: the preferred box (results outside it still count)

READY = Readiness("routes")
_deps: tuple[float, dict] | None = None
_deps_lock = threading.Lock()


def http_json(url: str, body: dict | None = None, timeout: float = 20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "skippy-core-routes"} if data else
                                 {"User-Agent": "skippy-core-routes"}, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:     # noqa: S310 — the two engines on this box's own network
        return json.loads(r.read() or b"null")


# ── addresses ──
SUITE_RE = re.compile(r"\s*(?:,\s*)?\b(?:suite|ste\.?|unit|apt\.?|apartment|floor|fl\.?|room|rm\.?|bldg\.?|building)\s*#?\s*[\w-]+\b|\s*#\s*\d+[\w-]*", re.I)
COUNTRY_RE = re.compile(r",?\s*(?:USA|U\.S\.A\.|United States(?: of America)?|US)\s*$", re.I)


def candidates(text: str) -> list[tuple[str, bool]]:
    """The searches to try, in order: (query, approx). The whole text, then without the suite, then with leading parts dropped; a
    query left with only a town (no digit in it) is approx."""
    q = COUNTRY_RE.sub("", re.sub(r"\s+", " ", text or "").strip()).strip(" ,")
    if not q:
        return []
    out: list[tuple[str, bool]] = []

    def add(s: str, approx: bool):
        s = s.strip(" ,")
        if s and s.lower() not in (c.lower() for c, _ in out):
            out.append((s, approx))
    add(q, False)
    bare = SUITE_RE.sub("", q)
    add(bare, False)
    parts = [p.strip() for p in bare.split(",") if p.strip()]
    for i in range(1, len(parts) - 1):                        # never down to the state alone
        rest = parts[i:]
        add(", ".join(rest), not any(ch.isdigit() for ch in rest[0]))
    return out


def geocode(text: str, near: tuple[float, float] | None = None, bounded: bool = False, get=http_json) -> dict | None:
    for q, approx in candidates(text):
        params = {"q": q, "format": "jsonv2", "limit": 1, "addressdetails": 0}
        if near is not None:
            d = BOUND_DEG if bounded else NEAR_DEG
            params["viewbox"] = f"{near[1] - d:.4f},{near[0] + d:.4f},{near[1] + d:.4f},{near[0] - d:.4f}"
            if bounded:
                params["bounded"] = 1
        got = get(f"{NOMINATIM}/search?{urllib.parse.urlencode(params)}", timeout=15) or []
        if got:
            hit = got[0]
            return {"lat": float(hit["lat"]), "lon": float(hit["lon"]), "label": hit.get("display_name") or q, "query": q,
                    "approx": approx or hit.get("addresstype") in ("city", "town", "village", "county", "state", "postcode"),
                    "source": f"Nominatim ({MAP})"}
    return None


# ── roads ──
def route(points: list, post=http_json, valhalla_status=None) -> dict:
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 2 or len(pts) > 20 or any(not (-90 <= a <= 90 and -180 <= b <= 180) for a, b in pts):
        raise ValueError("points: 2 to 20 [lat, lon] pairs")
    got = post(f"{VALHALLA}/route", {"locations": [{"lat": a, "lon": b} for a, b in pts], "costing": "auto", "units": "kilometers",
                                     "directions_type": "none"}, timeout=30)
    trip = got["trip"]
    legs = [{"km": round(float(lg["summary"]["length"]), 2), "minutes": round(float(lg["summary"]["time"]) / 60)} for lg in trip["legs"]]
    s = trip["summary"]
    data = (valhalla_status or {}).get("tileset_last_modified")
    when = time.strftime(" %Y-%m-%d", time.gmtime(data)) if data else ""
    return {"km": round(float(s["length"]), 2), "minutes": round(float(s["time"]) / 60), "legs": legs,
            "source": f"Valhalla, auto, no live traffic ({MAP}{when})"}


# ── health ──
def check_deps(get=http_json, force: bool = False) -> dict:
    global _deps
    with _deps_lock:
        if not force and _deps and time.monotonic() - _deps[0] < 10:
            return _deps[1]
    deps = {}
    try:
        st = get(f"{VALHALLA}/status", timeout=3) or {}
        deps["valhalla"] = {"ready": True, "detail": "", "status": st}
    except (OSError, urllib.error.URLError, ValueError) as e:
        deps["valhalla"] = {"ready": False, "detail": f"not answering yet (the first start builds California's road tiles): {type(e).__name__}"}
    try:
        st = get(f"{NOMINATIM}/status?format=json", timeout=3) or {}
        ok = st.get("status") == 0
        deps["nominatim"] = {"ready": ok, "detail": "" if ok else f"says {st.get('message')}"}
    except (OSError, urllib.error.URLError, ValueError) as e:
        deps["nominatim"] = {"ready": False, "detail": f"not answering yet (the first start imports California's addresses): {type(e).__name__}"}
    with _deps_lock:
        _deps = (time.monotonic(), deps)
    return deps


def health(get=http_json) -> dict:
    deps = check_deps(get)
    shown = {k: {"ready": v["ready"], "detail": v["detail"]} for k, v in deps.items()}
    return READY.report(ready=True, deps=shown, map=MAP)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        if u.path == "/health":
            got = health()
            return self._send(200 if got["ready"] else 503, got)
        if u.path != "/geocode":
            return self._send(404, {"error": "not found"})
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            near = tuple(float(x) for x in q["near"].split(",")) if q.get("near") else None
            if near is not None and len(near) != 2:
                raise ValueError("near: lat,lon")
            with READY.job("geocode"):
                got = geocode(q.get("q", "")[:300], near, q.get("bounded") == "1")
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except (OSError, urllib.error.URLError) as e:
            return self._send(503, {"error": f"Nominatim isn't answering: {type(e).__name__}"})
        self._send(200 if got else 404, got or {"error": "no place matches", "q": q.get("q", "")})

    def do_POST(self):  # noqa: N802
        if self.path.split("?", 1)[0] != "/route":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 16384)) or b"{}")
            with READY.job("route"):
                got = route(body.get("points") or [], valhalla_status=check_deps()["valhalla"].get("status"))
        except (ValueError, TypeError, AttributeError, IndexError) as e:
            return self._send(400, {"error": str(e)})
        except urllib.error.HTTPError as e:                     # Valhalla's 400: no road between the points
            return self._send(422, {"error": "no route: " + (e.read() or b"")[:200].decode("utf-8", "replace")})
        except (OSError, urllib.error.URLError, KeyError) as e:
            return self._send(503, {"error": f"Valhalla isn't answering: {type(e).__name__}"})
        self._send(200, got)

    def log_message(self, *args):
        pass


def alive() -> int:
    """Docker's health check: the door answers at all (503 while the map builds still counts; ready is /health's job)."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=4)   # noqa: S310
    except urllib.error.HTTPError:
        pass
    except (OSError, urllib.error.URLError):
        return 1
    return 0


def main() -> None:
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    if sys.argv[1:] == ["--alive"]:
        raise SystemExit(alive())
    main()
