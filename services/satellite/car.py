"""The Captain's Tesla, for Skippy at home: the portal chat's `car_status` as a tool the house voice can call (the Captain, 2026-11-04: asked "What is my car's charge level
right now?" the house said "I do not have access to your car's systems"; "You are wrong, you DO have access. Missed tool call.").

READ ONLY. It asks skippy-core's car reader (core/car, tailnet :7351), which keeps the latest values TeslaMate publishes: asking never wakes the car, never calls Tesla and
never signs in to anything (docs/tesla.md). Nothing here can lock, heat, charge or open the car; that stays purple Skippy's `car_charge`, behind its own rules. The tool
returns MATERIAL in plain words and units (percent, miles, F, kilowatts, hours; no markup, no link, no coordinates); the brain gives the takeaway.

Home: where the house has learned home to be (weather.home_of: SKIPPY_HOME_LATLON, else where the car spends the night) and the morning routine's test (daily.HOME_NEAR_KM);
a place TeslaMate has named (its geofence) is said when the car is elsewhere. Range: the car's own estimate when it has one, else its rated range (what its screen shows),
in miles by Mentat's factor (daily.KM_TO_MI), temperatures by weather.c_to_f's (Mentat's too): PLATINUM 21, Skippy computes no physics.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime

import weather as weather_mod
from daily import CAR_URL, HOME_NEAR_KM, KM_TO_MI, http_get_json, km_between

ABOUT = ("charge", "where", "locks", "climate", "all")
STALE_S = 900                       # an awake car the reader has not heard from for this long: say how long ago

TOOL = {"name": "car_status",
        "description": "The Captain's Tesla (T3-P0), READ ONLY, as TeslaMate last saw it: battery percent and charge limit, range in miles, plugged in or charging (kilowatts, "
                       "time to the limit), home or away, locked and what is open, sentry mode, inside and outside temperature. Use it for ANY question about his car: its charge, "
                       "battery or range, whether it is plugged in, charging, locked, awake or at home, where it is. Asking never wakes the car. It cannot lock, unlock, heat, "
                       "charge or open the car.",
        "input_schema": {"type": "object", "properties": {
            "about": {"type": "string", "enum": list(ABOUT), "description": "What he asked about: charge (battery, range, plugged in), where (home or away), locks, climate, or all (default)."}}}}

PROMPT = (" For his car (its charge, battery or range, whether it is plugged in, charging, locked or at home, where it is), call car_status: it is read only, never wakes the car, "
          "and gives TeslaMate's last report. Answer in a sentence or two with only what he asked, using only the numbers the tool gave (the percent, the miles of range, whether it is "
          "plugged in), never the whole card. A sleeping or offline car still has a last reading: give it and say how old it is, and never say you cannot reach the car. From this voice you "
          "cannot lock, unlock, heat, charge or open the car: say so if he asks (only the private Skippy on makerportal.live can start or stop its charging), and never say you "
          "have no access to his car.")

NO_READER = ("The car's data comes through a reader on skippy-core, and it is not answering right now, so I can't see the car. Say so plainly; do not guess its charge, range "
             "or where it is.")
NO_CAR = "TeslaMate has no car yet (nobody has signed in to Tesla there), so there is nothing to read. Say so plainly."
SIGNED_OUT = ("Tesla signed TeslaMate out, so nothing has been logged since and these are OLD values; he has to sign in again on TeslaMate's page (do not try to sign in "
              "for him). Say so first.")
STATE_WORDS = {"asleep": "asleep", "offline": "asleep or out of signal", "online": "awake", "driving": "driving", "charging": "awake and charging",
               "suspended": "about to fall asleep", "updating": "installing a software update"}
PLUG_WORDS = {"Complete": "Plugged in, charging is complete", "Stopped": "Plugged in, charging is stopped", "NoPower": "Plugged in, but no power is arriving",
              "Starting": "Plugged in, charging is starting"}
QUIET = ("asleep", "offline", "suspended")      # the car is not talking: what is shown is what it said last


# ── his words, routed: the question is about HIS car and one of its states (Core said "I do not have access" and called nothing, 2026-11-04) ──
# "the Tesla Model Y" and "Tesla stock" are not his car; a command ("lock the car", "start charging") is not a question this tool answers, the model says it can't.
CAR = re.compile(r"\b(?:(?:my|the|his|our) (?:tesla|car|model 3)\b(?!\s+(?:model\s+[ysx]\b|cybertruck|roadster|semi|stock|shares|company))|t3[- ]?p0\b)", re.I)
TOPICS = [("charge", re.compile(r"\b(?:charg\w*|batter\w*|range|plug\w*|unplug\w*|juice|state of charge|percent|miles? (?:left|of range|to go)|how far (?:can|will|could))\b", re.I)),
          ("where", re.compile(r"\b(?:where|park\w*|at home|home|away|garage|location|located)\b", re.I)),
          ("locks", re.compile(r"\b(?:lock\w*|unlock\w*|doors?|windows?|trunk|frunk|sentry|secure[d]?|left open)\b", re.I)),
          ("climate", re.compile(r"\b(?:temperature|degrees|hot|cold|warm|climate|a/?c|air conditioning|heat\w*)\b", re.I))]
GENERAL = re.compile(r"\b(?:how'?s|how is|how are|status|doing|check on|check the|update on|what'?s up with|tell me about|report on)\b", re.I)
COMMAND = re.compile(r"^(?:lock|unlock|honk|flash|wake|defrost|summon|vent)\b"
                     r"|^(?:start|stop|begin|pause|resume|schedule|cancel)\s+(?:the\s+)?(?:charging|charge|heating|cooling|climate|a/?c|pre-?conditioning|sentry)\b"
                     r"|^(?:open|close)\s+(?:the\s+|my\s+)?(?:trunk|frunk|doors?|windows?|charge port|port)\b"
                     r"|^turn\s+(?:on|off|up|down)\s+(?:the\s+|my\s+)?(?:climate|a/?c|air|heat|heater|sentry)\b|^turn\s+(?:the\s+|my\s+)?(?:climate|a/?c|heat|heater|sentry)\s+(?:on|off)\b"
                     r"|^(?:set|change)\s+(?:the\s+|my\s+)?(?:charge limit|climate|temperature)\b"
                     r"|^(?:charge|plug in|unplug|pre-?condition|warm up|cool down|heat|cool)\s+(?:the\s+|my\s+)?(?:car|tesla|it|model 3)\b", re.I)


def _plain(heard: str) -> str:
    """The words without the wake word, the lead-ins ("please", "can you") and the punctuation, lower case."""
    w = re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", (heard or "").lower()).strip(" ?!.,")
    w = re.sub(r"^(?:(?:please|just|and|then|so|ok|okay|hey)[ ,]+)+", "", w)
    return re.sub(r"^(?:(?:can|could|would|will) you(?: please)?|i (?:want|need|would like) you to|go ahead and)[ ,]+", "", w)


def pre_route(heard: str) -> dict | None:
    """car_status's arguments for a plain question about his car ("what's my car's charge level", "is the Tesla plugged in", "is the car home"); None = the model decides."""
    w = _plain(heard)
    if not CAR.search(w) or COMMAND.match(w):
        return None
    topics = [name for name, rx in TOPICS if rx.search(w)]
    if not topics and not GENERAL.search(w):
        return None
    return {"about": topics[0] if len(topics) == 1 else "all"}


# ── the car as words ──
def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _first(*vals):
    return next((v for v in map(_num, vals) if v is not None), None)


def age_words(seconds: float) -> str:
    m = max(0, int(seconds)) // 60
    if m < 2:
        return "a minute"
    if m < 60:
        return f"{m} minutes"
    h, m = divmod(m, 60)
    if h >= 48:
        return f"{h // 24} days"
    return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minute{'s' if m != 1 else ''}" if m and h < 6 else "")


def _since(text) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def charge_line(car: dict) -> str:
    level, limit = _num(car.get("battery_level")), _num(car.get("charge_limit_soc"))
    rng = _first(car.get("est_battery_range_km"))
    rated = rng is None
    if rated:
        rng = _first(car.get("rated_battery_range_km"), car.get("ideal_battery_range_km"))
    bits = []
    if level is not None:
        bits.append(f"Battery {round(level)} percent" + (f", charge limit {round(limit)} percent" if limit is not None else ""))
    if rng is not None:
        bits.append(f"about {round(rng * KM_TO_MI)} miles of {'rated ' if rated else ''}range")
    head = (", ".join(bits) + ".") if bits else "The battery level is not known."
    cs, plugged = str(car.get("charging_state") or ""), car.get("plugged_in")
    power, to_full = _num(car.get("charger_power")), _num(car.get("time_to_full_charge"))
    if cs == "Charging" or car.get("state") == "charging":
        plug = "Charging" + (f" at {round(power, 1):g} kilowatts" if power else "")
        if to_full:
            plug += f", about {age_words(round(to_full * 3600))} to the limit"
    elif plugged:
        plug = PLUG_WORDS.get(cs, "Plugged in, not charging")
    elif plugged is False:
        plug = "Not plugged in"
    else:
        plug = ""
    return head + (f" {plug}." if plug else "")


def locks_line(car: dict) -> str:
    out = []
    if car.get("locked") is not None:
        out.append("Locked." if car["locked"] else "Unlocked.")
    opened = [label for key, label in (("doors_open", "a door"), ("windows_open", "a window"), ("trunk_open", "the trunk"), ("frunk_open", "the front trunk")) if car.get(key)]
    out.append(f"Open: {', '.join(opened)}." if opened else "Nothing is open.")
    if car.get("sentry_mode") is not None:
        out.append("Sentry mode is on." if car["sentry_mode"] else "Sentry mode is off.")
    return " ".join(out)


def climate_line(car: dict) -> str:
    inside, outside = _num(car.get("inside_temp")), _num(car.get("outside_temp"))
    if inside is None and outside is None:
        return ""
    bits = ([f"inside {round(weather_mod.c_to_f(inside))} F"] if inside is not None else []) + ([f"outside {round(weather_mod.c_to_f(outside))} F"] if outside is not None else [])
    text = ", ".join(bits)
    return text[0].upper() + text[1:] + "." + (" The climate control is on." if car.get("is_climate_on") else "")


class Car:
    def __init__(self, env: dict | None = None, get=http_get_json, clock=time.time, daily_state=None, log=None):
        self.env = env if env is not None else dict(os.environ)
        self.url = (self.env.get("SKIPPY_CAR_URL") or CAR_URL).rstrip("/")
        self.get, self.clock, self.daily_state, self.log = get, clock, daily_state, log

    def home(self):
        return weather_mod.home_of(self.env, self.daily_state)

    def where_line(self, car: dict) -> str:
        shift, speed = car.get("shift_state"), _num(car.get("speed"))
        if shift in ("D", "R", "N") or (speed or 0) > 0:
            return "Driving" + (f", at {round(speed * KM_TO_MI)} miles per hour" if speed else "") + "."
        place = str(car.get("geofence") or "").strip()
        try:
            here = (float(car["latitude"]), float(car["longitude"]))
        except (KeyError, TypeError, ValueError):
            here = None
        home = self.home()
        if place.lower() in ("home", "house") or (home is not None and here is not None and km_between(home, here) <= HOME_NEAR_KM):
            return "Parked at home."
        if home is None and here is not None and not place:
            return "It is parked, but I do not know where home is yet, so I can't say whether it is home."
        if here is None and not place:
            return "I can't tell where it is."
        return "Away from home" + (f", at {place}" if place else "") + "."

    def fresh(self, car: dict, now: float) -> list[str]:
        """How old what was just said is, when the car is not talking: the readings stand, and he is told their age (a sleeping car is not out of reach)."""
        if str(car.get("state") or "") in QUIET:
            since = _since(car.get("since"))
            return ["These are its last readings" + (f", from {age_words(now - since)} ago, when it went quiet" if since else ", from when it went quiet")
                    + "; asking never wakes it. Give the readings and say how old they are."]
        heard = _num(car.get("updated_s"))
        return [f"The last word from the car was {age_words(heard)} ago."] if heard is not None and heard > STALE_S else []

    def lines(self, car: dict, about: str) -> list[str]:
        state, now = str(car.get("state") or ""), self.clock()
        out = [f"{car.get('name') or 'The car'} is {STATE_WORDS.get(state, state or 'in a state I cannot read')}."]
        if about in ("charge", "all"):
            out.append(charge_line(car))
        if about in ("where", "all"):
            out.append(self.where_line(car))
        if about in ("locks", "all"):
            out.append(locks_line(car))
        if about in ("climate", "all"):
            out.append(climate_line(car))
        if about == "all":
            if _num(car.get("odometer")) is not None:
                out.append(f"Odometer {round(car['odometer'] * KM_TO_MI)} miles.")
            if car.get("update_available"):
                out.append("A software update is waiting.")
        return [x for x in out + self.fresh(car, now) if x]

    def run(self, args: dict) -> str:
        about = (args or {}).get("about")
        about = about if about in ABOUT else "all"
        try:
            got = self.get(f"{self.url}/car", 10)
        except Exception as e:                                    # noqa: BLE001 — the reader, the tailnet or skippy-core is away: said plainly, never guessed
            if self.log:
                self.log(f"(car: the reader did not answer: {type(e).__name__})")
            return NO_READER
        cars = [c for c in (got.get("cars") or []) if isinstance(c, dict)] if isinstance(got, dict) else []
        if not cars:
            return NO_CAR
        return " ".join(([SIGNED_OUT] if got.get("signed_in") is False else []) + self.lines(cars[0], about))

    def tools(self) -> list[dict]:
        return [TOOL]
