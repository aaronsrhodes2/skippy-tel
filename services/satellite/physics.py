"""Mentat for Skippy at home: the portal chat's `ask_mentat`, as a tool the house voice can call (the Captain, 2026-11-04: the house chat "has no idea what it can reach";
the portal's Skippy has Mentat and the house had been told it was out of reach).

PLATINUM 21: Mentat does the physics. Every physical number the house says comes from here with its source; the brain never calculates, converts or recalls one. The
question goes to Mentat in his own words (it finds the calculation itself); the tool returns MATERIAL in plain words: the value rounded the way a person says it, its
units, how Mentat read the question (so an input it filled in shows), its source in a few words and its provenance. Nothing in it is markup, a link or a citation mark.

Mentat is ONE service, on skippy-core (tailnet :7350, Qwen and its router; docs/portal/README.md). Its answers take 0.1 s (a rule), a second or two (the router) and, when the
router's stock fallback or a web search runs, up to a minute, so a turn waits ASK_TIMEOUT_S and then says so. It never fills in an input by itself: when the question leaves
one out it SUGGESTS a value and computes nothing, and only his "yes" sends it back (`use_suggestion`; the brain keeps no tool results, so this module keeps the
suggestion for a few minutes, per room, and sends it exactly as Mentat made it, never anything the model wrote).
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request

MENTAT_URL = "http://skippy-core:7350"          # Mentat on skippy-core, tailnet only (SKIPPY_MENTAT_URL, else MENTAT_URL, overrides)
ASK_TIMEOUT_S = 75.0                            # a question: Mentat's own call limit is 90 s; he is standing in the room, so the house gives up sooner
KEEP_SUGGESTION_S = 300                         # how long his "yes" still means the values Mentat suggested
SIGNIFICANT = 5                                 # digits kept when a value is said aloud (a display rounding, never a calculation)

TOOL = {"name": "ask_mentat",
        "description": "Mentat, the Captain's physics and chemistry engine (sigma-ground): the ONLY source of a physical number. For any physics, chemistry, astronomy, unit "
                       "conversion or maths question that needs a number (an escape velocity, a boiling point, a molar mass, how long light takes, how many ounces in a cup), "
                       "pass his question in his own words with every number and unit he said. Don't solve it, convert units or recall a value yourself: Mentat finds the "
                       "calculation. It answers with a value, units and source; or, when the question leaves out an input, it SUGGESTS a value and computes nothing (ask him; "
                       "only after he says yes, call again with use_suggestion=true); or says it cannot model the question. NOT for places, driving distances, routes, prices, "
                       "news or people: it is a physics engine and can answer such a question with an unrelated calculation.",
        "input_schema": {"type": "object", "properties": {
            "question": {"type": "string", "description": "His question, in his own words, with every number and unit he gave."},
            "use_suggestion": {"type": "boolean", "description": "Only after he said yes to the values Mentat suggested for the last question: asks again with exactly those values."}},
            "required": ["question"]}}

PROMPT = (" For a physics, chemistry, astronomy or unit-conversion NUMBER (an escape velocity, a boiling point, a molar mass, how long light takes, how many ounces in a cup), "
          "call ask_mentat with his question in his own words, numbers and units included: Mentat is the calculator, you never are. Say the number the way the tool gave it "
          "(said the way a person would), say it came from Mentat and name the source exactly as the Source line says, in a few words, and if the tool says Mentat read the question "
          "with an input he did not give, say what it assumed. Never recall, convert or recompute a physical number yourself: if he wants other units, ask Mentat again. If "
          "Mentat is offline or cannot model the question, say so; do not answer it yourself. If it needs an input it suggests a value: tell him the suggestion and ask "
          "whether to use it, and only if he says yes call ask_mentat again with use_suggestion=true. Never read its formula or notes aloud. Mentat is for physics, chemistry, "
          "astronomy and units: never for places, driving distances, prices, news or people.")

OFFLINE = ("Mentat, the physics engine on skippy-core, is not answering right now ({why}). Say so plainly; do not answer the physics yourself and do not give a number "
           "from memory.")
SLOW = ("Mentat did not answer in time (it is busy, or a model on skippy-core is loading). Say so plainly and offer to ask again; do not answer the physics yourself "
        "and do not give a number from memory.")
NO_SUGGESTION = ("There is no suggestion of Mentat's waiting (they last five minutes). Ask him the question again, with the values he wants, and call ask_mentat with "
                 "the question alone.")
REMINDER = (" (Say the number in the units given, name Mentat and the source exactly as the Source line says, in a few words, and no other source; never calculate or convert it yourself. "
            "Check that the source and the reading are about what he asked: if Mentat solved a different question, or used an input he did not give, say it misread or "
            "assumed that and do not pass the number off as the answer; you cannot ask Mentat again in this reply, so do not say you will: he can ask again in other words.)")

# ── his questions, routed (the portal chat forces Mentat when a reply claims it; here the tool runs first): only the questions that are plainly physics, chemistry,
# astronomy or a unit conversion. Everything else is the model's call, with the tool and the rule above.
QUANTITIES = (r"escape (?:velocity|speed)|orbital (?:velocity|speed|period|radius)|speed of (?:light|sound)|light[- ]?years?|schwarzschild|event horizon|"
              r"molar mass|molecular (?:mass|weight)|atomic (?:mass|number|weight|radius)|boiling point|melting point|freezing point|half[- ]life|specific heat|heat capacity|"
              r"thermal conductivity|wavelength|electron ?volts?|kinetic energy|potential energy|terminal velocity|surface gravity|gravitational (?:force|acceleration|constant)|"
              r"acceleration (?:due to|of) gravity|g[- ]force|centripetal|angular momentum|moment of inertia|ohm'?s law|planck|boltzmann|avogadro|refractive index|redshift|"
              r"(?:mass|radius|diameter|density|gravity|age|distance|escape velocity|surface temperature|temperature) (?:of|from|to|on) (?:the )?"
              r"(?:sun|moon|earth|mars|jupiter|venus|saturn|mercury|neptune|uranus|pluto|milky way|andromeda|proxima centauri|sirius)|"
              r"weigh\w*\b[^.?!]{0,40}\b(?:on|at|in) (?:the )?(?:sun|moon|mars|jupiter|venus|saturn|mercury|neptune|uranus|pluto)|"
              r"how (?:fast|high|long)\b[^.?!]{0,40}\borbit\w*|"
              r"(?:energy|heat|calories|btus?)\b[^.?!]{0,20}\b(?:in|of|from|per)\b[^.?!]{0,20}\b(?:gallon|liter|litre|pound|kilogram|gram|barrel|gasoline|diesel|coal|uranium|hydrogen)")
LIGHT = r"how (?:long|far|fast)\b[^.?!]{0,30}\b(?:light|sound)\b[^.?!]{0,40}\b(?:take|travel|get|go)\b"
SKY = (r"how (?:far|big|large|massive|heavy|old|hot|cold|fast|dense)\b[^.?!]{0,25}\b(?:sun|moon|mars|jupiter|venus|saturn|mercury|neptune|uranus|pluto|"
       r"milky way|andromeda)\b")
UNITS = (r"inch(?:es)?|foot|feet|yards?|miles?|meters?|metres?|kilometers?|kilometres?|centimeters?|centimetres?|millimeters?|ounces?|pounds?|grams?|kilograms?|"
         r"tons?|tonnes?|liters?|litres?|milliliters?|gallons?|quarts?|pints?|cups?|tablespoons?|teaspoons?|seconds?|minutes?|hours?|days?|weeks?|years?|"
         r"celsius|fahrenheit|kelvin|calories?|kilocalories|joules?|kilojoules?|watts?|kilowatts?|kilowatt[- ]hours?|volts?|amps?|amperes?|newtons?|pascals?|psi|"
         r"bar|atmospheres?|horsepower|knots?|mph|kph|acres?|hectares?|parsecs?|astronomical units?|light[- ]?years?|bytes?|kilobytes?|megabytes?|gigabytes?")
CONVERT = re.compile(rf"\bconvert\b[^.?!]*\b(?:{UNITS})\b[^.?!]*\b(?:to|into|in)\b[^.?!]*\b(?:{UNITS})\b"
                     rf"|\bhow (?:many|much)\b[^.?!]*\b(?:{UNITS})\b[^.?!]*\b(?:in|per|to make|make up|are in|is in)\b[^.?!]*\b(?:{UNITS})\b"
                     rf"|\bwhat(?:'s| is)\b[^.?!]*\d[^.?!]*\b(?:{UNITS})\b[^.?!]*\b(?:in|to)\b[^.?!]*\b(?:{UNITS})\b", re.I)
PHYSICS = re.compile(rf"\b(?:{QUANTITIES})\b|\b{LIGHT}|\b{SKY}", re.I)
NOT_PHYSICS = re.compile(r"\b(?:play|song|album|music|playlist|news|headlines?|weather|forecast|alarm|timer|remind\w*|joke|poem|haiku|story|limerick)\b", re.I)


def pre_route(heard: str) -> dict | None:
    """ask_mentat's arguments for a plain physics, chemistry or unit question ("what is the escape velocity of Mars", "how many ounces in a cup"); None = the model decides."""
    h = re.sub(r"^\W*(?:hey |ok |okay )?skippy\W*", "", (heard or "").strip(), flags=re.I).strip()
    if not h or NOT_PHYSICS.search(h) or not (PHYSICS.search(h) or CONVERT.search(h)):
        return None
    return {"question": h}


# ── a physical figure said with no call to Mentat (Core, 2026-11-04: "[Mentat] The escape velocity of Mars is 5.03 km/s. Source: NASA Mars Fact Sheet." and "That comes from
# Mentat" with no tool run; a seventy kilogram person "weighs 114 pounds on the Moon", ten times too many). When his words were a factual question, the house asks Mentat itself
# (the portal chat's rule too: a reply that claims Mentat without calling it is thrown away and Mentat is forced) and Core only words the answer: a second try with the tool on
# offer wrote "[ask_mentat: ...]" as text instead. Units that mostly a science answer uses, so "a 60 watt bulb", "ten percent" and "330 meters" are left alone ──
_SCI = (r"(?:kilo)?met(?:er|re)s? (?:per|a|every) (?:second|hour)|miles? (?:per|an|a|every) hour|(?:mega|kilo|giga)?joules?|kelvins?|pascals?|newtons?|light[- ]?years?|parsecs?|"
        r"astronomical units?|electron ?volts?|becquerels?|moles?|g[- ]forces?")
_NUM = (r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
        r"seventy|eighty|ninety|hundred|thousand|million|billion|trillion|point)")
# digits ("5.03 km/s") and the way the voice says them ("five point zero kilometers per second": Core spells numbers out for the speaker)
FIGURE = re.compile(rf"\d[\d,.]*\s*(?:km/s|m/s|km/h|mph|{_SCI})\b|\b{_NUM}(?:[\s-]+(?:{_NUM}|and))*\s+(?:{_SCI})\b|\btimes (?:ten|10) to the\b|\bmach \d", re.I)
NAMED = re.compile(r"\bmentat\b", re.I)
ASKING = re.compile(r"\b(?:what|how|why|which|calculate|compute|convert|roughly|about how)\b|\?\s*$", re.I)


def unbacked(reply: str) -> bool:
    """A reply that presents a physical figure as fact (one of FIGURE's units after a number), or puts words in Mentat's mouth with a digit in them."""
    t = reply or ""
    return bool(FIGURE.search(t) or (NAMED.search(t) and re.search(r"\d", t)))


def askable(heard: str) -> bool:
    """His words were a factual question (not a poem, a joke, a song or a timer that happens to carry a figure): Mentat can be asked them as they are."""
    h = (heard or "").strip()
    return bool(h) and bool(ASKING.search(h)) and not NOT_PHYSICS.search(h)


# ── Mentat's answer as plain words ──
_CITE =re.compile(r"\s*\[\d+(?:[,\s]+\d+)*\]")
_URL = re.compile(r"https?://\S+|www\.\S+")
_SOURCES = re.compile(r"\n\s*Sources?:.*", re.S | re.I)
_MARKUP = re.compile(r"[*`#>|]+")                                # not "_": it is in names (body_name), and a name is what Mentat's "read as" line shows


def clean(text, n: int = 300) -> str:
    """Plain words from Mentat's text: the citation marks, links, markup and the sources block gone, one line, cut at a sentence or a word, never with dots."""
    t = _SOURCES.sub("", str(text or ""))
    t = _MARKUP.sub(" ", _URL.sub("", _CITE.sub("", t)))
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= n:
        return t
    cut = t[:n]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end >= n // 3 else cut.rsplit(" ", 1)[0]).rstrip(" ,;:")


def say_number(v, digits: int = SIGNIFICANT) -> str:
    """A number as it is said: SIGNIFICANT digits, no exponent below a quadrillion ("11186", "0.62137"), "6.0221 times 10 to the 23" beyond."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
        return str(v)
    if v == 0:
        return "0"
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))                                    # an exact value (299792458, a count) is said whole, never rounded to five digits
    mag = math.floor(math.log10(abs(v)))
    if -4 <= mag < 15:
        places = digits - 1 - mag
        r = round(v, places)
        return str(int(r)) if r == int(r) else f"{r:.{max(places, 0)}f}".rstrip("0").rstrip(".")
    return f"{round(v / 10 ** mag, digits - 1):g} times 10 to the {mag}"


def say_value(v) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return say_number(v)
    if isinstance(v, dict):
        return "; ".join(f"{str(k).replace('_', ' ')} {say_value(x)}" for k, x in list(v.items())[:8])
    if isinstance(v, (list, tuple)):
        return ", ".join(say_value(x) for x in v[:8])
    return clean(v, 200)


def source_words(src) -> str:
    """The first clause of Mentat's source ("sigma-ground (classical escape velocity)"): the part he can hear, not the whole bibliography."""
    first = re.split(r";|\n", str(src or ""), maxsplit=1)[0]
    return clean(first, 120)


def suggestion(result) -> dict | None:
    """Mentat's clarification when the question left an input out: {needs, suggested, sentence, follow_up: {question, given}}; None for an answer, a gap or an error."""
    clar = result.get("clarification") if isinstance(result, dict) else None
    return clar if isinstance(clar, dict) and isinstance(clar.get("follow_up"), dict) else None


def clean_follow_up(follow) -> dict | None:
    """The follow-up exactly as Mentat made it ({question, given}), size-capped and plain values only (the portal chat's rule); None if it is anything else."""
    if not isinstance(follow, dict):
        return None
    question = str(follow.get("question") or "").strip()[:2000]
    given = follow.get("given")
    if not question or not isinstance(given, dict) or not given or len(given) > 40:
        return None
    out = {}
    for k, v in given.items():
        if not isinstance(k, str) or len(k) > 60 or isinstance(v, bool) or not isinstance(v, (int, float, str)):
            return None
        out[k] = v if not isinstance(v, str) else v[:120]
    return {"question": question, "given": out}


def words(reply) -> str:
    """Mentat's reply ({"ok", "result"} or {"ok": false, "error"}) as the tool's text."""
    if not isinstance(reply, dict):
        return "Mentat answered something unreadable. Say so; do not answer the physics yourself."
    r = reply.get("result") if isinstance(reply.get("result"), dict) else None
    if r is None:
        return "Mentat refused: " + (clean(reply.get("error"), 300) or "no reason given") + ". Say so; do not answer the physics yourself."
    err = r.get("error")
    clar = suggestion(r)
    if clar is not None:
        out = ["Mentat needs one more input before it can answer: " + (clean(clar.get("sentence") or clar.get("needs") or "", 300) or "an input is missing") + "."]
        offered = clar.get("suggested") if isinstance(clar.get("suggested"), dict) else {}
        for name, s in list(offered.items())[:6]:
            if isinstance(s, dict):
                out.append(f"It suggests {clean(s.get('label') or name, 60)} = {say_value(s.get('value'))} {clean(s.get('units'), 30)}".rstrip()
                           + (f" ({clean(s.get('why'), 120)})" if s.get("why") else "") + ".")
        out.append("It has computed nothing. Tell him the suggestion and ask whether to use it; only if he says yes, call ask_mentat again with use_suggestion=true.")
        return " ".join(out)
    if r.get("value") is not None:
        units = str(r.get("units") or "").replace("_", " ").strip()
        out = [f"Mentat's answer: {say_value(r['value'])}{' ' + units if units else ''}."]
        if r.get("interpreted_as"):
            out.append(f"Mentat read the question as: {clean(r['interpreted_as'], 160)}.")
        if r.get("assumed"):
            out.append(f"Mentat assumed: {clean(json.dumps(r['assumed']) if not isinstance(r['assumed'], str) else r['assumed'], 200)}. Say so.")
        if r.get("source"):
            out.append(f"Source: {source_words(r['source'])}.")
        tag = str(r.get("provenance_tag") or "")
        if tag:
            out.append(f"Provenance: {tag}." + (" This is a speculative placeholder, not a measurement: say so prominently." if tag == "SPECULATIVE-PENDING" else ""))
        if r.get("uncertainty") not in (None, "", 0):
            out.append(f"Uncertainty: {say_value(r['uncertainty'])}.")
        if r.get("notes"):
            out.append(f"Note: {clean(r['notes'], 240)}")
        return " ".join(out) + REMINDER
    if r.get("display"):
        return ("Mentat did not calculate this: it looked it up on the web. Say it is from Mentat's web search, not a physics calculation. "
                + clean(r["display"], 500))
    if err:
        msg = err.get("message") if isinstance(err, dict) else err
        return "Mentat could not answer: " + (clean(msg, 300) or "no reason given") + ". Say so; do not answer the physics yourself."
    return ("Mentat could not model that question" + (": " + clean(r.get("notes"), 300) if r.get("notes") else "") + ". Say so plainly; do not answer the physics yourself "
            "and do not give a number from memory.")


def post_json(url: str, body: dict, headers: dict | None = None, timeout: float = ASK_TIMEOUT_S) -> dict:
    """Mentat's JSON reply; a refusal (4xx/5xx with a JSON body) is returned like an answer, a dead service raises."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:                # noqa: S310 — Mentat on the tailnet
            return json.loads(r.read(2_000_000))
    except urllib.error.HTTPError as e:
        try:
            got = json.loads(e.read(200_000) or b"{}")
        except ValueError:
            got = {}
        got = got if isinstance(got, dict) else {}
        got.setdefault("ok", False)
        got.setdefault("error", f"Mentat answered {e.code}")
        got["status"] = e.code
        return got


class Physics:
    def __init__(self, env: dict | None = None, post=post_json, clock=time.time, log=None):
        env = env or {}
        self.url = (env.get("SKIPPY_MENTAT_URL") or env.get("MENTAT_URL") or MENTAT_URL).rstrip("/")
        self.post, self.clock, self.log = post, clock, log
        self.timeout = ASK_TIMEOUT_S
        self.pending: dict[str, dict] = {}                      # room -> {"follow_up", "at"}: the values Mentat suggested, until his yes (or five minutes)

    def run(self, args: dict, room: str = "", trace: str = "") -> str:
        args = args if isinstance(args, dict) else {}
        question = str(args.get("question") or "").strip()
        body: dict
        if args.get("use_suggestion") is True:
            kept = self.pending.get(room)
            if not kept or self.clock() - kept["at"] > KEEP_SUGGESTION_S:
                self.pending.pop(room, None)
                return NO_SUGGESTION
            body = dict(kept["follow_up"])                      # exactly as Mentat made it: never a value the model wrote
        elif question:
            body = {"question": question[:2000]}
        else:
            return "ask_mentat needs his question, in his words."
        started = time.monotonic()
        try:
            reply = self.post(f"{self.url}/ask", body, {"X-Skippy-Caller": "house-voice", "X-Skippy-Trace": str(trace)[:200]}, self.timeout)
        except (TimeoutError, urllib.error.URLError, OSError, ValueError) as e:
            slow = isinstance(e, TimeoutError) or "timed out" in str(e).lower()
            if self.log:
                self.log(f"(mentat: {'no answer in time' if slow else type(e).__name__}: {str(e)[:80]})")
            return SLOW if slow else OFFLINE.format(why=type(e).__name__)
        status = reply.get("status") if isinstance(reply, dict) else None
        if isinstance(status, int) and (status >= 500 or status == 404):
            return OFFLINE.format(why=f"it answered {status}")
        if isinstance(status, int) and status == 429:
            return "Mentat asked us to slow down (too many questions); try again in a minute. Say so; do not answer the physics yourself."
        result = reply.get("result") if isinstance(reply, dict) else None
        clar = suggestion(result)
        follow = clean_follow_up(clar.get("follow_up")) if clar else None
        if follow:
            self.pending[room] = {"follow_up": follow, "at": self.clock()}
        else:
            self.pending.pop(room, None)                          # a new question, or the suggestion used: nothing is waiting any more
        if self.log:
            self.log(f"(mentat: {round(time.monotonic() - started, 1)} s: {question[:60] or 'the suggestion'})")
        return words(reply)

    def tools(self) -> list[dict]:
        return [TOOL]
