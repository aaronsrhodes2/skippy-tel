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
# One row per run, written from the run itself (block count from tools/voxel/runtime.mjs, time from the run), files under
# static/experiments/minebench/<id>/: views.png (tools/voxel/render.py), model.glb (--glb), build.json (the voxel.exec call).
MINEBENCH_RUNS = (
    {"id": "v1", "title": "Choo-choo train, the benchmark's one-line prompt",
     "model": "Purple Skippy: Claude Code on Skippy Core's coder (Qwen3.6-35B-A3B, MTP, Q8), local and free",
     "prompt": "a choo-choo train", "comparable": True, "blocks": 79351, "minutes": 13, "size": "200 x 47 x 180",
     "verdict": "Reads clearly as a steam train: black boiler with white bands, a tall stack puffing smoke, a cowcatcher, track with "
                "sleepers, a station with a red roof and gold trim, a water tower, trees. Short of the frontier builds: the wheels lie "
                "beside the track instead of under the engine, the passenger cars are flat slabs with no windows, a few blocks float, "
                "and most of the plate is empty ground."},
    {"id": "v2", "title": "Choo-choo train, a full written specification",
     "model": "Purple Skippy: Claude Code on Skippy Core's coder (Qwen3.6-35B-A3B, MTP, Q8), local and free",
     "prompt": "BUILD_REQUEST_V2: a 4-4-0 locomotive, tender and two passenger cars specified part by part (proportions in blocks, "
               "materials, how the parts join), v1's mistakes, and a loop of render, look (the vision model describes each pass), fix",
     "request": "experiments/minebench/v2/request.txt", "comparable": False, "blocks": 61098, "minutes": 29, "size": "206 x 66 x 169",
     "verdict": "Much better parts: a long boiler on its frame with gold domes, bell and red bands, a cab with windows, a cowcatcher, a "
                "big smoke plume, and real passenger cars (clerestory roofs, windows on both sides, stripes, end railings), a platform, "
                "depot and water tower. One big miss: both passenger cars and their railings float in the air beside the plate, off "
                "the track, and the wheels are thin rings. It looked at each pass through the vision model, which called the scene "
                "recognisable and missed the floating cars: describing a picture is not checking it. Next: an objective floating-parts "
                "check in the runtime, and pointed questions for the look."},
    {"id": "v3", "title": "Choo-choo train, coached and told to fix its floating parts",
     "model": "Purple Skippy: Claude Code on Skippy Core's coder (Qwen3.6-35B-A3B, MTP, Q8), local and free",
     "prompt": "BUILD_REQUEST_V3: v2's specification, start from the v2 build, and the runtime's floating-parts report must reach zero "
               "(smoke excepted) with pointed questions answered before stopping",
     "request": "experiments/minebench/v3/request.txt", "comparable": False, "blocks": 67079, "minutes": 42, "size": "206 x 66 x 169",
     "verdict": "A negative result, kept as one. It read the floating-parts report correctly but could not reason its way to the "
                "coordinate fix: 119 floating pieces remain (from 122), the locomotive still rests on nothing and both cars still float "
                "beside the plate. It also broke two of its rules: it stopped while the check still failed, and it offered to set up "
                "a remote and push. Long self-correction, not the first build, is where the local model breaks down."},
    {"id": "v4", "title": "Choo-choo train, the one line again, with its own plan and checks",
     "model": "Purple Skippy: Claude Code on Skippy Core's coder (Qwen3.6-35B-A3B, MTP, Q8), local and free; no spec from us, only "
              "the process: write its own plan, build bottom-up, reach zero floating parts, render and look, at most three passes",
     "prompt": "a choo-choo train", "request": "experiments/minebench/v4/request.txt", "comparable": True, "blocks": 74451,
     "minutes": 41, "size": "128 x 25 x 180",
     "verdict": "It passed the check by gaming it. The runtime says zero floating parts, but to get there it turned the round boiler "
                "into a square box and deleted the smoke, and its wheels are two huge black discs beside the track rather than wheels "
                "on the rails. The passenger car, station platform and trees are fine. It also broke the one hard rule: it made a "
                "private GitHub repo and pushed to it although the request said never push. A plan and an objective check do not "
                "make the local model better at long self-correction; it optimises the number, not the train."},
    {"id": "v5", "title": "Choo-choo train, the one line again, after the shake-down",
     "model": "Purple Skippy: Claude Code on Skippy Core's coder (Qwen3.6-35B-A3B, MTP, Q8), local and free, with the tuned harness "
              "(the workshop prompt as of commit 8f5f8d1, compaction hook included) and the same one-line request as v1",
     "prompt": "a choo-choo train", "request": "experiments/minebench/v5/request.txt", "comparable": True, "blocks": 121811,
     "minutes": 9, "size": "256 x 30 x 256",
     "verdict": "The best purple Skippy build so far, and the first with everything where it belongs: an iron locomotive with a "
                "domed boiler, cab, spoked wheels, cowcatcher and a smoke plume, an oak tender and a red and a blue passenger car, all "
                "coupled and on the track in a line, a station with platform, pillars and lamps beside it. It found and fixed its own "
                "bug (866 blocks below the ground) with the runtime, and finished clean: zero floating parts, nothing outside the grid. "
                "Weaker: it filled the whole 256 x 256 plot with several hundred trees in a rigid lattice (its summary says 60+), "
                "which swamps the train, and the locomotive is pale and plain next to the coloured cars. Nine minutes, against "
                "13 for v1 and 42 for the coached v3."},
    {"id": "v6", "title": "Choo-choo train, the one line again, five days on",
     "model": "Purple Skippy: Claude Code on Skippy Core's coder (the same Qwen3.6-35B-A3B, MTP, Q8 as v5), local and free, with the "
              "harness as of commit e2d7a02 (context discipline: scratch notes, more segments, a trimmed prompt); the same message as v5, word for word",
     "prompt": "a choo-choo train", "request": "experiments/minebench/v6/request.txt", "comparable": True, "blocks": 23104,
     "minutes": 28, "size": "102 x 50 x 151",
     "verdict": "Almost flawless, in the Captain's judgement: the most detailed locomotive yet (an iron boiler with bands, stack, "
                "dome, cab windows, cowcatcher, spoked drivers and a leading truck, a tall curling smoke plume) on a track with a "
                "station and trees. The one real mistake is the wheels: every one is drawn facing along the track instead of rolling "
                "down it (the code draws each in the x-y plane while the track runs along z), a mistake the Captain has seen before "
                "on vehicles. The same lost sense of forward strings the tender and the passenger car out sideways beside the track "
                "instead of behind the engine. The fix is one rule, now in purple Skippy's voxel skill and its header: name the "
                "forward axis once; wheels stand in the forward-and-up plane; every car follows along forward. Smaller: the plume "
                "and a few trim blocks float (8 parts, against v5's zero), and it is a fifth of v5's blocks in three times the "
                "time, with the same model and the same words."},
    {"id": "v7", "title": "Choo-choo train, the same words, the wheel rule in the tool (a negative result)",
     "model": "Purple Skippy: Claude Code on Skippy Core's coder (the same Qwen3.6-35B-A3B, MTP, Q8), local and free, the same message as v5 "
              "word for word; the voxel skill now carries the forward-axis rule",
     "prompt": "a choo-choo train", "request": "experiments/minebench/v7/request.txt", "comparable": True, "blocks": 129664,
     "minutes": None, "size": "171 x 85 x 86",
     "verdict": "A negative result, kept. The wheels are still turned: the runtime's wheel check finds eight black discs whose faces "
                "point along the track, the same mistake as v6, although the one rule was in the skill and in the agent's header. The "
                "one fix it made was to the boiler loops (commit 0a910d4), which stopped blocks floating past the shell. 31 floating "
                "parts remain (44 blocks, small iron pieces at the driving wheels). It is three times v5's blocks, and the rule the "
                "Captain asked for is not yet enforced by the builder, only written down."},
    {"id": "v8", "title": "Choo-choo train, the same words, a wider scene (a negative result)",
     "model": "Purple Skippy: Claude Code on Skippy Core's coder (the same Qwen3.6-35B-A3B, MTP, Q8), local and free, the same message as v5 "
              "word for word; the runtime now checks the wheels against the direction of travel",
     "prompt": "a choo-choo train", "request": "experiments/minebench/v8/request.txt", "comparable": True, "blocks": 191612,
     "minutes": None, "size": "256 x 87 x 256",
     "verdict": "A negative result, kept. The agent covered the whole 256 x 256 plot with grass, so the locomotive is a small piece in "
                "the middle of a field, and it added a second track, lamp posts, a water crane and a thicker plume. The wheels are iron "
                "blocks lying flat under the boiler instead of upright wheels, which the check flags as lying flat. The runtime counts "
                "381 floating parts (489 blocks): mostly small white wool pieces, probably steam, and some glass that is not steam. The "
                "scene reads as a small town with a plume of smoke, not a train. Two debug scripts were left uncommitted in the folder."},
    {"id": "v9", "title": "Choo-choo train, the same words, wheels buried in the ground (a negative result)",
     "model": "Purple Skippy: Claude Code on Skippy Core's coder (the same Qwen3.6-35B-A3B, MTP, Q8), local and free, the same message as v5 "
              "word for word",
     "prompt": "a choo-choo train", "request": "experiments/minebench/v9/request.txt", "comparable": True, "blocks": 166589,
     "minutes": None, "size": "216 x 82 x 108",
     "verdict": "A negative result, kept. No wheel can be seen: the three driving wheels are brick rings round an iron hub, centred "
                "at ground level and drawn facing along the track, so the ground and the sleepers cover them. The agent never opened "
                "the voxel-build skill, which has a tested wheel helper, and the copy of the check in its folder was an old one with no "
                "wheel check, so it saw no problem: zero floating parts, committed as clean. The ground and the coal use Math.random "
                "instead of the seeded rng, so each run of the build is slightly different. Tool fixes from this run: the folder now "
                "gets the current check, which finds wheels of mixed materials (these three: facing along the track) and prints the "
                "wheel helper itself when wheels are turned or missing."},
    {"id": "haiku-direct", "title": "Choo-choo train, Claude Haiku in one call (the benchmark's own way)",
     "model": "Claude Haiku 5.5 through Anthropic's API: one call with MineBench's prompt, no tools, no second try",
     "prompt": "a choo-choo train", "comparable": True, "blocks": 21048, "minutes": 2, "size": "144 x 40 x 42",
     "verdict": "Compact and coherent: a black locomotive with a curling smoke plume, a gold bell and a cab, a tender, two red "
                "passenger cars with windows and a yellow stripe, a platform with a canopy, trees. Everything sits on the track (3 "
                "tiny floating pieces). Fewer blocks than purple Skippy's v1, but the best composition: every part where it belongs."},
    {"id": "haiku-agent", "title": "Choo-choo train, Claude Haiku as the agent (purple Skippy's v1 setup)",
     "model": "Claude Haiku 5.5 in purple Skippy's place: the same Claude Code agent, instructions, folder, runtime and one-line "
              "request as v1, context capped at 138K. About $1.15 of API use",
     "prompt": "a choo-choo train", "comparable": True, "blocks": 61071, "minutes": 4, "size": "217 x 52 x 66",
     "verdict": "The fullest scene: a locomotive with cab, dome, bell, cowcatcher and a big smoke plume, a coal tender, coaches with "
                "rows of windows and a caboose with a cupola, all on the track, a raised platform, a brick-and-wood station house, a "
                "pond and trees. The coaches run together into one long body. Like-for-like with purple Skippy's v1 (only the model "
                "differs), it is clearly better, and three times faster."},
)
