"""Turn a raw N-body run (scripts/nbody_run.py) into the flip-book's data file: portal/static/demos/nbody/flipbook.json.

Only data handling happens here: for every predictor and body, the median and the quartiles across the windows of the position error
(as the shootout measured it against JPL's DE440, in AU) at each prediction time; the same medians from the earlier run (the shootout's
own fixture from before the 2026-10-10 1PN sign fix) for the before/after plate; and the time-lapse frames as the run recorded them.
No physics is computed.

    python scripts/nbody_flipbook_build.py build/nbody_*.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "portal" / "static" / "demos" / "nbody" / "flipbook.json"
OLD = Path(r"D:\Aaron\development\sigma-ground\sigma_ground\field\interface\fixtures\rolling_shootout_after_dt_fix.json")
YEAR = 365.25

# the order the plates run in: the planets by distance, each with its moons (a classification, not physics)
ORDER = ["Mercury", "Venus", "Earth", "Moon", "Mars", "Phobos", "Deimos", "Jupiter", "Io", "Europa", "Ganymede", "Callisto",
         "Saturn", "Mimas", "Enceladus", "Tethys", "Dione", "Rhea", "Titan", "Uranus", "Miranda", "Ariel", "Umbriel", "Titania", "Oberon", "Neptune", "Triton", "Pluto", "Charon"]
PARENT = {"Moon": "Earth", "Phobos": "Mars", "Deimos": "Mars", "Io": "Jupiter", "Europa": "Jupiter", "Ganymede": "Jupiter", "Callisto": "Jupiter",
          "Mimas": "Saturn", "Enceladus": "Saturn", "Tethys": "Saturn", "Dione": "Saturn", "Rhea": "Saturn", "Titan": "Saturn", "Miranda": "Uranus", "Ariel": "Uranus", "Umbriel": "Uranus", "Titania": "Uranus",
          "Oberon": "Uranus", "Triton": "Neptune", "Charon": "Pluto"}


def curves(samples: list[dict], windows: set | None = None) -> dict:
    """{predictor: {body: {off: [years], med: [AU], q1: [AU], q3: [AU], n: windows}}}, one value per prediction time (year 0 included)."""
    cell: dict = {}
    for s in samples:
        if s.get("error_au") is None or (windows is not None and s["window_start_jd"] not in windows):
            continue
        off = round((s["sample_jd"] - s["window_start_jd"]) / YEAR)
        cell.setdefault(s["predictor"], {}).setdefault(s["body"], {}).setdefault(off, []).append(s["error_au"])
    out: dict = {}
    for pred, bodies in cell.items():
        for body, offs in bodies.items():
            ys = sorted(offs)
            v = [np.array(offs[y]) for y in ys]
            out.setdefault(pred, {})[body] = {
                "years": ys, "med": [float(np.median(a)) for a in v],
                "q1": [float(np.percentile(a, 25)) for a in v], "q3": [float(np.percentile(a, 75)) for a in v], "n": [len(a) for a in v]}
    return out


def main() -> None:
    raws = [json.loads(Path(a).read_text(encoding="utf-8")) for a in sys.argv[1:]]     # several runs (the slow predictors ran on fewer windows)
    raw = {"metadata": {**raws[0]["metadata"], "runs": [r["metadata"] for r in raws],
                        "predictors": [p for r in raws for p in r["metadata"]["predictors"]]},
           "samples": [x for r in raws for x in r["samples"]],
           "frames": {k: v for r in raws for k, v in (r.get("frames") or {}).items()}}
    # Every comparison is made on the windows every predictor was run on (the slow, fine-step ones ran on four), so no predictor is judged on
    # easier or harder start dates than another; the earlier run is cut to the same windows.
    by_pred: dict = {}
    for x in raw["samples"]:
        by_pred.setdefault(x["predictor"], set()).add(x["window_start_jd"])
    shared = set.intersection(*by_pred.values())
    new = curves(raw["samples"], shared)
    old_meta, old = {}, {}
    if OLD.exists():
        d = json.loads(OLD.read_text(encoding="utf-8"))
        old_meta = d.get("metadata", {})
        old = curves([x for x in d["samples"] if x["predictor"] in by_pred], shared)
    bodies = [b for b in ORDER if any(b in v for v in new.values())]
    year = lambda jd: 2000 + round((jd - 2451545.0) / YEAR)          # a label for the window, from its Julian date
    data = {
        "meta": {**raw["metadata"], "bodies": bodies, "parent": PARENT,
                 "shared_windows": [f"J{year(j)}" for j in sorted(shared)], "ran_windows": {p: len(w) for p, w in by_pred.items()},
                 "earlier": {"file": OLD.name, "windows": old_meta.get("n_windows"), "predictors": old_meta.get("predictors"),
                             "note": "the shootout's own fixture from before the 2026-10-10 fix to the 1PN signs (and before its set of bodies grew)"}},
        "new": new, "old": old, "frames": raw.get("frames", {}),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT} ({OUT.stat().st_size // 1024} KB): {len(bodies)} bodies, predictors {sorted(new)}")


if __name__ == "__main__":
    main()
