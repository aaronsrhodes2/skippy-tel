"""Turn sigma-ground's fair-contest results into the three-blanket data file: portal/static/demos/nbody/blankets.json.

The contest (sigma-ground scripts/fair_jpl_contest.py) starts our integrator from each JPL ephemeris's OWN state at yearly dates after that ephemeris was issued, runs it forward,
and scores three things against reality (JPL's latest ephemeris, DE442): ours (eO), JPL's prediction at the time, i.e. that ephemeris's own continuation (eV), and, when present,
how far JPL's previous reality (DE440) sits from today's (eT: the width of the reality band). This script only arranges those numbers:

  blankets[body] = {rows: issue years (one per JPL ephemeris, by release), cols: target years, ours: [[km]], jpl: [[km]], n: [[runs]], band: [km per target col]}
  composite      = the same, each cell the mass-weighted mean over the bodies present (weights: the DE440 GM of each body, read from sigma-ground; the Moon and Earth separately)

A cell is the median over every run of that ephemeris whose lead lands on that target year (the start dates differ, the lead with them). Cells before the release are empty:
reality is the edge the blankets hang from. No physics is computed here.

    python scripts/nbody_blankets_build.py <fair_contest_runs.jsonl | fair_contest_contest.json> [--out portal/static/demos/nbody/blankets.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "portal" / "static" / "demos" / "nbody" / "blankets.json"
SIGMA = Path(r"D:\Aaron\development\sigma-ground")
ORDER = ["Mercury", "Venus", "Earth", "Moon", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"]
WORDS = {"ours": "Our prediction", "jpl": "JPL's prediction at the time", "truth": "Reality"}


def load_runs(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    doc = json.loads(text)
    return doc["results"] if isinstance(doc, dict) else doc


def releases() -> dict[str, int]:
    sys.path.insert(0, str(SIGMA / "scripts"))
    sys.path.insert(0, str(SIGMA))
    from vintage_deviance_mesh import VINTAGES                     # (name, file, release date) as the kernels' own comments state them
    return {name: rel.year + (rel.timetuple().tm_yday - 1) / 365.25 for name, _, rel in VINTAGES}


def masses() -> dict[str, float]:
    """DE440's GM per body (km^3/s^2) for the mass weights; the giants and Pluto as their systems, which is what the contest's barycentres are."""
    sys.path.insert(0, str(SIGMA))
    from sigma_ground.field.interface import de440_constants as c
    out = {"Mercury": c.gm_km3_s2("Mercury"), "Venus": c.gm_km3_s2("Venus"), "Earth": c.gm_km3_s2("Earth"), "Moon": c.gm_km3_s2("Moon")}
    for n in ("Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"):
        out[n] = c.gm_km3_s2(f"{n} system")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    runs = load_runs(a.source)
    rel = releases()
    gm = masses()
    vintages = sorted({r["vintage"] for r in runs if r["vintage"] in rel}, key=lambda v: rel[v])
    cells: dict = {}                                               # (body, vintage, target_year) -> {"ours": [], "jpl": [], "band": []}
    for r in runs:
        v = r["vintage"]
        if v not in rel:
            continue
        start_year = 2000 + round((r["jd"] - 2451545.0) / 365.25)
        for body, leads in r["bodies"].items():
            for lead, x in leads.items():
                target = start_year + int(round(float(lead)))
                c = cells.setdefault((body, v, target), {"ours": [], "jpl": [], "band": []})
                c["ours"].append(x["eO"]); c["jpl"].append(x["eV"])
                if "eT" in x:
                    c["band"].append(x["eT"])
    bodies = [b for b in ORDER if any(k[0] == b for k in cells)]
    targets = sorted({k[2] for k in cells})
    blankets = {}
    for b in bodies:
        ours = [[None] * len(targets) for _ in vintages]
        jpl = [[None] * len(targets) for _ in vintages]
        n = [[0] * len(targets) for _ in vintages]
        band_cols: dict = {}
        for i, v in enumerate(vintages):
            for j, t in enumerate(targets):
                c = cells.get((b, v, t))
                if not c:
                    continue
                ours[i][j] = float(np.median(c["ours"])); jpl[i][j] = float(np.median(c["jpl"])); n[i][j] = len(c["ours"])
                band_cols.setdefault(j, []).extend(c["band"])
        blankets[b] = {"ours": ours, "jpl": jpl, "n": n, "band": [float(np.median(band_cols[j])) if band_cols.get(j) else None for j in range(len(targets))]}
    # the composite: the mass-weighted mean over the bodies that have a value in that cell (the error of the system's centre of mass, if every body were off in the same direction)
    def weighted(key):
        grid = []
        for i in range(len(vintages)):
            row = []
            for j in range(len(targets)):
                num = den = 0.0
                for b in bodies:
                    val = blankets[b][key][i][j]
                    if val is not None:
                        num += gm[b] * val; den += gm[b]
                row.append(num / den if den else None)
            grid.append(row)
        return grid
    band = []
    for j in range(len(targets)):
        num = den = 0.0
        for b in bodies:
            val = blankets[b]["band"][j]
            if val is not None:
                num += gm[b] * val; den += gm[b]
        band.append(num / den if den else None)
    composite = {"ours": weighted("ours"), "jpl": weighted("jpl"), "band": band, "weights": {b: gm[b] / sum(gm[x] for x in bodies) for b in bodies},
                 "note": "mass-weighted mean of the bodies' errors (weights: DE440 GM; the giants as their systems). Jupiter and Saturn carry 92 % of it."}
    # by lead: at a fixed lead (1..5 yr), the error against the target year, every ephemeris and start pooled: does a later issue predict better at the same lead?
    lead_cells: dict = {}
    for r in runs:
        if r["vintage"] not in rel:
            continue
        start_year = 2000 + round((r["jd"] - 2451545.0) / 365.25)
        for body, leads in r["bodies"].items():
            for lead, x in leads.items():
                L = int(round(float(lead)))
                if L < 1 or L > 5:
                    continue
                c = lead_cells.setdefault((body, L, start_year + L), {"ours": [], "jpl": []})
                c["ours"].append(x["eO"]); c["jpl"].append(x["eV"])
    by_lead = {}
    for b in bodies:
        by_lead[b] = {}
        for L in range(1, 6):
            ts = sorted(t for (bb, LL, t) in lead_cells if bb == b and LL == L)
            if ts:
                by_lead[b][str(L)] = {"targets": ts, "ours": [float(np.median(lead_cells[(b, L, t)]["ours"])) for t in ts], "jpl": [float(np.median(lead_cells[(b, L, t)]["jpl"])) for t in ts],
                                      "n": [len(lead_cells[(b, L, t)]["ours"]) for t in ts]}
    first = runs[0]
    data = {"meta": {"source": a.source.name, "truth": first.get("truth", "DE440"), "predictor": first.get("predictor", "the 2026-10-12 headline stack"),
                     "words": WORDS, "issue_years": [round(rel[v], 2) for v in vintages], "vintages": vintages, "targets": targets, "bodies": bodies,
                     "runs": len(runs), "built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "how": "cell = median over the runs of that ephemeris whose lead lands on that target year; empty before the release; band = DE440 against DE442 at the target"},
            "blankets": blankets, "composite": composite, "by_lead": by_lead}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"{a.out} ({a.out.stat().st_size // 1024} KB): {len(vintages)} ephemerides x {len(targets)} target years, {len(bodies)} bodies, {len(runs)} runs, truth {data['meta']['truth']}")


if __name__ == "__main__":
    main()
