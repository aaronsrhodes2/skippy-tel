"""Mentat over HTTP: sigma-ground's MCP physics tools as a small public JSON API.

sigma-ground ships its tools as a stdio MCP server (sigma_ground/mcp/server.py
builds a FastMCP `server` inside main() and calls server.run()). This wrapper
builds that same server, catching it instead of running stdio, and serves:

    GET  /health             "ok"
    GET  /tools              [{name, description, input_schema, domain}]  (the public set)
    POST /tools/<name>       JSON arguments → {"ok": true, "result": {...}} | {"ok": false, "error": "..."}
    POST /ask                {"question": "..."} → Mentat's own plain-English front door
                             (Materia's translator: keyword routes, plus the qwen
                             residual when MENTAT_USE_LLM=1, i.e. the copy on skippy-pc)
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

from flask import Flask, jsonify, request
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import LATEST_PROTOCOL_VERSION

import wva

# Natural-language / stateful tools: need the local Ollama translator or keep
# state in this process (which Cloud Run may scale or recycle at any time).
EXCLUDED_PREFIXES = ("playground_",)
EXCLUDED = {"simulate", "request_clarification"}
CALL_TIMEOUT_S = int(os.environ.get("MENTAT_CALL_TIMEOUT", "20"))
RATE_PER_MIN = int(os.environ.get("MENTAT_RATE_PER_MIN", "60"))
MAX_ARGS_BYTES = 8_000
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


def _qwen_ready() -> bool:
    from sigma_ground.materia import translator as tr
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL or tr.OLLAMA_URL}/api/tags", timeout=3) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        return any(n.startswith(tr.OLLAMA_MODEL) for n in names)
    except Exception:  # noqa: BLE001
        return False


def _ask(question: str) -> dict:
    from sigma_ground.mcp.tools.simulation import simulate
    if USE_LLM and not _qwen_ready():
        # The Captain's rule: the Qwen-backed Mentat fails rather than answer without Qwen.
        raise QwenUnavailable("Qwen (Ollama) is unreachable, so Mentat won't answer without it.")
    result = simulate(question, use_llm=USE_LLM).to_dict()
    result["interpreter"] = "keywords + qwen" if USE_LLM else "keywords"
    return result


def _call(name: str, args: dict):
    content, structured = asyncio.run(server.call_tool(name, args))
    if structured is not None:
        return structured.get("result", structured) if set(structured) == {"result"} else structured
    text = "".join(getattr(c, "text", "") for c in content)
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


@app.get("/health")
def health():
    return "ok"


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


def _guarded(label: str, fn, *args):
    """Rate limit, size cap and timeout shared by every compute endpoint."""
    ip = client_ip()
    if _rate_limited(ip):
        return jsonify(ok=False, error="Slow down: rate limit reached, try again in a minute."), 429
    if (request.content_length or 0) > MAX_ARGS_BYTES:
        return jsonify(ok=False, error="arguments too large"), 413
    future = _pool.submit(fn, *args)
    try:
        return jsonify(ok=True, result=future.result(timeout=CALL_TIMEOUT_S))
    except CallTimeout:
        return jsonify(ok=False, error=f"{label} took longer than {CALL_TIMEOUT_S}s"), 504
    except QwenUnavailable as exc:
        return jsonify(ok=False, error=str(exc)), 503
    except Exception as exc:  # noqa: BLE001 — tool/validation errors go back as messages
        return jsonify(ok=False, error=str(exc)[:2000]), 400


@app.post("/tools/<name>")
def call_tool(name):
    if name not in TOOLS:
        return jsonify(ok=False, error=f"no public tool named {name!r}"), 404
    args = request.get_json(silent=True) or {}
    if not isinstance(args, dict):
        return jsonify(ok=False, error="arguments must be a JSON object"), 400
    if unsafe_arg(args) is not None:
        return jsonify(ok=False, error="That input looks like code, not maths; Mentat won't evaluate it."), 400
    return _guarded(name, _call, name, args)


@app.post("/ask")
def ask():
    question = str((request.get_json(silent=True) or {}).get("question") or "").strip()
    if not question:
        return jsonify(ok=False, error="ask needs a question"), 400
    return _guarded("ask", _ask, question[:2000])


# ── /mcp: the same public tools as a remote MCP server ─────────────────────────
# MCP's Streamable HTTP transport in its simplest legal form: stateless (no session
# id), every POST answered with plain JSON, no server-sent stream (GET is a 405). A
# tools-only server needs nothing more, and this way the endpoint sits behind the same
# public set and the same guards as POST /tools/<name>, with no second server process.
MCP_TOOLS = [{"name": t.name, "description": (t.description or "").strip(), "inputSchema": t.inputSchema}
             for t in sorted(TOOLS.values(), key=lambda t: t.name)]
MAX_MCP_BYTES = MAX_ARGS_BYTES + 4_000     # the arguments plus the JSON-RPC envelope


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
    """tools/call through POST /tools/<name>'s gates. A refusal is a tool error the
    model reads (isError), not a transport failure that ends the client's session."""
    if not isinstance(args, dict):
        return _tool_reply("arguments must be a JSON object", True)
    if _rate_limited(ip):
        return _tool_reply("Slow down: rate limit reached, try again in a minute.", True)
    if unsafe_arg(args) is not None:
        return _tool_reply("That input looks like code, not maths; Mentat won't evaluate it.", True)
    future = _pool.submit(_call, name, args)
    try:
        return _tool_reply(json.dumps(future.result(timeout=CALL_TIMEOUT_S), default=str))
    except CallTimeout:
        return _tool_reply(f"{name} took longer than {CALL_TIMEOUT_S}s", True)
    except Exception as exc:  # noqa: BLE001 — tool/validation errors go back as messages
        return _tool_reply(str(exc)[:2000], True)


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
        return _rpc_result(id_, {"tools": MCP_TOOLS})
    if method == "tools/call":
        name = params.get("name")
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
    try:
        fight, clamped = GAME.clean(request.get_json(silent=True))
    except wva.Invalid as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return _guarded("Weapon vs. Armor", GAME.solve, fight, clamped)
