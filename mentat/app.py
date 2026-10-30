"""Mentat over HTTP: sigma-ground's MCP physics tools as a small public JSON API.

sigma-ground ships its tools as a stdio MCP server (sigma_ground/mcp/server.py
builds a FastMCP `server` inside main() and calls server.run()). This wrapper
builds that same server, catching it instead of running stdio, and serves:

    GET  /health             "ok"
    GET  /tools              [{name, description, input_schema, domain}]  (the public set)
    POST /tools/<name>       JSON arguments → {"ok": true, "result": {...}} | {"ok": false, "error": "..."}
    POST /ask                {"question": "...", "given": {...}?} → Mentat's own plain-English front door
                             (Materia's translator: keyword routes, plus the qwen
                             residual when MENTAT_USE_LLM=1, i.e. the copy on skippy-pc).
                             An input the question leaves out is never filled in: the
                             result is a `clarification` (value null) suggesting a value
                             for each, whose `follow_up` ({question, given}) is what to
                             POST back once the person says yes.
    GET  /wva/catalog        Weapon vs. Armor: presets, materials (+ the game's temperature
                             colours), environments, limits  (wva.py)
    POST /wva/solve          {weapon, armor_layers, environment, distance_m} → the game's own
                             solve(), as JSON; inputs validated and clamped first
    POST /mcp                the same public tools as a remote MCP server (Streamable HTTP,
                             stateless, JSON replies): what Claude's custom connector and
                             any MCP client point at, instead of a local `docker exec` copy

It's public (the Experiments playground, Skippy's chat and MCP clients call it), so:
  • runs as the zero-permission `skippy-mentat` service account (bootstrap)
  • tools that need a local LLM or keep per-session state are left out
  • per-IP rate limit, argument size cap, per-call timeout
No LLM is involved anywhere here: it's physics code.

Every request to a compute endpoint is kept in full as research data (research.py:
the question or arguments, what Mentat understood, its whole answer, timings, the
caller and the X-Skippy-Trace header), forever, off the request path.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import urllib.request
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as CallTimeout

from flask import Flask, g, jsonify, request
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import LATEST_PROTOCOL_VERSION

import cascade_stage
import ready as ready_mod
import research
import position_stage
import fold_stage
import router_stage
import wva

# Natural-language / stateful tools: need the local Ollama translator or keep
# state in this process (which Cloud Run may scale or recycle at any time).
EXCLUDED_PREFIXES = ("playground_",)
EXCLUDED = {"simulate", "request_clarification"}
CALL_TIMEOUT_S = int(os.environ.get("MENTAT_CALL_TIMEOUT", "20"))
RATE_PER_MIN = int(os.environ.get("MENTAT_RATE_PER_MIN", "60"))
MAX_ARGS_BYTES = 8_000
MAX_GIVEN = 40                                  # inputs in one /ask "given"
# The copy on skippy-pc sets these: Qwen (Ollama) helps the translator with
# phrasings its keywords miss. The public cloud copy has no model: keywords only.
USE_LLM = os.environ.get("MENTAT_USE_LLM", "0") == "1"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "")


def _point_translator_at_ollama(url: str) -> None:
    """sigma-ground's translator hardcodes http://localhost:11434 as a default
    argument; inside a container Ollama is another service, so swap it."""
    from sigma_ground.materia import translator as tr
    for fn in (tr._ollama_chat, tr._ollama_plan):
        fn.__defaults__ = tuple(url if d == tr.OLLAMA_URL else d for d in fn.__defaults__)


def build_server():
    """sigma-ground's own FastMCP server, captured instead of run."""
    import mcp.server.fastmcp as fm
    captured = {}
    original = fm.FastMCP.run
    fm.FastMCP.run = lambda self, *a, **k: captured.setdefault("server", self)
    try:
        from sigma_ground.mcp import server as sg
        sg.main()
    finally:
        fm.FastMCP.run = original
    return captured["server"]


def public(name: str) -> bool:
    return name not in EXCLUDED and not name.startswith(EXCLUDED_PREFIXES)


def domain_of(name: str) -> str:
    """A coarse grouping for the playground's tool picker."""
    for prefix, label in (
        ("convert_", "Units"), ("lookup_constant", "Constants"), ("list_constants", "Constants"),
        ("parse_quantity", "Units"), ("schwarzschild", "Relativity"), ("hawking", "Relativity"),
        ("lorentz", "Relativity"), ("time_dilation", "Relativity"), ("isco", "Relativity"),
        ("photon_sphere", "Relativity"), ("hubble", "Cosmology"), ("critical_density", "Cosmology"),
        ("age_of_universe", "Cosmology"), ("orbital", "Orbital"), ("escape_velocity", "Orbital"),
        ("projectile", "Mechanics"), ("free_fall", "Mechanics"), ("solve", "Math"),
        ("integrate", "Math"), ("differentiate", "Math"), ("simplify", "Math"), ("factor", "Math"),
        ("expand", "Math"), ("matrix", "Math"), ("limit", "Math"), ("series", "Math"),
        ("snell", "Optics"), ("lens", "Optics"), ("double_slit", "Optics"), ("single_slit", "Optics"),
        ("grating", "Optics"), ("run_simulation", "Simulation"), ("list_simulation", "Simulation"),
    ):
        if name.startswith(prefix):
            return label
    if name.endswith("_analysis"):
        return "Analysis"
    return "Physics"


server = build_server()
if USE_LLM and OLLAMA_URL:
    _point_translator_at_ollama(OLLAMA_URL)
TOOLS = {t.name: t for t in asyncio.run(server.list_tools()) if public(t.name)}
try:
    GAME, GAME_ERROR = wva.Game(os.environ.get("SIGMA_GROUND_REF", "")), None
except Exception as exc:  # noqa: BLE001 — the other tools must still serve if the game can't load
    GAME, GAME_ERROR = None, f"Weapon vs. Armor didn't load at this sigma-ground commit: {exc}"
_pool = ThreadPoolExecutor(max_workers=4)
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()

app = Flask(__name__)


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            return True
        q.append(now)
        return False


class QwenUnavailable(Exception):
    pass


class BadArguments(Exception):
    """The caller's arguments were unusable (sigma-ground's tool contract says which parameter and what to send instead)."""

    def __init__(self, error: dict):
        super().__init__(str(error.get("message") or error.get("code") or "bad arguments"))
        self.error = error


# sigma-ground's tool contract (mcp/contract.py) answers a bad call as a normal result with value null and `error`; these codes are
# the CALLER's mistake (HTTP 400 here, as before), the others (unknown_key, declined) are the tool's own answer (HTTP 200, value null).
_CALLER_ERRORS = {"missing_argument", "bad_type", "bad_quantity", "wrong_dimension", "out_of_range", "invalid_arguments"}


def _qwen_ready() -> bool:
    from sigma_ground.materia import translator as tr
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL or tr.OLLAMA_URL}/api/tags", timeout=3) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        return any(n.startswith(tr.OLLAMA_MODEL) for n in names)
    except Exception:  # noqa: BLE001
        return False


def _router_guard(name: str, args: dict) -> str | None:
    """The same code guard /tools applies, for a plan the router wrote: an argument that looks like code is a router miss, nothing runs."""
    return "That input looks like code, not maths." if unsafe_arg(args_for_guard(name, args)) is not None else None


def _ask(question: str, given: dict | None = None) -> dict:
    from sigma_ground.mcp.tools.simulation import simulate
    if USE_LLM and not _qwen_ready():
        # The Captain's rule: the Qwen-backed Mentat fails rather than answer without Qwen.
        raise QwenUnavailable("Qwen (Ollama) is unreachable, so Mentat won't answer without it.")
    miss: dict = {}
    if not given:
        # Where is a planet, the Sun or the Moon at a time: a fixed rule, ahead of the cascade and the router (the router was trained before
        # body_position existed and sent these to the fact sheet). Only a question naming a body, a position word and a time; else it carries on.
        hit = position_stage.answer(question, call=_call, miss=miss)
        if hit is not None:
            return hit
        # A named body and a quantity sigma-ground folded into one call (escape velocity, Schwarzschild radius, ... with body_name):
        # a fixed rule, because the trained router predates body_name and plans solar_system_body > the formula (fold_stage.py).
        hit = fold_stage.answer(question, call=_call, miss=miss)
        if hit is not None:
            return hit
    if cascade_stage.enabled() and cascade_stage.MODE == "before" and not given:
        # Opt-in (MENTAT_CASCADE=before): the benchmark's deterministic cascade ahead of the router, as the harness measured it (140 vs the
        # router's 109 on the 150). A miss falls to the router with its reason in inputs.router_miss["cascade"].
        hit = cascade_stage.answer(question, miss=miss)
        if hit is not None:
            return hit
    if router_stage.enabled() and USE_LLM and not given:
        # Opt-in (MENTAT_ROUTER_MODEL): the trained router goes first. On the 150 benchmark questions the bare keyword shortcut was wrong as often as right
        # (it answered "escape velocity" with surface gravity), so it does not get to pre-empt the router. A miss (decline, clarification, error, router
        # down) falls through to the keyword + Qwen translator, exactly as before the router existed, and its reason travels with the fallback's
        # answer (inputs.router_miss) into the research log.
        routed = router_stage.answer(question, server=server, tools=TOOLS, ollama_url=OLLAMA_URL, guard=_router_guard, miss=miss)
        if routed is not None:
            return routed
    if cascade_stage.enabled() and cascade_stage.MODE == "after" and not given:
        # Opt-in (MENTAT_CASCADE=after): the cascade only where the router missed.
        hit = cascade_stage.answer(question, miss=miss)
        if hit is not None:
            return hit
    result = simulate(question, use_llm=USE_LLM, given=given or None).to_dict()
    result["interpreter"] = "keywords + qwen" if USE_LLM else "keywords"
    _decline_plainly(question, result)
    if miss and isinstance(result.get("inputs"), dict):
        result["inputs"]["router_miss"] = miss
    elif miss:
        result["inputs"] = {"router_miss": miss}
    return result


_FALLING_WORDS = re.compile(r"\b(fall|falls|falling|fell|drop|drops|dropped|dropping|hit the ground|impact|terminal velocity|heat up|heats up)\b", re.I)


def _decline_plainly(question: str, result: dict) -> None:
    """The last stop of /ask, when the cascade, the router and its fallback all missed: if the keyword translator can only offer its generic
    "I can simulate a falling object's impact speed..." blurb, a question about bananas, heartbeats or a puzzle was answered with an offer
    about falling objects (the 2026-10-30 adversarial misses). Say plainly that no tool covers the question, and keep the falling-object hint
    only for a question that mentions falling. The value stays null and nothing is guessed: this changes the text, the source and the route."""
    from sigma_ground.materia import translator as tr
    if result.get("value") is not None or result.get("clarification") or result.get("error"):
        return
    generic = getattr(tr, "_CLARIFY", None)                    # a private name: if sigma-ground renames it, /ask keeps its old answer
    if not generic or str(result.get("notes") or "") != generic:
        return
    hint = f" {generic}" if _FALLING_WORDS.search(question) else ""
    result["notes"] = ("Mentat has no tool that answers this question, so it won't guess a number. It computes standard physics, chemistry and "
                       "maths from its tools: ask for a quantity and give the inputs (\"escape velocity of Mars\", \"energy of a 500 nm photon\", "
                       "\"derivative of x^2 sin x\")." + hint)
    result["source"] = "mentat"
    if isinstance(result.get("inputs"), dict):
        result["inputs"]["route"] = "decline"


def _call(name: str, args: dict):
    content, structured = asyncio.run(server.call_tool(name, args))
    if structured is not None:
        result = structured.get("result", structured) if set(structured) == {"result"} else structured
    else:
        text = "".join(getattr(c, "text", "") for c in content)
        try:
            result = json.loads(text)
        except ValueError:
            result = {"text": text}
    err = result.get("error") if isinstance(result, dict) else None
    if isinstance(err, dict) and result.get("value") is None and err.get("code") in _CALLER_ERRORS:
        raise BadArguments(err)
    return result


READY = ready_mod.Readiness("mentat")
_CHECK_EVERY_S = 30
_checks: dict[str, tuple[float, bool, str]] = {}


def _cached(name: str, fn) -> tuple[bool, str]:
    """A dependency check, repeated at most every _CHECK_EVERY_S (a health probe must stay cheap, never pile work on the thing it asks about)."""
    at, ok, why = _checks.get(name, (0.0, False, ""))
    # (never checked = check now: on a fresh Cloud Run instance the monotonic clock starts near 0, so "0.0" looked fresh and /health
    # said 503 "not ready" for its first 30 s without asking anything)
    if name not in _checks or time.monotonic() - at > _CHECK_EVERY_S:
        try:
            ok, why = fn()
        except Exception as e:  # noqa: BLE001 — a check that raises is a failed check
            ok, why = False, f"{type(e).__name__}"
        _checks[name] = (time.monotonic(), ok, why)
    return ok, why


def _tools_answer() -> tuple[bool, str]:
    """Not "the process is up": a real physics call comes back right (the Sun's Schwarzschild radius is 2953 m)."""
    got = _call("schwarzschild_radius", {"mass_kg": 1.989e30})
    value = got.get("value") if isinstance(got, dict) else None
    if isinstance(value, (int, float)) and abs(value - 2953.25) < 5:
        return True, ""
    return False, "the physics tools answered a known question wrongly"


def _router_loaded() -> tuple[bool, str]:
    from sigma_ground.materia import translator as tr
    with urllib.request.urlopen(f"{OLLAMA_URL or tr.OLLAMA_URL}/api/tags", timeout=3) as r:
        names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
    return (any(n.startswith(router_stage.MODEL) for n in names), "the router model isn't offered by skippy-core")


@app.before_request
def _job_begins():
    if request.method == "POST":                       # a call that does work (a tool, a question, a game solve): counted while it runs
        g._job = READY.begin(request.path)


@app.teardown_request
def _job_ends(_exc):
    token = g.pop("_job", None)
    if token:
        READY.end(token)


@app.get("/health")
def health():
    """Ready, accepting, and what it is doing (PLATINUM section 24): 200 only when it can take a question RIGHT NOW. The physics tools get a
    real known question; on skippy-core (MENTAT_USE_LLM) the chat model and the trained router must be offered by skippy-core too."""
    ok, why = _cached("tools", _tools_answer)
    deps = {}
    if USE_LLM:
        q = _cached("llm", lambda: (_qwen_ready(), "skippy-core doesn't offer the chat model"))
        deps["llm"] = {"ready": q[0], "detail": "" if q[0] else q[1]}
        if router_stage.enabled():
            r = _cached("router", _router_loaded)
            deps["router"] = {"ready": r[0], "detail": "" if r[0] else r[1]}
    rep = READY.report(ready=ok, detail=why, deps=deps, instance=os.environ.get("MENTAT_INSTANCE", "cloud"), tools=len(TOOLS),
                       mode="llm" if USE_LLM else "keywords")
    return jsonify(rep), ready_mod.status_code(rep)


@app.get("/version")
def version():
    """Which sigma-ground commit this Mentat runs (the image's SIGMA_GROUND_REF) and how many tools it serves.
    sigma-ground's tools/check_mentat_deploy.sh and the wa-trickle service read it."""
    return jsonify(sigma_ground_ref=os.environ.get("SIGMA_GROUND_REF") or "dev", tools=len(TOOLS))


@app.get("/tools")
def list_tools():
    return jsonify([{"name": t.name, "description": (t.description or "").strip(),
                     "input_schema": t.inputSchema, "domain": domain_of(t.name)}
                    for t in sorted(TOOLS.values(), key=lambda t: t.name)])


# sigma-ground's symbolic tools hand strings to sympy.sympify, which is eval()
# underneath: a public caller could run Python here. Maths never needs dunder
# attributes, quotes, backslashes or Python's introspection builtins, so any
# argument containing them is refused before a tool sees it.
_UNSAFE = re.compile(
    r"__|['\"`\\;]|\b(lambda|import|exec|eval|open|compile|getattr|setattr|delattr|globals|locals|"
    r"vars|breakpoint|builtins|subclasses|mro|os|sys|subprocess|input)\b", re.I)


# Runaway maths: power towers and huge literals can pin a worker for minutes.
_HUGE = re.compile(r"(\^|\*\*)\s*\(?\s*\d{4,}"                    # x**10000
                   r"|(\^|\*\*)\s*\(?\s*[\d.]+\s*\)?\s*(\^|\*\*)"   # power towers: 9^9^9
                   r"|factorial\s*\(\s*\d{4,}|\d{4,}\s*!", re.I)       # 1000!


def args_for_guard(name: str, args: dict) -> dict:
    """What the code guard looks at. An ODE is written with primes (y'' + y); the tool turns them into Derivative(...)
    and sigma-ground's parser (mathparse) refuses any quote that is left, so the apostrophe alone is not code here."""
    if name == "solve_ode" and isinstance(args.get("equation"), str):
        return {**args, "equation": args["equation"].replace("'", "")}
    return args


def unsafe_arg(value) -> str | None:
    """The first string (anywhere in the arguments) that looks like code, else None."""
    if isinstance(value, str):
        return value if _UNSAFE.search(value) or _HUGE.search(value) else None
    if isinstance(value, dict):
        value = list(value.keys()) + list(value.values())
    if isinstance(value, (list, tuple)):
        for v in value:
            bad = unsafe_arg(v)
            if bad is not None:
                return bad
    return None


def client_ip() -> str:
    """Cloud Run's front end appends the real client address LAST; earlier
    X-Forwarded-For entries are whatever the caller sent."""
    hops = [h.strip() for h in (request.headers.get("X-Forwarded-For") or "").split(",") if h.strip()]
    return hops[-1] if hops else (request.remote_addr or "?")


def keep(endpoint: str, tool, req, reply: dict, status: int, started: float, **extra) -> None:
    """Put one request, in full, in the research log (research.py). Never raises, never waits."""
    try:
        research.LOG.record({
            "trace": (request.headers.get("X-Skippy-Trace") or "")[:200] or None,
            "endpoint": endpoint, "tool": tool,
            "caller": {"ip": client_ip(), "user_agent": (request.headers.get("User-Agent") or "")[:300] or None,
                       "via": (request.headers.get("X-Skippy-Caller") or "")[:100] or None},
            "request": req, "status": status, "ok": bool(reply.get("ok")) if isinstance(reply, dict) else False,
            "response": reply, "ms": round((time.monotonic() - started) * 1000, 1), **extra})
    except Exception:  # noqa: BLE001 — research logging must never break an answer
        pass


def _guarded(label: str, fn, *args, endpoint: str = "", tool=None, req=None, started=None):
    """Rate limit, size cap and timeout shared by every compute endpoint; the reply is kept for research."""
    started = started if started is not None else time.monotonic()
    reply, status = _guarded_reply(label, fn, *args)
    extra = {}
    if endpoint == "/ask" and isinstance(reply.get("result"), dict):
        extra = {"interpreter": reply["result"].get("interpreter"), "understood": research.understood(reply["result"])}
    keep(endpoint or label, tool, req, reply, status, started, **extra)
    return jsonify(reply), status


def _guarded_reply(label: str, fn, *args) -> tuple[dict, int]:
    ip = client_ip()
    if _rate_limited(ip):
        return {"ok": False, "error": "Slow down: rate limit reached, try again in a minute."}, 429
    if (request.content_length or 0) > MAX_ARGS_BYTES:
        return {"ok": False, "error": "arguments too large"}, 413
    future = _pool.submit(fn, *args)
    try:
        return {"ok": True, "result": future.result(timeout=CALL_TIMEOUT_S)}, 200
    except CallTimeout:
        return {"ok": False, "error": f"{label} took longer than {CALL_TIMEOUT_S}s"}, 504
    except QwenUnavailable as exc:
        return {"ok": False, "error": str(exc)}, 503
    except BadArguments as exc:
        return {"ok": False, "error": str(exc)[:2000], "detail": exc.error}, 400
    except Exception as exc:  # noqa: BLE001 — tool/validation errors go back as messages
        return {"ok": False, "error": str(exc)[:2000]}, 400


def refused(endpoint: str, tool, req, error: str, status: int, started: float):
    """A request turned away before any physics ran: answered, and kept for research like any other."""
    reply = {"ok": False, "error": error}
    keep(endpoint, tool, req, reply, status, started)
    return jsonify(reply), status


@app.post("/tools/<name>")
def call_tool(name):
    started, endpoint = time.monotonic(), f"/tools/{name}"
    args = request.get_json(silent=True)
    args = {} if args is None else args
    if name not in TOOLS:
        return refused(endpoint, name, args, f"no public tool named {name!r}", 404, started)
    if not isinstance(args, dict):
        return refused(endpoint, name, args, "arguments must be a JSON object", 400, started)
    if unsafe_arg(args_for_guard(name, args)) is not None:
        return refused(endpoint, name, args, "That input looks like code, not maths; Mentat won't evaluate it.", 400, started)
    return _guarded(name, _call, name, args, endpoint=endpoint, tool=name, req=args, started=started)


def _valid_given(given) -> bool:
    return given is None or (isinstance(given, dict) and len(given) <= MAX_GIVEN and all(
        isinstance(k, str) and isinstance(v, (str, int, float)) and not isinstance(v, bool) for k, v in given.items()))


@app.post("/ask")
def ask():
    started = time.monotonic()
    body = request.get_json(silent=True) or {}
    body = body if isinstance(body, dict) else {}
    question = str(body.get("question") or "").strip()
    given = body.get("given")
    req = {"question": question[:2000], "given": given}
    if not question:
        return refused("/ask", None, req, "ask needs a question", 400, started)
    if not _valid_given(given):
        return refused("/ask", None, req, "given must be an object of input name to number or quantity text "
                                          "(the clarification's follow_up.given, as Mentat sent it)", 400, started)
    return _guarded("ask", _ask, question[:2000], given, endpoint="/ask", req=req, started=started)


@app.get("/research")
def research_status():
    """Is the research log writing, and where (no records here: they are read where they're kept)."""
    return jsonify(research.LOG.status())


# ── /mcp: the same public tools as a remote MCP server ─────────────────────────
# MCP's Streamable HTTP transport in its simplest legal form: stateless (no session
# id), every POST answered with plain JSON, no server-sent stream (GET is a 405). A
# tools-only server needs nothing more, and this way the endpoint sits behind the same
# public set and the same guards as POST /tools/<name>, with no second server process.
MCP_TOOLS = [{"name": t.name, "description": (t.description or "").strip(), "inputSchema": t.inputSchema}
             for t in sorted(TOOLS.values(), key=lambda t: t.name)]
MAX_MCP_BYTES = MAX_ARGS_BYTES + 4_000     # the arguments plus the JSON-RPC envelope

# What an MCP client is SHOWN is three tools, not the 220+: listing every one costs a chat about 73,000 tokens of its context, and the research log shows
# almost every real call is a plain question (958 of 989 in October 2026). The full set is still here: POST /tools/<name> serves each one, `find_tools`
# searches them, `call_tool` runs one by name, and tools/call still accepts any real tool name directly (a client with an old tool list keeps working).
# MENTAT_MCP_FULL=1 brings the long list back in tools/list for a client that wants it.
MCP_META = [
    {"name": "ask", "description": "Ask a physics, chemistry or maths question in plain words (with the numbers you were given). Mentat finds the right "
                                   "calculation, runs it and answers with the value, unit, formula and source. Every physical number you report must come "
                                   "from here with its source; never calculate or recall one yourself. If it asks for more input, ask the person: do not "
                                   "fill in inputs, and pass `given` only with values they agreed to.",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string", "description": "the question, in plain words"},
         "given": {"type": "object", "description": "inputs the person confirmed (name to number or quantity text); only after they agreed"}},
         "required": ["question"]}},
    {"name": "find_tools", "description": "Search Mentat's individual tools (about 220) by a word in the name, description or domain (for example 'orbit', "
                                          "'optics', 'thermo'). Returns each match's name and argument names, for call_tool.",
     "inputSchema": {"type": "object", "properties": {"search": {"type": "string", "description": "a word to match (empty lists the domains)"}}}},
    {"name": "call_tool", "description": "Run one individual Mentat tool by name with exact arguments (find the name and arguments with find_tools). "
                                          "Prefer `ask` unless you already know the tool.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "the tool's name, for example schwarzschild_radius"},
         "arguments": {"type": "object", "description": "the tool's arguments, as find_tools shows them"}}, "required": ["name"]}},
]
MCP_META_NAMES = {t["name"] for t in MCP_META}
# The cloud copy has no language model (keywords only): its `ask` understands only a few scenarios, so it says so up front and the model goes
# straight to find_tools + call_tool for anything else, instead of trusting an `ask` that cannot answer.
KEYWORDS_ONLY_NOTE = ("On THIS server `ask` has no language model and understands only a few simple scenarios (a falling object's impact, "
                      "projectile range). For anything else, use find_tools to find the calculation and call_tool to run it.")
if not USE_LLM:
    MCP_META[0] = {**MCP_META[0], "description": MCP_META[0]["description"] + " " + KEYWORDS_ONLY_NOTE}


def _mcp_instructions() -> str | None:
    """sigma-ground's own rules for the model (cite every number's source, never guess)."""
    from sigma_ground.mcp import server as sg
    return getattr(sg, "SYSTEM_INSTRUCTIONS", None)


MCP_INSTRUCTIONS = _mcp_instructions()


def _rpc_result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tool_reply(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _mcp_call(name: str, args, ip: str) -> dict:
    """tools/call through POST /tools/<name>'s gates, kept for research like them. A refusal is a tool
    error the model reads (isError), not a transport failure that ends the client's session."""
    started = time.monotonic()
    reply = _mcp_call_reply(name, args, ip)
    keep("/mcp", name, args, {"ok": not reply["isError"], "mcp": reply}, 200, started)
    return reply


def _mcp_call_reply(name: str, args, ip: str) -> dict:
    if not isinstance(args, dict):
        return _tool_reply("arguments must be a JSON object", True)
    if _rate_limited(ip):
        return _tool_reply("Slow down: rate limit reached, try again in a minute.", True)
    if unsafe_arg(args_for_guard(name, args)) is not None:
        return _tool_reply("That input looks like code, not maths; Mentat won't evaluate it.", True)
    future = _pool.submit(_call, name, args)
    try:
        return _tool_reply(json.dumps(future.result(timeout=CALL_TIMEOUT_S), default=str))
    except CallTimeout:
        return _tool_reply(f"{name} took longer than {CALL_TIMEOUT_S}s", True)
    except BadArguments as exc:
        return _tool_reply(json.dumps(exc.error), True)
    except Exception as exc:  # noqa: BLE001 — tool/validation errors go back as messages
        return _tool_reply(str(exc)[:2000], True)


def _mcp_ask(args, ip: str) -> dict:
    """The `ask` tool: /ask's own path (rate limit, timeout, the Qwen rule), kept for research as an ask."""
    started = time.monotonic()
    question = str(args.get("question") or "").strip()[:2000]
    given = args.get("given")
    if not question:
        return _tool_reply("ask needs a question", True)
    if not _valid_given(given):
        return _tool_reply("given must be an object of input name to number or quantity text (the clarification's follow_up.given, as Mentat sent it)", True)
    reply, status = _guarded_reply("ask", _ask, question, given)
    extra = {}
    if isinstance(reply.get("result"), dict):
        extra = {"interpreter": reply["result"].get("interpreter"), "understood": research.understood(reply["result"])}
    keep("/mcp", "ask", {"question": question, "given": given}, reply, status, started, **extra)
    if reply.get("ok"):
        result = reply["result"]
        if not USE_LLM and isinstance(result, dict) and result.get("value") is None:
            return _tool_reply(json.dumps(result, default=str) + "\n\n" + KEYWORDS_ONLY_NOTE)
        return _tool_reply(json.dumps(result, default=str))
    return _tool_reply(json.dumps(reply.get("detail") or reply.get("error")), True)


def _mcp_find_tools(args) -> dict:
    needle = str(args.get("search") or "").strip().lower()[:80]
    rows = sorted(TOOLS.values(), key=lambda t: t.name)
    if not needle:
        domains = {}
        for t in rows:
            domains[domain_of(t.name)] = domains.get(domain_of(t.name), 0) + 1
        return _tool_reply("Domains (search one by name): " + ", ".join(f"{d} ({n})" for d, n in sorted(domains.items())))
    hits = [t for t in rows if needle in t.name.lower() or needle in (t.description or "").lower() or needle == str(domain_of(t.name)).lower()]
    if not hits:
        return _tool_reply(f"No Mentat tool matches {needle!r}.")
    lines = [f"{len(hits)} of {len(rows)} tools match {needle!r} (showing up to 40):"]
    for t in hits[:40]:
        props = ", ".join(((t.inputSchema or {}).get("properties") or {}))
        lines.append(f"- {t.name}({props}): {' '.join((t.description or '').split())[:140]}")
    return _tool_reply("\n".join(lines))


def _mcp_meta(name: str, args, ip: str) -> dict:
    if not isinstance(args, dict):
        return _tool_reply("arguments must be a JSON object", True)
    if name == "ask":
        return _mcp_ask(args, ip)
    if name == "find_tools":
        return _mcp_find_tools(args)
    target = str(args.get("name") or "")                      # call_tool
    if target not in TOOLS:
        return _tool_reply(f"There is no public tool named {target!r}; search with find_tools.", True)
    return _mcp_call(target, args.get("arguments") or {}, ip)


def _mcp_handle(msg, ip: str) -> dict | None:
    """One JSON-RPC message → its reply; None for a notification (or a client's reply)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "not a JSON-RPC 2.0 message")
    if "method" not in msg or "id" not in msg:
        return None
    method, id_, params = msg["method"], msg["id"], msg.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(id_, -32602, "params must be an object")
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _rpc_result(id_, {
            "protocolVersion": asked if asked in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "mentat", "version": (os.environ.get("SIGMA_GROUND_REF") or "dev")[:12]},
            **({"instructions": MCP_INSTRUCTIONS} if MCP_INSTRUCTIONS else {}),
        })
    if method == "ping":
        return _rpc_result(id_, {})
    if method == "tools/list":
        return _rpc_result(id_, {"tools": MCP_TOOLS if os.environ.get("MENTAT_MCP_FULL") == "1" else MCP_META})
    if method == "tools/call":
        name = params.get("name")
        if name in MCP_META_NAMES:
            return _rpc_result(id_, _mcp_meta(name, params.get("arguments") or {}, ip))
        if name not in TOOLS:
            return _rpc_error(id_, -32602, f"no public tool named {name!r}")
        return _rpc_result(id_, _mcp_call(name, params.get("arguments") or {}, ip))
    return _rpc_error(id_, -32601, f"method not found: {method}")


@app.post("/mcp")
def mcp_endpoint():
    if (request.content_length or 0) > MAX_MCP_BYTES:
        return jsonify(_rpc_error(None, -32600, "message too large")), 413
    msg = request.get_json(silent=True)
    if msg is None:
        return jsonify(_rpc_error(None, -32700, "parse error: the body must be one JSON-RPC message")), 400
    if isinstance(msg, list):     # JSON-RPC batches left MCP in 2025-06-18; never accepted here
        return jsonify(_rpc_error(None, -32600, "batches aren't supported: send one message per POST")), 400
    reply = _mcp_handle(msg, client_ip())
    return (jsonify(reply), 200) if reply is not None else ("", 202)


@app.route("/mcp", methods=["GET", "DELETE"])
def mcp_no_stream():
    """Stateless: no server-initiated stream to open (GET), no session to end (DELETE)."""
    return "", 405, {"Allow": "POST"}


@app.get("/wva/catalog")
def wva_catalog():
    if GAME is None:
        return jsonify(ok=False, error=GAME_ERROR), 503
    return jsonify(ok=True, result=GAME.catalog)


@app.post("/wva/solve")
def wva_solve():
    """One fight through sigma-ground's own Weapon vs. Armor solver. Nothing from the
    request reaches the game until wva.clean has checked, clamped and rebuilt it."""
    if GAME is None:
        return jsonify(ok=False, error=GAME_ERROR), 503
    if (request.content_length or 0) > wva.MAX_BODY_BYTES:
        return jsonify(ok=False, error="that fight is too large to send"), 413
    started, raw = time.monotonic(), request.get_json(silent=True)
    try:
        fight, clamped = GAME.clean(raw)
    except wva.Invalid as exc:
        return refused("/wva/solve", None, raw, str(exc), 400, started)
    return _guarded("Weapon vs. Armor", GAME.solve, fight, clamped, endpoint="/wva/solve", req=raw, started=started)
