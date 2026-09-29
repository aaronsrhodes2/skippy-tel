"""Mentat over HTTP: sigma-ground's MCP physics tools as a small public JSON API.

sigma-ground ships its tools as a stdio MCP server (sigma_ground/mcp/server.py
builds a FastMCP `server` inside main() and calls server.run()). This wrapper
builds that same server, catching it instead of running stdio, and serves:

    GET  /health             "ok"
    GET  /tools              [{name, description, input_schema, domain}]  (the public set)
    POST /tools/<name>       JSON arguments → {"ok": true, "result": {...}} | {"ok": false, "error": "..."}

It's public (the Experiments playground and Skippy's chat call it), so:
  • runs as the zero-permission `skippy-mentat` service account (bootstrap)
  • tools that need a local LLM or keep per-session state are left out
  • per-IP rate limit, argument size cap, per-call timeout
No LLM is involved anywhere here: it's physics code.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as CallTimeout

from flask import Flask, jsonify, request

# Natural-language / stateful tools: need the local Ollama translator or keep
# state in this process (which Cloud Run may scale or recycle at any time).
EXCLUDED_PREFIXES = ("playground_",)
EXCLUDED = {"simulate", "request_clarification"}
CALL_TIMEOUT_S = int(os.environ.get("MENTAT_CALL_TIMEOUT", "20"))
RATE_PER_MIN = int(os.environ.get("MENTAT_RATE_PER_MIN", "60"))
MAX_ARGS_BYTES = 8_000


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
TOOLS = {t.name: t for t in asyncio.run(server.list_tools()) if public(t.name)}
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


@app.post("/tools/<name>")
def call_tool(name):
    if name not in TOOLS:
        return jsonify(ok=False, error=f"no public tool named {name!r}"), 404
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()
    if _rate_limited(ip):
        return jsonify(ok=False, error="Slow down: rate limit reached, try again in a minute."), 429
    if (request.content_length or 0) > MAX_ARGS_BYTES:
        return jsonify(ok=False, error="arguments too large"), 413
    args = request.get_json(silent=True) or {}
    if not isinstance(args, dict):
        return jsonify(ok=False, error="arguments must be a JSON object"), 400
    future = _pool.submit(_call, name, args)
    try:
        return jsonify(ok=True, result=future.result(timeout=CALL_TIMEOUT_S))
    except CallTimeout:
        return jsonify(ok=False, error=f"{name} took longer than {CALL_TIMEOUT_S}s"), 504
    except Exception as exc:  # noqa: BLE001 — tool/validation errors go back as messages
        return jsonify(ok=False, error=str(exc)[:2000]), 400
