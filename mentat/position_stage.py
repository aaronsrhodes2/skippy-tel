"""Where is a planet, the Sun or the Moon at a time? A fixed rule ahead of the cascade and the router in /ask.

sigma-ground's `body_position` (2026-10-26) answers it: JPL DE440 up to today (labelled known), its own N-body forecast after (labelled a
prediction, with JPL's extrapolation and the measured error beside it). The trained router (mentat-router-v31) was trained before that tool
existed, so on 2026-10-29 "Where will Mars be on March 1, 2027, as seen from Earth?" went to `solar_system_body`: Mars's fact sheet, not a
position. The cascade had no rule for it either, and the keyword translator, which does know the tool, only runs when both decline.

So this rule reads the question itself: a known body (Sun, Moon, Mercury to Pluto), a position word (where, position, right ascension,
declination, how far, distance, in the sky) and a time (a date, "now", "tonight", "tomorrow", "in 3 years", "in 2030"). All three, or no
rule: it never guesses. "Where is Mars?" with no time means now. The centre is the Earth (people ask from here) unless the question says
"from the Sun", "heliocentric" or "barycentre"; Earth's own position is measured from the Sun.

Anything it can't read returns None with its reason in `miss["position"]`, and /ask carries on to the cascade and the router as before.
"""
from __future__ import annotations

import datetime
import re

BODIES = {"sun": "sun", "moon": "moon", "luna": "moon", "mercury": "mercury", "venus": "venus", "earth": "earth", "mars": "mars",
          "jupiter": "jupiter", "saturn": "saturn", "uranus": "uranus", "neptune": "neptune", "pluto": "pluto"}
_BODY = re.compile(r"\b(" + "|".join(BODIES) + r")\b", re.I)
_CUE = re.compile(r"\bwhere(?:'s| is| was| will| would)?\b|\bposition\b|\bright ascension\b|\bdeclination\b|\bra\s*(?:and|/|&)\s*dec\b"
                  r"|\bhow far\b|\bdistance\b|\bin the sky\b|\blocated\b|\bephemeris\b|\bcoordinates\b", re.I)
# Questions about a body's nature, not its place (its orbit's size, its mass, a trip to it): not this rule's.
_NOT_POSITION = re.compile(r"\b(mass|weigh|gravity|escape|orbital period|period|radius|diameter|temperature|atmosphere|density|travel|fly|"
                           r"rocket|delta.?v|transfer|light (?:take|travel)|signal)\b", re.I)
_PRESENT = re.compile(r"\b(where(?:'s| is)|right now|currently|at the moment|at present|now|today|tonight|this evening)\b", re.I)

MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                                      "november", "december"), 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
_MON = r"(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?)\b")
_JD = re.compile(r"\bJD\s*(\d{7}(?:\.\d+)?)\b", re.I)
_MDY = re.compile(_MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON + r",?\s+(\d{4})\b", re.I)
_MY = re.compile(r"\b" + _MON + r"\s+(\d{4})\b", re.I)
_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_REL = re.compile(r"\bin\s+(a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(day|week|month|year)s?\b"
                  r"|\b(a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(day|week|month|year)s?\s+ago\b", re.I)
_YEAR = re.compile(r"\b(?:in|during|for|by)\s+(?:the\s+year\s+)?(\d{4})\b", re.I)
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_DAYS = {"day": 1, "week": 7, "month": 30.436875, "year": 365.25}


def read_when(question: str, now: datetime.datetime) -> tuple[str, str | None] | None:
    """(the time as body_position reads it, what was assumed or None), or None when the question names no time."""
    q = question
    if m := _JD.search(q):
        return f"JD {m.group(1)}", None
    if m := _ISO.search(q):
        return m.group(1).replace(" ", "T"), None
    if m := _MDY.search(q):
        return _date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    if m := _DMY.search(q):
        return _date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    if m := _SLASH.search(q):       # month/day/year, the Captain's way of writing a date
        return _date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    if m := _MY.search(q):
        d = _date(int(m.group(2)), MONTHS[m.group(1).lower()], 1)
        return d and (d[0], "the 1st of the month (only the month was given)")
    if m := _REL.search(q):
        n, unit, sign = (m.group(1), m.group(2), 1) if m.group(1) else (m.group(3), m.group(4), -1)
        n = _WORDS.get(n.lower()) or int(n)
        t = now + sign * datetime.timedelta(days=n * _DAYS[unit.lower()])
        return t.strftime("%Y-%m-%dT%H:%M:%SZ"), None
    if re.search(r"\btomorrow\b", q, re.I):
        return (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d"), None
    if re.search(r"\byesterday\b", q, re.I):
        return (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d"), None
    if m := _YEAR.search(q):
        return f"{m.group(1)}-01-01", "1 January of that year (only the year was given)"
    if _PRESENT.search(q):
        return "now", None
    return None


def _date(y: int, mo: int, d: int) -> tuple[str, None] | None:
    try:
        return datetime.date(y, mo, d).isoformat(), None
    except ValueError:
        return None


def read_center(question: str, body: str) -> str:
    q = question.lower()
    if re.search(r"barycent", q):
        return "ssb"
    if body == "earth" or re.search(r"\bfrom (?:the )?sun\b|\bheliocentric\b|\baround the sun\b|\bsun-centred\b|\bsun-centered\b", q):
        return "sun"
    return "earth"


def read(question: str, now: datetime.datetime | None = None) -> tuple[dict | None, str, str | None]:
    """(body_position's arguments or None, why not, what was assumed)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not _CUE.search(question):
        return None, "no position word", None
    if _NOT_POSITION.search(question):
        return None, "asks about the body, not where it is", None
    named = []
    for m in _BODY.finditer(question):
        b = BODIES[m.group(1).lower()]
        before = question[max(0, m.start() - 16):m.start()].lower()
        if b in ("earth", "sun") and re.search(r"\b(from|seen from|around|of the)\s+(?:the\s+)?$", before):
            continue                                              # the centre ("from Earth"), not the body asked about
        if b not in named:
            named.append(b)
    if len(named) != 1:
        return None, ("no planet, Sun or Moon named" if not named else f"more than one body named ({', '.join(named)})"), None
    when = read_when(question, now)
    if when is None:
        return None, "no time named", None
    body = named[0]
    center = read_center(question, body)
    if body == "sun" and center == "sun":
        center = "ssb"
    return {"body_name": body, "when": when[0], "central_body": center}, "", when[1]


def answer(question: str, *, call, miss: dict | None = None, now: datetime.datetime | None = None) -> dict | None:
    """The /ask result for a position question, or None (the caller carries on). `call(name, args)` runs a Mentat tool."""
    args, why, assumed = read(question, now)
    if args is None:
        if miss is not None:
            miss["position"] = why
        return None
    try:
        got = call("body_position", args)
    except Exception as exc:                                      # noqa: BLE001 — a rule that breaks never stops /ask
        if miss is not None:
            miss["position"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return None
    got = got if isinstance(got, dict) else {}
    plan = "body_position(" + ", ".join(f"{k}={v!r}" for k, v in args.items()) + ")"
    out = {k: got.get(k) for k in ("value", "units", "source", "formula", "uncertainty", "provenance_tag", "display", "notes")}
    if out["value"] is None:
        # The tool's own reason (a date outside DE440, a body it doesn't hold) is the honest answer; a router would only answer something else.
        out["error"] = got.get("notes") or "body_position gave no value"
    else:
        out["error"] = None
    out["clarification"], out["assumed"] = None, assumed
    out["interpreted_as"] = plan
    out["interpreter"] = "position rule"
    out["inputs"] = {"route": "position", "chain": [{"tool": "body_position", "args": args, "value": got.get("value"), "units": got.get("units"),
                                                     "source": got.get("source"), "formula": None}],
                     "understood": [{"tool": "body_position", "params": args}]}
    return out
