"""A question that names the tool and its arguments is a call, not a riddle: a fixed rule ahead of every other stage in /ask.

An agent (purple Skippy, 2026-11-03) wrote to /ask: "Where is asteroid 433 Eros on 2029-10-27T00:00:00 UTC? ... Use the body_position tool
with body_name='eros', when='2029-10-27T00:00:00', central_body='barycenter', budget_s=100." The router read "asteroid" and answered with
asteroid_analysis's surface gravity, having ignored every argument. When the caller has already done the routing, Mentat runs that call:
the tool must exist, at least one `name=value` argument must parse, and sigma-ground's tool contract still checks the arguments.

Anything that is not such a directive returns None with its reason in `miss["directive"]`, and /ask carries on as before.
"""
from __future__ import annotations

import re

_TOOL = re.compile(r"\b(?:use|call|run|invoke|with|via)\s+(?:the\s+)?`?([a-z][a-z0-9_]{2,60})`?(?:\s+tool)?\b|\b`?([a-z][a-z0-9_]{2,60})`?\s*\(", re.I)
_ARG = re.compile(r"\b([a-z_][a-z0-9_]*)\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([^\s,;)]+))", re.I)
_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?$", re.I)


def read(question: str, tools: dict) -> tuple[str | None, dict | None, str]:
    """(tool name, arguments, why not): the directive the question carries, or None and the reason."""
    names = [n for pair in _TOOL.findall(question) for n in pair if n]
    known = [n for n in names if n in tools]
    if not known:
        return None, None, "no tool named" if not names else f"no such tool ({', '.join(dict.fromkeys(names))})"
    tool = known[0]
    schema = (getattr(tools[tool], "inputSchema", None) or {}).get("properties") or {}
    args: dict = {}
    for m in _ARG.finditer(question):
        key = m.group(1)
        if key not in schema:
            continue
        if m.group(2) is not None or m.group(3) is not None:
            args[key] = m.group(2) if m.group(2) is not None else m.group(3)
        else:
            raw = m.group(4).rstrip(".")
            t = (schema[key].get("type") if isinstance(schema[key], dict) else None)
            if t == "boolean" and raw.lower() in ("true", "false", "yes", "no", "1", "0"):
                args[key] = raw.lower() in ("true", "yes", "1")
            else:
                args[key] = (int(raw) if t == "integer" else float(raw)) if _NUMBER.match(raw) and t in ("integer", "number") else raw
    if not args:
        return None, None, f"{tool} named but no argument of its signature given as name=value"
    return tool, args, ""


def answer(question: str, *, call, tools: dict, miss: dict | None = None) -> dict | None:
    """The /ask result for a tool directive, or None (the caller carries on). `call(name, args)` runs a Mentat tool."""
    tool, args, why = read(question, tools)
    if tool is None:
        if miss is not None:
            miss["directive"] = why
        return None
    try:
        got = call(tool, args)
    except Exception as exc:                                      # noqa: BLE001 — a rule that breaks never stops /ask
        if miss is not None:
            miss["directive"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return None
    got = got if isinstance(got, dict) else {}
    plan = f"{tool}(" + ", ".join(f"{k}={v!r}" for k, v in args.items()) + ")"
    out = {k: got.get(k) for k in ("value", "units", "source", "formula", "uncertainty", "provenance_tag", "display", "notes")}
    out["error"] = None if out["value"] is not None else (got.get("error") or got.get("notes") or f"{tool} gave no value")
    out["clarification"], out["assumed"] = None, got.get("assumed")
    out["interpreted_as"] = plan
    out["interpreter"] = "directive (the question named the tool and its arguments)"
    out["inputs"] = {"route": "directive", "chain": [{"tool": tool, "args": args, "value": got.get("value"), "units": got.get("units"), "source": got.get("source"), "formula": None}],
                     "understood": [{"tool": tool, "params": args}]}
    return out
