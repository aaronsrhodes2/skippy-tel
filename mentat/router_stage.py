"""Mentat's trained router as a stage of /ask (opt-in: MENTAT_ROUTER_MODEL; unset, nothing here runs).

The router (core/models.json slot `router`: Qwen3-4B + LoRA, "mentat-router-v31-4b") reads a question and writes ONE grammar-constrained JSON plan: which Mentat tool,
with which arguments, or a chain of up to three tools ("$1.mass_kg" feeds one call's result to the next), or a decline, or a clarification. It never computes: Mentat's
tools do. It was trained, and scored, with sigma-ground's benchmark harness (`run_sigma_ground._route_once`, plan format v3), which this module calls as it is, so what
runs here is what was measured there.

Where it sits in /ask (mentat/app.py `_ask`): first, ahead of the keyword + Qwen translator. A router answer is returned only when a
tool produced a value; a decline, a clarification, an unparseable plan, an unknown tool or parameter, a tool error, a timeout or the model being down all return None,
and the caller carries on exactly as it did before the router existed. So the router can add answers; it cannot take one away or stop /ask from answering.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

MODEL = os.environ.get("MENTAT_ROUTER_MODEL", "").strip()
PLAN = os.environ.get("MENTAT_ROUTER_PLAN", "v3")                 # the plan format the model was trained on (v3.1 = v3; v3.2 = v4)
TIMEOUT_S = float(os.environ.get("MENTAT_ROUTER_TIMEOUT", "60"))


class _Session:
    """The benchmark harness's `session`: call_tool(name, args) -> something with `.content` parts that carry `.text`, here in process."""

    def __init__(self, server, guard=None):
        self.server, self.guard = server, guard

    async def call_tool(self, name: str, args: dict):
        if self.guard is not None:
            why = self.guard(name, args)
            if why:
                raise ValueError(why)                              # becomes "<TOOL ERROR: ...>" in the harness: a router miss, nothing runs
        content, _ = await self.server.call_tool(name, args)
        return SimpleNamespace(content=content)


def enabled() -> bool:
    return bool(MODEL)


def _params(tools: dict) -> dict[str, set[str]]:
    out = {}
    for name, t in tools.items():
        schema = getattr(t, "inputSchema", None) or {}
        out[name] = set((schema.get("properties") or {}))
    return out


async def _run(question: str, server, tools: dict, ollama_url: str, guard) -> dict:
    import httpx
    from sigma_ground.mcp.benchmark import router_plan as RP
    from sigma_ground.mcp.benchmark.run_sigma_ground import _route_once
    names = [n for n in tools if n != "request_clarification"]
    system = RP.system_prompt(PLAN)
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
        return await asyncio.wait_for(
            _route_once(_Session(server, guard), http, ollama_url.rstrip("/") + "/api/chat", MODEL, question, system,
                        [{"function": {"name": n}} for n in names], _params(tools), time.time(), "toolfirst", PLAN),
            TIMEOUT_S + 30)


def _tool_dict(text: str) -> dict | None:
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def answer(question: str, *, server, tools: dict, ollama_url: str, guard=None) -> dict | None:
    """The /ask result dict for a question the router and Mentat's tools answered, or None (the caller carries on without it)."""
    if not (MODEL and ollama_url):
        return None
    try:
        rec = asyncio.run(_run(question, server, tools, ollama_url, guard))
    except Exception:                                              # noqa: BLE001 — a router that is down, slow or wrong never stops /ask
        return None
    if any(k in rec for k in ("router_error", "router_decline", "router_clarify")) or rec.get("extracted_value") is None:
        return None
    calls = rec.get("tool_calls") or []
    last = _tool_dict(calls[-1].get("result_text", "")) if calls else None
    if not last or last.get("error") or last.get("value") is None:
        return None
    plan = " then ".join(f"{c['name']}({', '.join(f'{k}={v!r}' for k, v in (c.get('args') or {}).items())})" for c in calls)
    chain = []
    for c in calls:
        got = _tool_dict(c.get("result_text", "")) or {}
        chain.append({"tool": c["name"], "args": c.get("args"), "value": got.get("value"), "units": got.get("units"), "source": got.get("source"),
                      "formula": got.get("formula")})
    out = {k: last.get(k) for k in ("value", "units", "source", "formula", "uncertainty", "provenance_tag", "display", "notes")}
    out["error"], out["clarification"], out["assumed"] = None, None, last.get("assumed")
    out["interpreted_as"] = plan
    out["interpreter"] = f"router {MODEL}"
    out["inputs"] = {"route": "router", "plan": rec.get("router_plan"), "chain": chain, "understood": [{"tool": c["name"], "params": c.get("args")} for c in calls]}
    return out
