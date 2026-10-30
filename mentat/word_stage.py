"""The word-problem stage of /ask (opt-in: MENTAT_WORD_MODEL; unset, nothing here runs).

The last stop before a plain decline (mentat/app.py `_ask`): the cascade, the router and its fallback all missed and the keyword translator has nothing but its
generic blurb. A question that hands over its own numbers ("80 years at 70 beats per minute", "100 km apart, one at 60 km/h and one at 40 km/h") is still
arithmetic, and sigma-ground has a tool for arithmetic with units: `evaluate_quantity` (pint). The model's whole job here is to READ: copy the numbers the question
gives into ONE expression. It never computes, and it may not bring a number of its own:

  * every numeric literal in the expression must be in the question (digits, a number word, a scale word such as "billion", a percentage's 100; the structural
    1, 2, 3 and 0.5 are free). A conversion factor (365 days, 60 s) is written as a unit, never as a number; a missing fact (the height of Everest) is a refusal,
    not a recollection. An expression that breaks this, or that evaluate_quantity cannot use, is sent back with the problem named (up to ATTEMPTS tries in all).
  * the value is `evaluate_quantity`'s, its provenance DERIVED, and the answer says in `notes` and `interpreted_as` that the numbers are the question's own and
    what expression was read from it, so a wrong reading is visible, not hidden in a number.

A miss (no model, no expression, "cannot", a number not in the question, a tool error, a timeout) returns None and the caller declines plainly, exactly as
before this stage existed; `answer(..., miss=d)` writes the reason into `d`. So the stage can add answers; it cannot take one away.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.request

MODEL = os.environ.get("MENTAT_WORD_MODEL", "").strip()
TIMEOUT_S = float(os.environ.get("MENTAT_WORD_TIMEOUT", "60"))
ATTEMPTS = 3                                                    # a later one only after the first was sent back with its problem named

_SYSTEM = """You turn a word problem into ONE arithmetic expression. You never compute the answer and you never use a number that is not in the question.

Reply with JSON: {"asks_for": <what quantity the question asks for, in a few words>, "question_gives_every_fact": <true only if every number needed to get that quantity is stated in the question>, "expression": <string or null>, "to_units": <string or null>, "cannot": <string or null>}.

Rules:
- Write the expression for the Python library `pint`: numbers with spelled-out units (km, m, cm, hour, minute, second, year, day, kg, g, L, W, J), joined by * / + - and **. Parentheses where needed. A rate is a quotient: "70 beats per minute" is 70 / minute (a count word such as beats, bananas, people, pages or dollars is NOT a unit: leave it out and treat it as a plain number).
- Every number in the expression must be a number the question states (digits or number words; "billion" is 1e9, "percent" divides by 100). Nothing from memory: not the height of a mountain, not the speed of light, not the length of a year. If the question needs a number it does not give, set "cannot" to what is missing and "expression" to null.
- Never write a unit conversion as a number (60 for minutes, 365 for days, 1000 for km): write the unit instead and let the arithmetic convert it ("1 year", "3 hour").
- A divisor that has a unit goes in parentheses: 400 m / (12 m/second), never 400 m / 12 m/second.
- Units carry their own conversion factors, so you never need one: second, minute, hour, day, week, year; mm, cm, m, km; mL, L; g, kg; W, J, kWh. "5 billion seconds" is 5e9 second, and asking for years is to_units "year".
- "to_units" is the unit the question asks the answer in (for example "hour" for "how many hours", "km" for "how far"), or "dimensionless" when the answer is a plain number (a count, a ratio, a probability).
- If the question is not arithmetic on given quantities (nonsense, an opinion, a fact to look up, a puzzle that is not arithmetic), set "expression" to null and say why in "cannot".

Examples:
Q: A car goes 90 km/h for 2.5 hours. How far does it go?
{"asks_for": "distance travelled", "question_gives_every_fact": true, "expression": "90 km/hour * 2.5 hour", "to_units": "km", "cannot": null}
Q: A bakery sells 12 loaves an hour. How many loaves in 5 days of 8 hours?
{"asks_for": "number of loaves", "question_gives_every_fact": true, "expression": "12 / hour * 5 * 8 hour", "to_units": "dimensionless", "cannot": null}
Q: A cat sleeps 16 hours a day. How many hours does it sleep in 3 years?
{"asks_for": "hours slept", "question_gives_every_fact": true, "expression": "16 hour/day * 3 year", "to_units": "hour", "cannot": null}
Q: How many seconds does it take a runner at 10 m/s to cross a bridge?
{"asks_for": "time to cross", "question_gives_every_fact": false, "expression": null, "to_units": null, "cannot": "the length of the bridge is not given"}
Q: What is the colour of Tuesday?
{"asks_for": "a colour", "question_gives_every_fact": false, "expression": null, "to_units": null, "cannot": "not a question about quantities"}
Q: A cat eats 3 cans of food a day. How many calories does it eat in a week?
{"asks_for": "calories", "question_gives_every_fact": false, "expression": null, "to_units": null, "cannot": "the calories in a can are not given"}"""

_SCHEMA = {"type": "object",
           "properties": {"asks_for": {"type": "string"}, "question_gives_every_fact": {"type": "boolean"},
                          "expression": {"type": ["string", "null"]}, "to_units": {"type": ["string", "null"]}, "cannot": {"type": ["string", "null"]}},
           "required": ["asks_for", "question_gives_every_fact", "expression", "to_units", "cannot"]}

_NUMBER = re.compile(r"(?<![A-Za-z_\d.])(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_WORD_VALUES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
                "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
                "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "dozen": 12, "half": 0.5,
                "twice": 2, "double": 2, "triple": 3, "quarter": 0.25, "third": 1 / 3}
_SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12, "k": 1e3}
_SIZE_QUESTION = re.compile(r"\b(how (many|much|long|far|fast|tall|high|old|big|heavy)|by how)\b", re.I)
_FREE = (0.5, 1.0, 2.0, 3.0)                                     # a square, a cube, a half, a "per"


def enabled() -> bool:
    return bool(MODEL)


def _literals(text: str) -> list[float]:
    return [float(m.group()) for m in _NUMBER.finditer(text)]


def _allowed(question: str) -> list[float]:
    """Every number the question licenses: its digits, its number words, a scale word applied to a digit, the scales themselves, 100 for a percentage."""
    q = re.sub(r"(?<=\d),(?=\d{3}\b)", "", question)                       # 384,000 -> 384000
    nums = _literals(q)
    words = set(re.findall(r"[a-z]+", q.lower()))
    nums += [float(v) for w, v in _WORD_VALUES.items() if w in words]
    out = list(nums) + list(_FREE)
    for w, s in _SCALES.items():
        if w in words or re.search(rf"\d\s*{w}\b", q, re.I):
            out += [s, 1 / s, 10.0, float(round(math.log10(s)))] + [n * s for n in nums]
    if "%" in q or "percent" in words or "per cent" in q.lower():
        out += [100.0]
    return out


def _unlicensed(expression: str, question: str) -> list[float]:
    """The numeric literals of `expression` that the question does not state."""
    allowed = _allowed(question)
    bad = []
    for lit in _literals(expression):
        if not any(abs(lit - a) <= 1e-9 * max(1.0, abs(a)) for a in allowed):
            bad.append(lit)
    return bad


def _ask_model(messages: list[dict], ollama_url: str) -> dict:
    body = {"model": MODEL, "messages": messages, "stream": False, "think": False, "format": _SCHEMA, "options": {"temperature": 0, "seed": 0, "num_predict": 400}}
    req = urllib.request.Request(ollama_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        content = json.loads(r.read())["message"]["content"]
    plan = json.loads(content)
    if not isinstance(plan, dict):
        raise ValueError("the model's reply was not a JSON object")
    return plan


def _missed(miss: dict | None, reason: str, **more) -> None:
    if miss is not None:
        miss["reason"] = reason
        miss.update(more)


def answer(question: str, *, call, ollama_url: str, guard=None, miss: dict | None = None) -> dict | None:
    """The /ask result dict for a question the model could read as one expression and `evaluate_quantity` computed, or None (the caller declines).
    `call(name, args)` runs a Mentat tool; `guard(name, args)` returns a reason to refuse an expression that looks like code (else None)."""
    if not (MODEL and ollama_url):
        _missed(miss, "word stage off")
        return None
    if not (_literals(question) or any(w in _WORD_VALUES for w in re.findall(r"[a-z]+", question.lower()))):
        _missed(miss, "the question states no numbers")
        return None
    messages = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": f"Q: {question.strip()}"}]
    for attempt in range(ATTEMPTS):
        try:
            plan = _ask_model(messages, ollama_url)
        except Exception as exc:                                    # noqa: BLE001 — a model that is down, slow or incoherent never stops /ask
            _missed(miss, f"{type(exc).__name__}: {str(exc)[:200]}")
            return None
        expression = (plan.get("expression") or "").strip()
        if expression and plan.get("question_gives_every_fact") is False:
            _missed(miss, f"a fact the question does not give: {str(plan.get('cannot') or plan.get('asks_for'))[:300]}", plan=plan)
            return None
        if not expression:
            cannot = str(plan.get("cannot") or "no expression")
            _missed(miss, f"cannot: {cannot[:300]}", plan=plan)
            if attempt < ATTEMPTS - 1 and re.search(r"conver|number of (seconds|minutes|hours|days|weeks|months|years|millimet|centimet|meters|metres|kilomet|grams|liters)", cannot, re.I):
                messages += [{"role": "assistant", "content": json.dumps(plan)},
                             {"role": "user", "content": "Units convert themselves: write the quantity with its unit (3 week, 5 hour) and set to_units to the unit asked for. "
                                                          "Write the expression."}]
                continue
            return None
        why = guard("evaluate_quantity", {"expression": expression}) if guard else None
        if why:
            _missed(miss, why, plan=plan)
            return None
        bad = _unlicensed(expression, question)
        if bad:
            shown = ", ".join(f"{b:g}" for b in bad)
            problem = (f"The number(s) {shown} are not stated in the question. Use only the question's numbers. Write a unit conversion as a unit "
                       f"(minute, hour, day, year, km, m, mm, mL, L), never as a number; if a needed number is missing, set expression to null and say what is missing.")
            _missed(miss, f"number(s) not in the question: {shown}", plan=plan)
        else:
            to_units = (plan.get("to_units") or "").strip() or "dimensionless"
            try:
                got = call("evaluate_quantity", {"expression": expression, "to_units": to_units})
            except Exception as exc:                                # noqa: BLE001 — BadArguments and the like: a miss, not a crash
                _missed(miss, f"{type(exc).__name__}: {str(exc)[:200]}", plan=plan)
                return None
            if isinstance(got, dict) and got.get("value") is not None:
                if isinstance(got["value"], (int, float)) and got["value"] < 0 and _SIZE_QUESTION.search(question):
                    _missed(miss, "negative size", plan=plan)
                    messages += [{"role": "assistant", "content": json.dumps(plan)},
                                 {"role": "user", "content": "The result is negative, but the question asks for a size (how many, how long, how far, by how much). Swap the order "
                                                              "of the subtraction so the larger comes first."}]
                    continue
                break
            note = str((got or {}).get("notes") or (got or {}).get("error"))[:300]
            problem = (f"evaluate_quantity could not use that: {note} Fix the expression. A divisor that has a unit must be in parentheses "
                       f"(400 m / (12 m/second), not 400 m / 12 m/second). Set to_units to the unit the question asks for, or \"dimensionless\" for a plain number; "
                       f"count words (pages, beats, people, dollars) are not units, leave them out.")
            _missed(miss, f"tool error: {note}", plan=plan)
        messages += [{"role": "assistant", "content": json.dumps(plan)}, {"role": "user", "content": problem}]
    else:
        return None
    if miss is not None:
        miss.clear()                                               # an answer after a corrected attempt carries no miss
    read = (got.get("interpreted_as") or expression)
    out = {k: got.get(k) for k in ("value", "units", "source", "formula", "uncertainty", "provenance_tag", "display")}
    out["notes"] = (f"A word problem: the model only read the question's own numbers into the expression `{expression}`; the arithmetic is Mentat's "
                    f"(pint). If that is not the question you asked, ask it with the quantities spelled out.")
    out["error"], out["clarification"], out["assumed"] = None, None, None
    out["interpreted_as"] = read
    out["interpreter"] = f"word problem {MODEL}"
    out["inputs"] = {"route": "word_problem", "expression": expression, "to_units": to_units, "attempts": attempt + 1, "plan": plan}
    return out
