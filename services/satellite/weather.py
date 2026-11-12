"""The weather, for Skippy at home (the Captain, 2026-10-31: "add it, including the public NOAA data, as I live near the coast").

One `weather` tool for the hub brain, from public sources only, no key:

* the National Weather Service (api.weather.gov, NOAA): the forecast for home in words, period by period, and any active alert there;
* NOAA's tide predictions (CO-OPS, tidesandcurrents.noaa.gov): today's highs and lows at the nearest tide station;
* NOAA's buoys (NDBC): the nearest station that reports waves, for the surf height, its period, the sea temperature and the wind;
* Open-Meteo, as the morning routine (daily.py) uses it: the temperature right now and what it feels like, and the day's UV index;
* what to bring or wear (bring(): a rain jacket, a warm layer or coat, light clothes and water, sunscreen) from the NWS hourly forecast and
  the UV index, with its thresholds in BRING. The morning routine's weather line uses the same advice (Weather.advice).

Home is SKIPPY_HOME_LATLON, else where the car spends the night (state/daily/state.json, learned by daily.py). The nearest tide station and buoy
are found once and kept in state/weather.json. Each answer is cached a few minutes. The tool returns MATERIAL; the brain gives the takeaway
(what to wear, whether to bother with an umbrella, whether the coast is worth it) with a figure or two, never a data card read aloud.

Unit factors are Mentat's (PLATINUM 21), convert_units, pint UnitRegistry, 2026-10-31.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

M_TO_FT = 3.2808398950131235            # Mentat convert_units 1 meter -> foot (pint UnitRegistry), 2026-10-31
MS_TO_MPH = 2.2369362920544025          # Mentat convert_units 1 m/s -> mile/hour (pint UnitRegistry), 2026-10-31
C_STEP_TO_F = 1.8                       # Mentat convert_units 1 delta_degC -> delta_degF (pint UnitRegistry), 2026-10-31
F_AT_0C = 32.0                          # Mentat convert_units 273.15 K -> degF (pint UnitRegistry), 2026-10-31

ROOT = Path(__file__).resolve().parents[2]
UA = "SkippyHome/1.0 (a home voice assistant; weather for one house)"
NWS = "https://api.weather.gov"
TIDES = "https://api.tidesandcurrents.noaa.gov"
NDBC = "https://www.ndbc.noaa.gov"
METEO = "https://api.open-meteo.com/v1/forecast"
CACHE_S = 600
BUOYS_TRIED = 10                        # the nearest stations looked at for one that reports waves

# What to bring or wear (the Captain, 2026-10-31: "humans want to know when it is raining or cold or hot to bring wet weather, warm, or cool
# clothing and sunscreen"). Comfort judgments, not physics: tune them here, the one place they live.
BRING = {
    "rain_pct": 40,        # an hour with this chance of rain or more: a rain jacket or an umbrella
    "cold_f": 55,          # the coldest waking hour at or below this: a warm layer
    "coat_f": 42,          # ... at or below this: a warm coat
    "hot_f": 85,           # the warmest hour at or above this: light, cool clothes and water
    "uv": 6,               # the day's UV index at or above this (EPA's "high" starts at 6): sunscreen
    "day": (7, 21),        # the waking hours the advice looks at (from now, today)
    "night": (18, 24),     # ... tonight
}

TOOL = {"name": "weather", "description": "The weather at home: the forecast in words (National Weather Service), any weather alert, the temperature now; "
                                          "with coast=true also today's tides and the surf and sea temperature from NOAA's nearest buoy. "
                                          "Use it for any question about the weather, rain, what to wear, the tides, the surf or the beach.",
        "input_schema": {"type": "object", "properties": {
            "when": {"type": "string", "enum": ["now", "today", "tonight", "tomorrow", "week"], "description": "Which part of the forecast (default today)."},
            "coast": {"type": "boolean", "description": "True for the tides, the surf, the sea or the beach."}}}}

PROMPT = (" For the weather, the tides or the surf, call the weather tool, then give him the TAKEAWAY in two or three sentences: what it means for "
          "him (a jacket, an umbrella, fog, whether the coast is worth the drive), with a figure or two at most, and only what the tool said. Never read "
          "the numbers out like a data card. Mention an alert first if there is one. When the tool says what to bring, lead with it in one short line "
          "('Grab a jacket, rain after three'), only the things it lists; when it says nothing is needed, don't invent a thing to bring.")

WEATHER_RE = re.compile(r"\b(?:weather|forecast|rain(?:ing|y)?|umbrella|jacket|coat|fog(?:gy)?|sunny|storm(?:s|y)?|snow(?:ing)?|temperature|"
                        r"how (?:cold|hot|warm) is it|tides?|surf|waves?|beach|swell|sunscreen|sunblock|uv|"
                        r"what (?:should|do) i (?:wear|bring|need)|dress for)\b", re.I)
COAST_RE = re.compile(r"\b(?:tides?|surf|waves?|beach|ocean|sea|coast|swell|buoy)\b", re.I)
NOT_WEATHER = re.compile(r"\b(?:news|headlines?|song|album|play|music|book|story|poem|haiku|joke|movie|film|game)\b", re.I)


def pre_route(heard: str) -> dict | None:
    """Tool args for a plain weather question ("do I need an umbrella tomorrow?", "how's the surf?"); None = the model decides."""
    h = heard or ""
    if not WEATHER_RE.search(h) or NOT_WEATHER.search(h):
        return None
    if re.search(r"\btomorrow\b", h, re.I):
        when = "tomorrow"
    elif re.search(r"\b(?:tonight|this evening|later tonight)\b", h, re.I):
        when = "tonight"
    elif re.search(r"\b(?:this week|week|weekend|next few days)\b", h, re.I):
        when = "week"
    else:
        when = "today"
    return {"when": when, "coast": True} if COAST_RE.search(h) else {"when": when}

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers",
       82: "violent rain showers", 95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def http_get(url: str, timeout: float = 20, raw: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json, application/json, text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as r:    # noqa: S310 — NOAA and Open-Meteo, public
        body = r.read()
    return body.decode("utf-8", "replace") if raw else json.loads(body)


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance, only to pick the nearest station (never said aloud)."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def home_of(env: dict, daily_state: Path | None = None) -> tuple[float, float] | None:
    spec = (env.get("SKIPPY_HOME_LATLON") or "").strip()
    if spec:
        try:
            lat, lon = (float(x) for x in spec.split(","))
            return lat, lon
        except ValueError:
            pass
    try:
        h = json.loads((daily_state or ROOT / "state" / "daily" / "state.json").read_text(encoding="utf-8")).get("home")
        return (float(h["lat"]), float(h["lon"])) if isinstance(h, dict) and "lat" in h else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def say_clock(hhmm: str) -> str:
    """'2026-10-31 14:20' -> '2:20 PM'."""
    try:
        h, m = (int(x) for x in hhmm[-5:].split(":"))
    except ValueError:
        return hhmm
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def c_to_f(c: float) -> float:
    return c * C_STEP_TO_F + F_AT_0C


def bring(hours: list[dict], uv: float | None = None, limits: dict | None = None) -> list[str]:
    """What to bring or wear for these hours: [{"t": "YYYY-MM-DDTHH:MM" local, "rain": percent, "temp_f": ...}], plus the day's UV index.
    Each item is a short spoken phrase; only what applies, and an empty list when nothing does."""
    lim = {**BRING, **(limits or {})}
    out = []
    wet = [h for h in hours if (h.get("rain") or 0) >= lim["rain_pct"]]
    if wet:
        first, peak = wet[0], max(h["rain"] for h in wet)
        when = "" if hours and first is hours[0] else f" after {say_clock(first['t']).replace(':00', '')}"
        out.append(f"a rain jacket or an umbrella, a {peak:.0f} percent chance of rain{when}")
    temps = [h["temp_f"] for h in hours if h.get("temp_f") is not None]
    if temps and min(temps) <= lim["coat_f"]:
        out.append(f"a warm coat, down to {min(temps):.0f} F")
    elif temps and min(temps) <= lim["cold_f"]:
        out.append(f"a warm layer, down to {min(temps):.0f} F")
    if temps and max(temps) >= lim["hot_f"]:
        out.append(f"light, cool clothes and water, up to {max(temps):.0f} F")
    if uv is not None and uv >= lim["uv"]:
        out.append(f"sunscreen, the UV index reaches {uv:.0f}")
    return out


def bring_line(items: list[str]) -> str:
    return ("What to bring or wear: " + "; ".join(items) + ".") if items else "What to bring or wear: nothing special."


class Weather:
    def __init__(self, env: dict | None = None, get=http_get, clock=time.time, state: Path | None = None, daily_state: Path | None = None, log=None):
        self.env = env if env is not None else dict(os.environ)
        self.get, self.clock, self.log = get, clock, log
        self.state_path = state or ROOT / "state" / "weather.json"
        self.daily_state = daily_state
        self._cache: dict[str, tuple[float, object]] = {}

    # ── plumbing ──
    def home(self):
        return home_of(self.env, self.daily_state)

    def _fetch(self, url: str, ttl: float = CACHE_S, raw: bool = False):
        hit = self._cache.get(url)
        if hit and self.clock() - hit[0] < ttl:
            return hit[1]
        got = self.get(url, raw=raw) if raw else self.get(url)
        self._cache[url] = (self.clock(), got)
        return got

    def _state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember(self, **kv) -> None:
        s = {**self._state(), **kv}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(s, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _home_key(self, home) -> str:
        return f"{home[0]:.2f},{home[1]:.2f}"                  # a moved home finds its stations again

    # ── the sources ──
    def nws(self, home, when: str) -> tuple[list[str], list[str]]:
        """(forecast lines, alert lines) from the National Weather Service."""
        lat, lon = f"{home[0]:.4f}", f"{home[1]:.4f}"
        points = self._fetch(f"{NWS}/points/{lat},{lon}", ttl=7 * 86400)["properties"]
        periods = self._fetch(points["forecast"])["properties"]["periods"]
        if when == "tonight":
            periods = [p for p in periods if not p.get("isDaytime")][:1] or periods[:1]
        elif when == "tomorrow":
            first_day = next((i for i, p in enumerate(periods) if p.get("isDaytime") and i > 0), 2)
            periods = periods[first_day:first_day + 2]
        elif when == "week":
            periods = [p for p in periods if p.get("isDaytime")][:7]
        else:
            periods = periods[:2]
        lines = [f"{p['name']}: {p.get('detailedForecast') or p.get('shortForecast')}" for p in periods]
        alerts = self._fetch(f"{NWS}/alerts/active?point={lat},{lon}", ttl=300).get("features") or []
        warn = [f"{a['properties'].get('event')}: {a['properties'].get('headline') or ''}".strip(": ") for a in alerts[:3]]
        return lines, warn

    def window(self, when: str) -> tuple[str, str] | None:
        """The local hours ('YYYY-MM-DDTHH:MM', start inclusive, end exclusive) the advice is for; None = no advice (the week)."""
        now = time.localtime(self.clock())
        today = time.strftime("%Y-%m-%d", now)
        if when == "tomorrow":
            day = time.strftime("%Y-%m-%d", time.localtime(self.clock() + 86400))
            a, b = BRING["day"]
            return f"{day}T{a:02d}:00", f"{day}T{b:02d}:00"
        if when == "week":
            return None
        a, b = BRING["night"] if when == "tonight" else BRING["day"]
        if now.tm_hour >= b:                                   # late: what is left of tonight
            a, b = now.tm_hour, 24
        start = max(a, now.tm_hour)
        end = f"{today}T{b:02d}:00" if b < 24 else time.strftime("%Y-%m-%dT00:00", time.localtime(self.clock() + 86400))
        return f"{today}T{start:02d}:00", end

    def hours(self, home, when: str) -> list[dict]:
        """The National Weather Service's hourly forecast in the advice window: the rain chance and temperature, hour by hour."""
        win = self.window(when)
        if win is None:
            return []
        lat, lon = f"{home[0]:.4f}", f"{home[1]:.4f}"
        points = self._fetch(f"{NWS}/points/{lat},{lon}", ttl=7 * 86400)["properties"]
        url = points.get("forecastHourly") or points["forecast"].rstrip("/") + "/hourly"
        out = []
        for p in self._fetch(url).get("properties", {}).get("periods") or []:
            t = (p.get("startTime") or "")[:16]
            if win[0] <= t < win[1]:
                out.append({"t": t, "rain": (p.get("probabilityOfPrecipitation") or {}).get("value") or 0, "temp_f": p.get("temperature")})
        return out

    def uv(self, home, when: str) -> float | None:
        """The day's highest UV index (Open-Meteo; no key). None at night or for the week."""
        if when in ("tonight", "week"):
            return None
        q = {"latitude": f"{home[0]:.3f}", "longitude": f"{home[1]:.3f}", "timezone": "auto", "forecast_days": 2, "daily": "uv_index_max"}
        got = ((self._fetch(f"{METEO}?{urllib.parse.urlencode(q)}") or {}).get("daily") or {}).get("uv_index_max") or []
        i = 1 if when == "tomorrow" else 0
        return float(got[i]) if len(got) > i and got[i] is not None else None

    def advice(self, home, when: str) -> list[str] | None:
        """What to bring or wear (bring()); None when there is nothing to judge it on (the week, or no hourly forecast)."""
        hrs = self.hours(home, when)
        if not hrs:
            return None
        try:
            uv = self.uv(home, when)
        except Exception:                                      # noqa: BLE001 — no UV: the rest of the advice still stands
            uv = None
        return bring(hrs, uv)

    def now(self, home) -> str | None:
        q = {"latitude": f"{home[0]:.3f}", "longitude": f"{home[1]:.3f}", "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "auto",
             "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m"}
        c = (self._fetch(f"{METEO}?{urllib.parse.urlencode(q)}") or {}).get("current") or {}
        if "temperature_2m" not in c:
            return None
        return (f"Right now at home: {c['temperature_2m']:.0f} F, feels like {c.get('apparent_temperature', c['temperature_2m']):.0f} F, "
                f"{WMO.get(c.get('weather_code'), 'mixed sky')}, wind {c.get('wind_speed_10m', 0):.0f} mph (Open-Meteo).")

    def tide_station(self, home) -> dict | None:
        s, key = self._state(), self._home_key(home)
        if (s.get("tide") or {}).get("home") == key:
            return s["tide"]
        stations = self.get(f"{TIDES}/mdapi/prod/webapi/stations.json?type=tidepredictions").get("stations") or []
        best = min(stations, key=lambda x: km_between(home, (float(x["lat"]), float(x["lng"]))), default=None)
        if best is None:
            return None
        rec = {"home": key, "id": best["id"], "name": best["name"]}
        self._remember(tide=rec)
        return rec

    def tides(self, home, day: str = "today") -> str | None:
        st = self.tide_station(home)
        if st is None:
            return None
        date = "today" if day != "tomorrow" else time.strftime("%Y%m%d", time.localtime(self.clock() + 86400))
        q = {"product": "predictions", "datum": "MLLW", "interval": "hilo", "units": "english", "time_zone": "lst_ldt", "format": "json", "station": st["id"]}
        q.update({"date": "today"} if date == "today" else {"begin_date": date, "end_date": date})
        got = self._fetch(f"{TIDES}/api/prod/datagetter?{urllib.parse.urlencode(q)}", ttl=3600).get("predictions") or []
        if not got:
            return None
        parts = [f"{'high' if p['type'] == 'H' else 'low'} {float(p['v']):.1f} ft at {say_clock(p['t'])}" for p in got]
        out = f"Tides {'tomorrow' if day == 'tomorrow' else 'today'} at {st['name']} (NOAA): " + ", ".join(parts) + "."
        if date == "today":                                    # which way it is going now, said outright (Core guessed it wrong, 2026-10-31)
            now = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.clock()))
            nxt = next((p for p in got if p["t"] > now), None)
            if nxt is not None:
                out += f" Right now the tide is {'falling' if nxt['type'] == 'L' else 'rising'}, toward the {'low' if nxt['type'] == 'L' else 'high'} at {say_clock(nxt['t'])}."
        return out

    def buoy(self, home) -> tuple[dict, dict] | None:
        """(station, its latest reading with waves) from the nearest NDBC station that reports wave height."""
        s, key = self._state(), self._home_key(home)
        known = (s.get("buoy") or {})
        if known.get("home") == key:
            reading = self._reading(known["id"])
            if reading is not None:
                return known, reading
        xml = self.get(f"{NDBC}/activestations.xml", raw=True)
        found = []
        for m in re.finditer(r"<station\b([^>]*)>|<station\b([^>]*)/>", xml):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1) or m.group(2) or ""))
            if attrs.get("met") == "y" and attrs.get("lat") and attrs.get("id"):
                found.append(attrs)
        found.sort(key=lambda a: km_between(home, (float(a["lat"]), float(a["lon"]))))
        for a in found[:BUOYS_TRIED]:
            reading = self._reading(a["id"])
            if reading is not None and reading.get("wave_ft") is not None:
                rec = {"home": key, "id": a["id"], "name": a.get("name", a["id"])}
                self._remember(buoy=rec)
                return rec, reading
        return None

    def _reading(self, sid: str) -> dict | None:
        try:
            rows = self._fetch(f"{NDBC}/data/realtime2/{sid.upper()}.txt", raw=True).splitlines()
        except Exception:                                      # noqa: BLE001 — a station with no realtime file is skipped
            return None
        if len(rows) < 3:
            return None
        head = rows[0].lstrip("#").split()
        out: dict = {}
        for row in rows[2:8]:                                  # the newest few readings: the first with each value
            vals = dict(zip(head, row.split()))

            def num(k):
                v = vals.get(k, "MM")
                return None if v in ("MM", "") else float(v)
            for k, name, conv in (("WVHT", "wave_ft", lambda v: v * M_TO_FT), ("DPD", "period_s", float), ("WTMP", "sea_f", c_to_f),
                                  ("WSPD", "wind_mph", lambda v: v * MS_TO_MPH)):
                if name not in out and num(k) is not None:
                    out[name] = conv(num(k))
        return out or None

    def coast(self, home, when: str) -> list[str]:
        lines = []
        tide = self.tides(home, "tomorrow" if when == "tomorrow" else "today")
        if tide:
            lines.append(tide)
        b = self.buoy(home)
        if b:
            st, r = b
            bits = []
            if r.get("wave_ft") is not None:
                bits.append(f"waves {r['wave_ft']:.0f} ft" + (f" every {r['period_s']:.0f} seconds" if r.get("period_s") else ""))
            if r.get("sea_f") is not None:
                bits.append(f"sea {r['sea_f']:.0f} F")
            if r.get("wind_mph") is not None:
                bits.append(f"wind {r['wind_mph']:.0f} mph")
            if bits:
                lines.append(f"Off the coast at NOAA buoy {st['name']}: " + ", ".join(bits) + ".")
        return lines

    # ── the tool ──
    def run(self, args: dict) -> str:
        args = args or {}
        when = args.get("when") if args.get("when") in ("now", "today", "tonight", "tomorrow", "week") else "today"
        home = self.home()
        if home is None:
            return "I don't know where home is yet (no SKIPPY_HOME_LATLON, and the car hasn't spent a night at home). Say so."
        lines, failed = [], []
        try:
            fc, warn = self.nws(home, when)
            lines += [f"ALERT: {w}" for w in warn] + [f"Forecast (National Weather Service) {x}" for x in fc]
        except Exception as e:                                 # noqa: BLE001 — one source down: the others still answer
            failed.append(f"the National Weather Service ({type(e).__name__})")
        if when in ("now", "today"):
            try:
                n = self.now(home)
                if n:
                    lines.insert(0, n)
            except Exception as e:                             # noqa: BLE001
                failed.append(f"Open-Meteo ({type(e).__name__})")
        try:
            items = self.advice(home, when)
            if items is not None:
                at = next((i for i, x in enumerate(lines) if x.startswith("Forecast")), len(lines))
                lines.insert(at, bring_line(items))            # after any alert, ahead of the forecast
        except Exception as e:                                 # noqa: BLE001
            failed.append(f"the hourly forecast ({type(e).__name__})")
        if args.get("coast"):
            try:
                lines += self.coast(home, when)
            except Exception as e:                             # noqa: BLE001
                failed.append(f"NOAA's tides and buoys ({type(e).__name__})")
        if failed and self.log:
            self.log(f"(weather: {', '.join(failed)} not answering)")
        if not lines:
            return "The weather services are not answering right now. Say so plainly."
        return "\n".join(lines) + (f"\n(Not answering: {', '.join(failed)}.)" if failed else "")

    def tools(self):
        return [TOOL]
