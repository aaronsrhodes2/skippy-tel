"""A named body straight into the formula tool: a fixed rule ahead of the cascade and the router in /ask.

sigma-ground b8538b2 folded the one tool chain real questions keep needing ("look the body up, then the formula": 6 of the 12 chains in
October's research log were solar_system_body > escape_velocity) into the tool itself: escape_velocity, schwarzschild_radius, isco_radius,
photon_sphere_radius, hawking_temperature, hawking_evaporation_time and gravitational_time_dilation take `body_name` and look the body up
(JPL's GM, the catalogue's radius, both cited; sigma-ground misc/FOLDED_CHAINS.md). The trained router (mentat-router-v31) predates that
and still plans the chain, or types the mass in from memory. This rule reads the question itself and makes the one call.

It answers only when it reads all of: one quantity cue (escape velocity, Schwarzschild radius / event horizon, photon sphere, ISCO, Hawking
temperature, Hawking evaporation, time dilation at the surface) and exactly ONE named body the catalogue holds. It stands aside (None, its
reason in `miss["fold"]`) when the question gives a mass or a radius of its own ("a planet of 3 Earth masses"), names two bodies, compares, or
asks about an orbit rather than the body ("escape velocity from the solar system at Earth's orbit"): the router and the cascade carry on.
"""
from __future__ import annotations

import re

# names the sigma-ground body catalogue resolves (field/interface/body_data.py); the key is what the tool is called with
BODIES = {"sun": "sun", "the sun": "sun", "moon": "moon", "the moon": "moon", "luna": "moon", "mercury": "mercury", "venus": "venus",
          "earth": "earth", "mars": "mars", "jupiter": "jupiter", "saturn": "saturn", "uranus": "uranus", "neptune": "neptune",
          "pluto": "pluto", "io": "io", "europa": "europa", "ganymede": "ganymede", "callisto": "callisto", "titan": "titan",
          "enceladus": "enceladus", "triton": "triton", "charon": "charon", "ceres": "ceres", "vesta": "vesta", "phobos": "phobos",
          "deimos": "deimos"}
_BODY = re.compile(r"\b(" + "|".join(sorted({k.split()[-1] for k in BODIES}, key=len, reverse=True)) + r")(?:'s|’s|s')?\b", re.I)

# (tool, cue, needs the body's surface): the first cue that matches decides
INTENTS = (
    ("hawking_evaporation_time", re.compile(r"\bevaporat\w*\b.*\bblack hole\b|\bblack hole\b.*\bevaporat\w*|\bhawking\b.*\b(evaporat\w*|lifetime)\b", re.I), False),
    ("hawking_temperature", re.compile(r"\bhawking (temperature|radiation)\b", re.I), False),
    ("photon_sphere_radius", re.compile(r"\bphoton sphere\b", re.I), False),
    ("isco_radius", re.compile(r"\bisco\b|\binnermost stable (circular )?orbit\b", re.I), False),
    ("schwarzschild_radius", re.compile(r"\bschwarzschild radius\b|\bevent horizon\b|\b(collaps\w*|turn\w*|became|become|squeez\w*|compress\w*)\b.*\bblack hole\b", re.I), False),
    ("escape_velocity", re.compile(r"\bescape (velocity|speed)\b|\bbreak free of\b.*\bgravity\b|\bleave\b.*\bfor good\b|\bescape\b.*\bgravity\b", re.I), True),
    ("gravitational_time_dilation", re.compile(r"\b(gravitational )?time dilation\b.*\bsurface\b|\bsurface\b.*\btime dilation\b|\bclocks?\b.*\b(tick|run)s?\b.*\bsurface\b", re.I), True),
)
# a mass or a size of its own, a comparison, two places, or an orbit: not the body's own number, so not this rule's
_GIVEN = re.compile(r"\d\s*(?:x\s*10\^?\s*-?\d+\s*)?(kg|kilograms?|tonnes?|solar mass\w*|earth mass\w*|jupiter mass\w*|m_sun|m☉)\b"
                    r"|\b(mass|radius|diameter) (of|is|=)\s*[\d.]", re.I)
_NOT_THIS = re.compile(r"\bcompared?\b|\bversus\b|\bvs\.?\b|\bratio\b|\bthan\b|\bsolar system\b|\bgalaxy\b|\bmilky way\b|\borbit of\b"
                       r"|\bat (the |its |earth's |a )?(distance|orbit)\b|\bfrom (the )?(orbit|distance)\b|\bneutron star\b", re.I)
_ALTITUDE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(km|kilomet(?:er|re)s?|m|met(?:er|re)s?)\s+(?:above|over|up from|high above|altitude)", re.I)
_ALTITUDE2 = re.compile(r"\baltitude of\s*(\d[\d,]*(?:\.\d+)?)\s*(km|kilomet(?:er|re)s?|m|met(?:er|re)s?)\b", re.I)


def read(question: str) -> tuple[str | None, dict | None, str]:
    """(tool, its arguments, "") or (None, None, why not)."""
    tool = needs_surface = None
    for name, cue, surface in INTENTS:
        if cue.search(question):
            tool, needs_surface = name, surface
            break
    if tool is None:
        return None, None, "no folded quantity named"
    if _GIVEN.search(question):
        return None, None, "the question gives its own mass or size"
    if _NOT_THIS.search(question):
        return None, None, "a comparison, an orbit or a place that is not one body"
    named = []
    for m in _BODY.finditer(question):
        key = BODIES[m.group(1).lower()]
        if key not in named:
            named.append(key)
    if len(named) != 1:
        return None, None, "no body the catalogue holds is named" if not named else f"more than one body named ({', '.join(named)})"
    args: dict = {"body_name": named[0]}
    if needs_surface:
        m = _ALTITUDE.search(question) or _ALTITUDE2.search(question)
        if m:
            value = float(m.group(1).replace(",", ""))
            args["altitude_km"] = value if m.group(2).lower().startswith("k") else value / 1000.0
    return tool, args, ""


def answer(question: str, *, call, miss: dict | None = None) -> dict | None:
    """The /ask result for a folded question, or None (the caller carries on). `call(name, args)` runs a Mentat tool."""
    tool, args, why = read(question)
    if tool is None:
        if miss is not None:
            miss["fold"] = why
        return None
    try:
        got = call(tool, args)
    except Exception as exc:                                      # noqa: BLE001 — a rule that breaks never stops /ask
        if miss is not None:
            miss["fold"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return None
    got = got if isinstance(got, dict) else {}
    if got.get("value") is None:
        # an unknown body or a refusal: the router may still read the question another way
        if miss is not None:
            miss["fold"] = f"{tool} gave no value: {str(got.get('notes') or got.get('error'))[:200]}"
        return None
    plan = f"{tool}(" + ", ".join(f"{k}={v!r}" for k, v in args.items()) + ")"
    out = {k: got.get(k) for k in ("value", "units", "source", "formula", "uncertainty", "provenance_tag", "display", "notes")}
    out["error"], out["clarification"], out["assumed"] = None, None, got.get("assumed")
    out["interpreted_as"] = plan
    out["interpreter"] = "fold rule"
    out["inputs"] = {"route": "fold", "chain": [{"tool": tool, "args": args, "value": got.get("value"), "units": got.get("units"),
                                                 "source": got.get("source"), "formula": got.get("formula")}],
                     "understood": [{"tool": tool, "params": args}]}
    return out
