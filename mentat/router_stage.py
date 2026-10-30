"""Mentat's trained router as a stage of /ask (opt-in: MENTAT_ROUTER_MODEL; unset, nothing here runs).

The router (core/models.json slot `router`: stock Qwen3-8B "qwen3-8b-stock" since 2026-10-29; the trained Qwen3-4B + LoRA "mentat-router-v31-4b" before) reads a question
and writes ONE grammar-constrained JSON plan: which Mentat tool,
with which arguments, or a chain of up to three tools ("$1.mass_kg" feeds one call's result to the next), or a decline, or a clarification. It never computes: Mentat's
tools do. It was trained, and scored, with sigma-ground's benchmark harness (`run_sigma_ground._route_once`, plan format v3), which this module calls as it is, so what
runs here is what was measured there.

Where it sits in /ask (mentat/app.py `_ask`): first, ahead of the keyword + Qwen translator. A router answer is returned only when a
tool produced a value; a decline, a clarification, an unparseable plan, an unknown tool or parameter, a tool error, a timeout or the model being down all return None,
and the caller carries on exactly as it did before the router existed. So the router can add answers; it cannot take one away or stop /ask from answering.
A miss is never silent: `answer(..., miss=d)` writes its reason (and the plan, when there was one) into `d`, and /ask keeps it in the fallback's
`inputs.router_miss`, so the research log says why the router did not answer.

The harness runs with `structured_results` on, the switch the router's scores were taken with (sigma-ground tools/eval_math_suite.py,
misc/ROUTER_V31_PLAN.md). Left at the registry defaults (2026-10-25 .. 2026-10-27) it was off, and the harness then reads a symbolic or list result (a
derivative, roots, a matrix) as "returned no value": the router planned every maths question right and /ask answered none of them (0 of 329; 313-317
with the switch on; sigma-ground misc/MENTAT_AUDIT_2026-10-27.md). Not the harness's `trained` profile: its `strict_params` refuses a plan that names a
parameter the v3.1 router still writes but the schema no longer lists, either a pre-rename name the server accepts as an alias
(joules_to_eV(energy_joules) -> energy_j) or an invented extra the tool ignores (free_fall_time(initial_velocity_m_s)); measured live, that profile
lost 2 physics answers for nothing. MENTAT_ROUTER_PROFILE and MENTAT_ROUTER_TWEAKS_ON override it for an experiment.
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
PROFILE = os.environ.get("MENTAT_ROUTER_PROFILE", "") or None     # a harness tweak profile; empty = the registry defaults (see above)
TWEAKS_ON = tuple(x for x in os.environ.get("MENTAT_ROUTER_TWEAKS_ON", "structured_results").replace(",", " ").split() if x)
# A stock model has no tool catalogue in its weights: with MENTAT_ROUTER_INDEX=1 the prompt carries the harness's tool index (every public tool, its
# parameters and a line of what it does, about 17k tokens), exactly as sigma-ground's stock baselines were scored (run_sigma_ground --router-index).
# A trained router (v3.1) learned the catalogue and runs without it (0).
INDEX = os.environ.get("MENTAT_ROUTER_INDEX", "0").strip() not in ("", "0", "false", "no")
# A second opinion where the trained router misses (2026-10-30, the Captain): a stock model reads the tool index, so it knows tools added after the
# router was trained, but alone it lost 93 of 329 maths answers (it asks for clarification where the router answers). Asked only after a miss,
# the router keeps what it answers and the stock model gets the rest. Empty = off.
FALLBACK_MODEL = os.environ.get("MENTAT_ROUTER_FALLBACK_MODEL", "").strip()
FALLBACK_INDEX = os.environ.get("MENTAT_ROUTER_FALLBACK_INDEX", "1").strip() not in ("", "0", "false", "no")
MISS_KEYS = ("router_error", "router_decline", "router_clarify")


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


def configure_harness() -> None:
    """The harness's tweak switches, set to the configuration the router was scored in (on every call: another caller may have reset them)."""
    from sigma_ground.mcp.benchmark import tweaks
    tweaks.configure(PROFILE, on=TWEAKS_ON)


async def _run(question: str, server, tools: dict, ollama_url: str, guard, model: str | None = None, index: bool | None = None) -> dict:
    import httpx
    from sigma_ground.mcp.benchmark import router_plan as RP
    from sigma_ground.mcp.benchmark.run_sigma_ground import _route_once
    configure_harness()
    names = [n for n in tools if n != "request_clarification"]
    model = MODEL if model is None else model
    system = RP.system_prompt(PLAN)
    if (INDEX if index is None else index):
        from sigma_ground.mcp.benchmark.run_sigma_ground import _ollama_tools, _router_system_prompt
        system = _router_system_prompt(_ollama_tools([tools[n] for n in names]), True, PLAN)
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
        return await asyncio.wait_for(
            _route_once(_Session(server, guard), http, ollama_url.rstrip("/") + "/api/chat", model, question, system,
                        [{"function": {"name": n}} for n in names], _params(tools), time.time(), "toolfirst", PLAN),
            TIMEOUT_S + 30)


def _tool_dict(text: str) -> dict | None:
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def _missed(miss: dict | None, reason: str, rec: dict | None = None) -> None:
    if miss is not None:
        miss["reason"] = reason
        if rec and rec.get("router_plan"):
            miss["plan"] = rec["router_plan"]


def answer(question: str, *, server, tools: dict, ollama_url: str, guard=None, miss: dict | None = None) -> dict | None:
    """The /ask result dict for a question the router and Mentat's tools answered, or None (the caller carries on without it).
    `miss`, when given, receives why there was no answer: "reason", and "plan" when the model wrote one.
    When the router misses and MENTAT_ROUTER_FALLBACK_MODEL is set, that model is asked the same way (with the tool index); its answer says
    so (interpreter "router <fallback> after <router>", inputs.router_first_miss) and its miss is reported beside the router's."""
    if not (MODEL and ollama_url):
        _missed(miss, "router off")
        return None
    first: dict = {}
    out = _answer_with(MODEL, INDEX, question, server=server, tools=tools, ollama_url=ollama_url, guard=guard, miss=first)
    if out is not None or not FALLBACK_MODEL:
        if miss is not None:
            miss.update(first)
        return out
    second: dict = {}
    out = _answer_with(FALLBACK_MODEL, FALLBACK_INDEX, question, server=server, tools=tools, ollama_url=ollama_url, guard=guard, miss=second)
    if out is not None:
        out["interpreter"] = f"router {FALLBACK_MODEL} after {MODEL}"
        out["inputs"]["router_first_miss"] = first
        return out
    if miss is not None:
        miss.update(first)
        miss["fallback"] = {"model": FALLBACK_MODEL, **second}
    return None


def _answer_with(model: str, index: bool, question: str, *, server, tools: dict, ollama_url: str, guard=None, miss: dict | None = None) -> dict | None:
    """One model's attempt: answer() for `model`, with or without the tool index in its prompt."""
    try:
        rec = asyncio.run(_run(question, server, tools, ollama_url, guard, model, index))
    except Exception as exc:                                       # noqa: BLE001 — a router that is down, slow or wrong never stops /ask
        _missed(miss, f"{type(exc).__name__}: {str(exc)[:200]}")
        return None
    for key in MISS_KEYS:
        if key in rec:
            _missed(miss, f"{key.removeprefix('router_')}: {str(rec[key])[:300]}", rec)
            return None
    if rec.get("extracted_value") is None:
        _missed(miss, "no value", rec)
        return None
    calls = rec.get("tool_calls") or []
    last = _tool_dict(calls[-1].get("result_text", "")) if calls else None
    if not last or last.get("error") or last.get("value") is None:
        _missed(miss, f"tool error: {str(last.get('error'))[:200]}" if last and last.get("error") else "the last tool call carried no value", rec)
        return None
    plan = " then ".join(f"{c['name']}({', '.join(f'{k}={v!r}' for k, v in (c.get('args') or {}).items())})" for c in calls)
    chain = []
    for c in calls:
        got = _tool_dict(c.get("result_text", "")) or {}
        chain.append({"tool": c["name"], "args": c.get("args"), "value": got.get("value"), "units": got.get("units"), "source": got.get("source"),
                      "formula": got.get("formula")})
    out = {k: last.get(k) for k in ("value", "units", "source", "formula", "uncertainty", "provenance_tag", "display", "notes")}
    if "value_float" in last:
        out["value_float"] = last["value_float"]        # the decimal beside an exact symbolic value ("1/3" -> 0.333...), sigma-ground contract
    out["error"], out["clarification"], out["assumed"] = None, None, last.get("assumed")
    record = None
    if isinstance(last.get("value"), dict) and rec.get("extracted_by") == "cue" and rec.get("extracted_field"):
        # A record tool answered (solar_system_body, element_atomic_data) and a phrase in the question named one field of it
        # ("surface gravity of Mars" -> surface_g_ms2): that field is the answer, the record travels in inputs.record. The harness
        # (sigma-ground run_sigma_ground.pick_field) decides the field; a guessed field (extracted_by "default") keeps the whole record,
        # because a guess presented as the answer is a silently wrong one. 7 of the 18 physics misses of 2026-10-27 were this.
        record, field = last["value"], rec["extracted_field"]
        out["value"], out["units"] = rec.get("extracted_value"), rec.get("extracted_units") or ""
        out["notes"] = f"{field} of the {calls[-1]['name']} record (the whole record is in inputs.record). " + (out.get("notes") or "")
    out["interpreted_as"] = plan
    out["interpreter"] = f"router {model}"
    out["inputs"] = {"route": "router", "plan": rec.get("router_plan"), "chain": chain, "understood": [{"tool": c["name"], "params": c.get("args")} for c in calls]}
    if record is not None:
        out["inputs"]["record"], out["inputs"]["field"] = record, rec["extracted_field"]
    return out
