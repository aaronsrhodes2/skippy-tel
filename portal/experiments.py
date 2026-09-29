"""Experiments: the public showcase of the Captain's projects.

v1 is sigma-ground (github.com/aaronsrhodes2/sigma-ground, pinned in
.github/workflows/deploy.yml as SIGMA_GROUND_REF):
  • Mentat playground — the physics tools, live, through the skippy-mentat service
  • Radiance — sigma-ground's WebGL viewer + its recorded simulations, served as-is
  • the Mentat vs Wolfram Alpha vs Gemini benchmark, from its committed results

The portal image carries a copy of the pieces it serves (portal/Dockerfile
fetches them at the pinned commit) under EXPERIMENTS_DIR:
    radiance/        sigma_ground/radiance/web/  (index.html, viewer.js, data/…)
    benchmark/       sigma_ground/mcp/benchmark/results/*_scored.json
Nothing here calls an LLM.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(os.environ.get("EXPERIMENTS_DIR", Path(__file__).with_name("experiments_static")))
REPO = "https://github.com/aaronsrhodes2/sigma-ground"


@dataclass(frozen=True)
class Experiment:
    slug: str
    title: str
    blurb: str
    tags: tuple[str, ...]
    endpoint: str | None          # portal endpoint for the live demo, None = write-up only
    source: str                   # link to the code


EXPERIMENTS = (
    Experiment("mentat", "Mentat — a Wolfram Alpha rival",
               "About 220 physics and maths tools with provenance on every number: units, constants, "
               "symbolic maths, mechanics, relativity, cosmology, optics, thermodynamics, circuits, "
               "chemistry, nuclear and materials. Try any of them live.",
               ("Python", "MCP", "sympy", "scipy"), "experiments.mentat_page", f"{REPO}/tree/master/sigma_ground/mcp"),
    Experiment("benchmark", "Mentat vs Wolfram Alpha vs Gemini",
               "150 physics questions, from intro mechanics to relativity, scored against ground truth. A small local model "
               "driving Mentat's tools beats both.",
               ("Evaluation",), "experiments.benchmark", f"{REPO}/tree/master/sigma_ground/mcp/benchmark"),
    Experiment("radiance", "Radiance — simulated physics, rendered",
               "sigma-ground's own WebGL2 raymarcher replaying recorded simulations: falling metals, a "
               "Mach 2 projectile, glowing iron, gear trains, a windmill, water with Fresnel reflection.",
               ("WebGL2", "Simulation"), "experiments.radiance_index", f"{REPO}/tree/master/sigma_ground/radiance"),
)

# The benchmark runs worth showing, as committed in sigma-ground (no curation
# of the numbers: each row is one results file's own summary).
BENCHMARK_RUNS = (
    ("Mentat + Qwen 2.5 7B (local, free)", "sigma_ground_scored.json", "mentat"),
    ("Gemini 2.5 Pro (no tools)", "gemini_scored.json", "llm"),
    ("Wolfram Alpha (misses re-run)", "wolfram_merged_scored.json", "wolfram"),
    ("Wolfram Alpha (single pass)", "wolfram_scored.json", "wolfram"),
)
ADVERSARIAL_RUNS = (
    ("Mentat", "adversarial_sigma_ground_scored.json", "mentat"),
    ("Wolfram Alpha", "adversarial_wolfram_scored.json", "wolfram"),
)


HIDDEN_KEY = "portal-experiments"   # VM metadata: JSON list of hidden slugs (dashboard toggle)


_CACHE: dict[int, tuple[float, list[str]]] = {}
CACHE_SECONDS = 30      # /experiments is public: don't call the Compute API on every visit


def hidden(vm) -> list[str]:
    cached = _CACHE.get(id(vm))
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        data = json.loads(vm.get_metadata(HIDDEN_KEY) or "[]")
        out = [s for s in data if isinstance(s, str)]
    except Exception:  # noqa: BLE001 — showing everything beats a broken showcase
        out = []
    _CACHE[id(vm)] = (time.monotonic(), out)
    return out


def forget(vm) -> None:
    """After the dashboard toggles one, show the change right away."""
    _CACHE.pop(id(vm), None)


def visible(vm) -> tuple[Experiment, ...]:
    gone = set(hidden(vm))
    return tuple(x for x in EXPERIMENTS if x.slug not in gone)


def _summary(name: str) -> dict | None:
    path = ROOT / "benchmark" / name
    try:
        return json.loads(path.read_text()).get("summary")
    except (OSError, ValueError):
        return None


def benchmark() -> dict:
    """Headline + adversarial rows and the domain matrix, from the result files present."""
    def rows(runs):
        out = []
        for label, name, kind in runs:
            s = _summary(name)
            if s and s.get("overall_total"):
                out.append({"label": label, "kind": kind, "file": name,
                            "pct": round(s["overall_accuracy_pct"], 1),
                            "correct": s["overall_correct"], "total": s["overall_total"],
                            "by_domain": s.get("by_domain", {})})
        return out
    main = rows(BENCHMARK_RUNS)
    domains = sorted({d for r in main for d in r["by_domain"]})
    return {"main": main, "adversarial": rows(ADVERSARIAL_RUNS), "domains": domains}


def radiance_dir() -> Path:
    return ROOT / "radiance"


# Injected into Radiance's index.html when served here: its chat box needs a
# local Qwen model (not hosted), so hide it and open straight into the viewer
# with the scene library showing.
RADIANCE_SHOWCASE_SNIPPET = """
<style>#composer, #scroll, #mic { display:none !important; }</style>
<div style="position:fixed;left:12px;bottom:10px;z-index:99;font:12px system-ui;color:#6a6a72">
  Recorded sigma-ground simulations · <a href="/experiments" style="color:#00CCFF">← Experiments</a></div>
<script>
  window.addEventListener("load", () => {
    document.getElementById("open-viewer")?.click();
    setTimeout(() => document.getElementById("b-lib")?.click(), 300);
  });
</script>
"""
