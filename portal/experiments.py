"""Experiments: the public showcase of the Captain's projects.

v1 is sigma-ground (github.com/aaronsrhodes2/sigma-ground, pinned in
core/cd/deploy.json's pins as SIGMA_GROUND_REF):
  • Mentat playground — the physics tools, live, through skippy-core's Mentat (its Funnel)
  • Weapon vs. Armor — sigma-ground's game: its own solver runs on Mentat (mentat/wva.py),
    the page only draws the fight (listed under Browser Games, play.py; its URL stays here)
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
import pathlib
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(os.environ.get("EXPERIMENTS_DIR", Path(__file__).with_name("experiments_static")))
REPO = "https://github.com/aaronsrhodes2/sigma-ground"


# How a demo is experienced: the inventory's filter chips and the pill on each tile.
KINDS = {
    "live": "Live in your browser",
    "recorded": "Recorded simulations",
    "results": "Results",
    "tool": "Tools",
    "local": "Runs on your computer",
}


@dataclass(frozen=True)
class Experiment:
    slug: str
    title: str
    blurb: str
    tags: tuple[str, ...]
    endpoint: str | None          # portal endpoint for the live demo, None = write-up only
    source: str                   # link to the code
    kind: str = "live"            # a key of KINDS
    featured: bool = False        # the big tiles at the top of the inventory
    icon: str = "🧪"


EXPERIMENTS = (
    Experiment("nbody", "N-Body Shootout",
               "Our N-body integrator against pure Keplerian orbits: 26 years of the Jovian system, 1.38 million "
               "steps. Energy is conserved to 1.5×10⁻⁶ and angular momentum to 4×10⁻¹⁵, and the Laplace "
               "resonance stays alive, which no Keplerian orbit can show.",
               ("N-body", "Benchmark"), "experiments.nbody", "https://github.com/aaronsrhodes2/matter-shaper",
               kind="results", featured=True, icon="🪐"),
    Experiment("double-slit", "Double-Slit",
               "Watch the Born rule build an interference pattern one particle at a time. Turn up the observer "
               "strength D and the fringes fade: which-path information costs visibility, D² + V² ≤ 1.",
               ("Quantum", "Simulation"), "experiments.double_slit", "https://github.com/aaronsrhodes2/matter-shaper",
               kind="live", featured=True, icon="〰️"),
    Experiment("mentat", "Mentat — a Wolfram Alpha rival",
               "About 220 physics and maths tools with provenance on every number: units, constants, "
               "symbolic maths, mechanics, relativity, cosmology, optics, thermodynamics, circuits, "
               "chemistry, nuclear and materials. Try any of them live.",
               ("Python", "MCP", "sympy", "scipy"), "experiments.mentat_page", f"{REPO}/tree/master/sigma_ground/mcp",
               kind="tool", icon="🧮"),
    Experiment("benchmark", "Mentat vs Wolfram Alpha vs Gemini",
               "150 physics questions, from intro mechanics to relativity, scored against ground truth. A small local model "
               "driving Mentat's tools beats both.",
               ("Evaluation",), "experiments.benchmark", f"{REPO}/tree/master/sigma_ground/mcp/benchmark",
               kind="results", icon="📊"),
    Experiment("minebench", "MineBench: the home AI builds in blocks",
               "MineBench asks a model to build an object in Minecraft blocks, and the detail and precision show at a glance what "
               "it can do. Our local, free purple Skippy (Qwen3.6 on skippy-core) builds a choo-choo train: once from the benchmark's "
               "one-line prompt, then from a full written spec with a look at its own work after each pass.",
               ("Benchmark", "Voxels", "Local AI"), "experiments.minebench", "https://github.com/Ammaar-Alam/minebench",
               kind="results", icon="🧱"),
    Experiment("radiance", "Radiance — simulated physics, rendered",
               "sigma-ground's own WebGL2 raymarcher replaying recorded simulations: falling metals, a "
               "Mach 2 projectile, glowing iron, gear trains, a windmill, water with Fresnel reflection.",
               ("WebGL2", "Simulation"), "experiments.radiance_index", f"{REPO}/tree/master/sigma_ground/radiance",
               kind="recorded", icon="✨"),
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


# The N-body shootout's scoreboard, verbatim from the recorded result (nbody_results.svg, 26-year Jovian run),
# including the columns and rows that don't flatter it.
NBODY_SCOREBOARD = (
    ("Energy conservation", "ΔE/E = 1.5×10⁻⁶ ✓", "Exact (analytical)"),
    ("Angular momentum", "ΔL/L = 4×10⁻¹⁵ ✓", "Exact (analytical)"),
    ("Laplace resonance lock", "+178° (alive!) ✓", "N/A (no interactions)"),
    ("Moon-moon gravity", "Yes: captured ✓", "Ignored ✗"),
    ("Orbital radii stable", "Within 2% ✓", "Exact by definition"),
    ("Phase accuracy (Io)", "±23° drift (needs J2)", "Meaningless (no coupling)"),
    ("Missing physics", "Jupiter J2, solar tides", "All moon interactions"),
)

def inventory(items) -> dict:
    """What experiments.html needs besides the items: the kinds, and which of them have anything in them."""
    return {"experiments": items, "kinds": KINDS, "present": {x.kind for x in items}}


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


DISTILL_CAUSES = ("no_route", "bad_args", "tool_error", "wrong_tool", "wrong_value")


def distillation() -> dict:
    """The improvement-distillation branch (sigma-ground misc/DISTILLATION_BRANCH_PLAN.md): every day a NEW set of terse science questions
    goes to Wolfram|Alpha (once, free tier) and to Mentat; Mentat is judged against OUR oracle and a miss is classified from Mentat's own
    record. Read from the committed day files (results/distill/distill-<date>.json, written by benchmark/distill_score.py) and the sets
    (distill-<date>.set.json) for days queued but not yet scored. Wolfram's column is the yardstick; nothing of Wolfram's is in the files."""
    folder = ROOT / "benchmark" / "distill"
    days, causes, unverified = [], {}, []
    try:
        scored = sorted(folder.glob("distill-*.json"))
        sets = sorted(folder.glob("distill-*.set.json"))
    except OSError:
        scored, sets = [], []
    scored = [p for p in scored if not p.name.endswith(".set.json")]
    have = {p.name.removesuffix(".json") for p in scored}
    for p in scored:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        s = d.get("summary") or {}
        w = s.get("wolfram") or {}
        days.append({"day": d.get("suite", p.stem).removeprefix("distill-"), "n": s.get("n", 0), "judged": s.get("judged", 0),
                     "right": s.get("mentat_right", 0), "misses": dict(s.get("mentat_misses") or {}), "unverified": s.get("unverified", 0),
                     "unasked": s.get("unasked", 0), "wa_answered": w.get("answered", 0), "wa_no_result": w.get("no_result", 0),
                     "wa_agrees": w.get("agrees_with_us", 0), "wa_disagrees": w.get("disagrees_with_us", 0), "ref": str(s.get("mentat_ref") or "")[:7],
                     "by_domain": s.get("by_domain") or {}})
        for r in d.get("rows") or []:
            if r.get("cause") in DISTILL_CAUSES:
                causes.setdefault((r["cause"], r.get("expected_tool") or "?"), []).append(r)
            elif r.get("cause") == "unverified":
                unverified.append(r)
    for p in sets:                                                           # queued, not yet scored: say so, with the count
        suite = p.name.removesuffix(".set.json")
        if suite in have:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            days.append({"day": suite.removeprefix("distill-"), "n": len(d.get("items") or []), "pending": True,
                         "made": (d.get("generator") or {}).get("made") or {}})
        except (OSError, ValueError):
            continue
    days.sort(key=lambda x: x["day"])
    grouped = [{"cause": c, "tool": t, "rows": len(rs), "examples": [r["question"] for r in rs[:2]]}
               for (c, t), rs in sorted(causes.items(), key=lambda kv: -len(kv[1]))]
    total = {"n": sum(x.get("n", 0) for x in days if not x.get("pending")), "judged": sum(x.get("judged", 0) for x in days),
             "right": sum(x.get("right", 0) for x in days), "wa_answered": sum(x.get("wa_answered", 0) for x in days),
             "wa_agrees": sum(x.get("wa_agrees", 0) for x in days)}
    return {"days": days, "causes": grouped, "unverified": unverified[:20], "total": total, "cause_names": DISTILL_CAUSES}


def radiance_dir() -> Path:
    return ROOT / "radiance"


# Injected into Radiance's index.html when served here: its chat box needs a
# local Qwen model (not hosted), so hide it and open straight into the viewer
# with the scene library showing.
RADIANCE_SHOWCASE_SNIPPET = """
<style>#composer, #scroll, #mic { display:none !important; }</style>
<a href="/experiments" style="position:fixed;left:12px;top:10px;z-index:99;font:14px -apple-system,'Segoe UI',Roboto,sans-serif;
   color:#00ffff;text-decoration:none;padding:8px 14px 8px 10px;border-radius:999px;background:#003322;border:1px solid #006644">&lsaquo; Experiments</a>
<a href="/" aria-label="Home" title="Home" style="position:fixed;left:50%;transform:translateX(-50%);bottom:14px;z-index:99;width:46px;height:46px;
   border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.3rem;text-decoration:none;color:#aa44ff;background:#050505;
   border:1px solid #aa44ff">&#8962;</a>
<script>
  window.addEventListener("load", () => {
    document.getElementById("open-viewer")?.click();
    setTimeout(() => document.getElementById("b-lib")?.click(), 300);
  });
</script>
"""


# MineBench runs (github.com/Ammaar-Alam/minebench: 256 grid, simple palette, its own system prompt; experiments/minebench.html).
# A run is its folder, static/experiments/minebench/<id>/: run.json (n, the run's place in time; title, model, prompt, comparable,
# blocks, minutes, size, verdict, request when the full text is there, and stats: when it started, its tokens and every model it used,
# tools/voxel/run_stats.py), views.png, model.glb and build.json. A new folder is a new page, in the order the runs happened (the
# Captain, 2026-10-31): nothing to add here. tools/voxel/publish_run.py writes the folder from a run's working folder.
# Runs come in series (run.json "series", "tuned" when absent), newest series first, each in the order its runs happened. The first
# eleven are the tuned branch: by the Captain's call (2026-10-31) its tooling had tainted the comparison, so the fresh start began.
MINEBENCH_SERIES = (
    ("rematch", "Rematch", "Best 4 of 7, started 2026-11-01, after Haiku won the head to head 4-0: the same two players, the same vanilla "
     "setup, message and prompt, plus the generic help purple got that day (no training, no prompt change, nothing about trains): "
     "eyes (Skippy Core's vision model describes any picture the coder opens) and the stop gate's work check, which won't let a "
     "run end while MineBench's own runtime fails its build, before it has opened a picture of its latest version, or before it "
     "has spent an 80K output-token budget (about what Haiku spent a round), each a few times at most. Only the coder plays "
     "again (the Captain: Haiku's builds are enough): each new build faces Haiku's from the same round of the head to head "
     "(rounds 5-7, if needed, reuse Haiku's 1-3), judged blind as before."),
    ("h2h", "Head to head", "Best 4 of 7, started 2026-10-31: Claude Haiku 5.5 against Skippy Core's coder, both as vanilla agents (Claude "
     "Code with purple's system prompt and an empty setup), the same message, MineBench's full tool-mode prompt, effort high, the same "
     "131K window, \"a choo-choo train\" every round. Each round is judged blind by a separate Claude agent with MineBench's own "
     "criteria; a build whose code fails loses. Result: Haiku 4-0. It won rounds 1 and 2 on the judge's call (high confidence both times); in rounds 3 and 4 the coder's code failed MineBench's runtime (a NaN, then an unquoted block name) after it had checked the code only against its own mock."),
    ("fresh", "Fresh start", "Nothing made for MineBench or for trains: MineBench's own prompt, and either one call to a model on "
     "Skippy Core, the way MineBench itself runs one, or purple Skippy with an empty setup (none of the Captain's skills, no "
     "checker, no shared memory)."),
    ("tuned", "The tuned branch", "Runs where purple Skippy had tooling written for this build: our runtime and its floating-parts "
     "and wheel checks, a voxel skill with a wheel helper, rules in its header. Set aside on 2026-10-31 as a tainted comparison; "
     "kept, good and bad."),
)
MINEBENCH_DIR = pathlib.Path(__file__).parent / "static" / "experiments" / "minebench"


def load_minebench_runs(root=MINEBENCH_DIR):
    runs = []
    for f in sorted(root.glob("*/run.json")):
        run = json.loads(f.read_text(encoding="utf-8"))
        run["id"] = f.parent.name
        runs.append(run)
    rank = {key: i for i, (key, _, _) in enumerate(MINEBENCH_SERIES)}
    runs.sort(key=lambda r: (rank[r.setdefault("series", "tuned")], (r.get("stats") or {}).get("started") or "~", r["n"]))
    for key in rank:
        mine = [r for r in runs if r["series"] == key]
        for k, r in enumerate(mine, 1):
            r["k"], r["of"] = k, len(mine)
    return tuple(runs)


MINEBENCH_RUNS = load_minebench_runs()
