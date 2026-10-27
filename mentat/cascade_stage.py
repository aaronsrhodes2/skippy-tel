"""The benchmark's deterministic cascade as a stage of /ask (opt-in: MENTAT_CASCADE=before|after; unset, nothing here runs).

sigma-ground's benchmark harness answers a question in three stages: pattern classifiers (frontier, new tools, refusal, clarification, conversion,
GR, cosmology, maths, thermo, modern, classical intro, optics, astro), the semantic interpreter, then the router model. The 2026-10-27 audit
(sigma-ground misc/MENTAT_AUDIT_2026-10-27.md, finding 3b) measured the router alone at 109 of the 150 narrative questions and the harness with its
cascade at 140; /ask had the router and the keyword translator, never the cascade. sigma-ground lifted the cascade into
`run_sigma_ground.cascade_answer(question, t0, router)`; this module runs it, unchanged, inside /ask.

MENTAT_CASCADE=before runs it ahead of the router, as the harness does; =after runs it only when the router missed. Which one /ask should use is
decided by measuring both against the live suites (the audit's replay), not assumed. MENTAT_CASCADE_SEMANTIC=1 also enables the semantic stage,
which needs an embedding model (nomic-embed-text) at the translator's Ollama URL; off by default because skippy-core's model shim does not serve one.

Like the router stage, a cascade answer is returned only when a stage produced a value; anything else returns None with its reason in `miss`
("cascade"), and the caller carries on.
"""
from __future__ import annotations

import os
import time

MODE = os.environ.get("MENTAT_CASCADE", "").strip().lower()
SEMANTIC = os.environ.get("MENTAT_CASCADE_SEMANTIC", "") == "1"
# Stages switched off by name (MENTAT_CASCADE_OFF=math,thermo): the harness tweak `cascade.<name>` for each. The 2026-10-27 replay measured
# `math` (regexes such as "factor ..." and "integral of ... from a to b") at 7 of 27 on the maths suite against the router's 317 of 329, and it
# took 19 answers the router had; the other stages only gained. So the deployed cascade runs with `math` off.
OFF = tuple(s.strip() for s in os.environ.get("MENTAT_CASCADE_OFF", "").split(",") if s.strip())
STAGES = ("frontier", "new_tools", "refusal", "clarification", "conversion", "gr", "cosmology", "math", "thermo", "modern", "classical_intro",
          "optics", "astro", "semantic")
STAGE_KEYS = ("frontier_router_hit", "new_tools_router_hit", "refusal_hit", "clarification_hit", "conversion_hit", "gr_hit", "cosmology_hit",
              "math_hit", "thermo_hit", "modern_hit", "classical_intro_hit", "optics_hit", "astro_hit", "semantic_hit")


def enabled() -> bool:
    return MODE in ("before", "after")


def configure_harness() -> None:
    """The cascade's tweak switches: the registry defaults (every classifier on), minus the stages named in MENTAT_CASCADE_OFF, and the semantic
    stage only when asked for."""
    from sigma_ground.mcp.benchmark import tweaks
    off = {f"cascade.{s}" for s in OFF if s in STAGES}
    if not SEMANTIC:
        off.add("cascade.semantic")
    tweaks.configure(None, off=tuple(sorted(off)))


def _stage(rec: dict) -> str:
    for k in STAGE_KEYS:
        if rec.get(k):
            return f"{k.removesuffix('_router_hit').removesuffix('_hit')}:{rec[k]}" if isinstance(rec[k], str) else k.removesuffix("_hit")
    for k, v in rec.items():
        if k.endswith("_hit") and v:
            return k.removesuffix("_hit")
    return "cascade"


def answer(question: str, *, miss: dict | None = None) -> dict | None:
    """The /ask result dict for a question a cascade stage answered, or None (the caller carries on)."""
    if not enabled():
        return None
    try:
        from sigma_ground.mcp.benchmark.run_sigma_ground import cascade_answer
        configure_harness()
        rec = cascade_answer(question, time.time(), True)
    except Exception as exc:                                       # noqa: BLE001 — a cascade that breaks never stops /ask
        if miss is not None:
            miss["cascade"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return None
    if rec is None:
        if miss is not None:
            miss["cascade"] = "no stage matched"
        return None
    value = rec.get("extracted_value")
    if value is None:
        if miss is not None:
            miss["cascade"] = f"{_stage(rec)} matched without a value"
        return None
    stage = _stage(rec)
    text = str(rec.get("answer_text") or "")
    return {"value": value, "units": rec.get("extracted_units") or "", "source": f"sigma-ground benchmark cascade ({stage})", "formula": None,
            "uncertainty": None, "provenance_tag": None, "display": None, "notes": text, "error": None, "clarification": None, "assumed": None,
            "interpreted_as": stage, "interpreter": "cascade",
            "inputs": {"route": "cascade", "stage": stage, "mode": MODE,
                       "harness_record": {k: rec.get(k) for k in ("answer_text", "extracted_value", "extracted_units", "tool_calls")}}}
