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


SIGMA_FIX = SIGMA / "sigma_ground" / "field" / "interface" / "fixtures"


def sky_section(scoreboard: Path, eros_fit: Path) -> dict | None:
    """The telescope arena (sigma-ground C27/C28), arranged for the page: per tracked body the daily scoreboard's windows (ours and JPL against fresh MPC positions, the
    telescopes' own scatter), and for Eros the out-of-sample test of OUR orbit solution. Nothing is computed; the arcseconds are the scoreboard's."""
    if not scoreboard.is_file():
        return None
    sb = json.loads(scoreboard.read_text(encoding="utf-8"))
    bodies = []
    for b in sb.get("bodies", []):
        rows = sorted((s for s in b.get("series", []) if s.get("n_scored")), key=lambda s: s["lead_days"])
        if not rows:
            continue
        bodies.append({"number": b["body"], "name": b["name"].split(" (")[0].split(" ", 1)[-1] if b["name"][:1].isdigit() else b["name"], "start_date": _jd_date(b["start_epoch_jd_tdb"]),
                       "windows": [{"lead_years": s["lead_days"] / 365.25, "to": s["window_utc"][1][:10], "n": s["n_scored"], "stations": s.get("stations"),
                                    "ours": s["ours_median_arcsec"], "jpl": s["jpl_median_arcsec"], "sigma": s.get("jpl_sigma_arcsec")} for s in rows]})
    out = {"generated_utc": sb.get("generated_utc"), "ledger": sb.get("ledger"), "units": "arcsec on the sky, median of |observed minus predicted| over the window's observations",
           "how": "each body is propagated by our engine from one fixed start and never re-fitted; every new month of Minor Planet Center observations scores that ageing prediction and JPL's own for the same instants",
           "bodies": bodies}
    if eros_fit.is_file():
        f = json.loads(eros_fit.read_text(encoding="utf-8"))
        m = f["measured"]
        out["eros_fit"] = {"ledger": f["ledger"], "fit_window": f["fit_window"], "observations_used": f["observations_used"], "observations_total": f["observations_total"], "fit_rms_arcsec": f["fit_rms_arcsec"],
                           "n_predicted_observations": m["n_predicted_observations"], "curves": m["curves"], "per_year": m["per_year"], "ours_median_arcsec": m["ours_median_arcsec"], "jpl_median_arcsec": m["jpl_median_arcsec"]}
    return out


def _jd_date(jd: float) -> str:
    return (dt.datetime(2000, 1, 1, 12) + dt.timedelta(days=jd - 2451545.0)).strftime("%Y-%m-%d")


INNER = ("Mercury", "Venus", "Earth", "Moon", "Mars")              # where DE440 and DE442 agree to <= 0.08 km, so an "ours" scored against DE440 is the same number against DE442


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", type=Path, nargs="+", help="contest jsonl/json files; a kernels-only file (scripts/jpl_vintages_vs_reality.py, no eO) supplies JPL and the reality band against DE442")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--sky", type=Path, default=SIGMA_FIX / "mpc_live" / "scoreboard_portal.json", help="sigma-ground's daily MPC scoreboard (C27): ours and JPL against fresh telescope positions")
    ap.add_argument("--eros-fit", type=Path, default=SIGMA_FIX / "small_body_starts" / "own_fit_433.json", help="sigma-ground's own Eros orbit solution and its out-of-sample test (C28)")
    a = ap.parse_args()
    sky = sky_section(a.sky, a.eros_fit)
    runs = [r for p in a.sources for r in load_runs(p)]
    rel = releases()
    gm = masses()
    truths = {r.get("truth", "DE440") for r in runs}
    truth = "DE442" if "DE442" in truths else sorted(truths)[0]
    vintages = sorted({r["vintage"] for r in runs if r["vintage"] in rel}, key=lambda v: rel[v])
    cells: dict = {}                                               # (body, vintage, target_year) -> {"ours": [], "jpl": [], "band": []}
    ours_truths: set = set()
    for r in runs:
        v = r["vintage"]
        if v not in rel:
            continue
        r_truth = r.get("truth", "DE440")
        start_year = 2000 + round((r["jd"] - 2451545.0) / 365.25)
        for body, leads in r["bodies"].items():
            for lead, x in leads.items():
                target = start_year + int(round(float(lead)))
                c = cells.setdefault((body, v, target), {"ours": [], "jpl": [], "jpl_truth": set(), "band": [], "ours_truth": set()})
                if "eO" in x and (r_truth == truth or body in INNER):            # ours against the page's reality, or where the two realities are the same number
                    if r_truth == truth and truth not in c["ours_truth"]:        # a run against the page's reality (the current model) replaces the older contest's cell
                        c["ours"] = []; c["ours_truth"] = {truth}
                    if r_truth == truth or not c["ours_truth"]:
                        c["ours"].append(x["eO"]); c["ours_truth"].add(r_truth); ours_truths.add(r_truth)
                if "eV" in x:
                    if r_truth == truth and truth not in c["jpl_truth"]:          # a row against the page's reality replaces any against an older one
                        c["jpl"] = []; c["jpl_truth"] = {truth}
                    if r_truth == truth or not c["jpl_truth"]:
                        c["jpl"].append(x["eV"]); c["jpl_truth"].add(r_truth)
                if x.get("eT") is not None:
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
                ours[i][j] = float(np.median(c["ours"])) if c["ours"] else None
                jpl[i][j] = float(np.median(c["jpl"])) if c["jpl"] else None
                n[i][j] = len(c["ours"])
                band_cols.setdefault(j, []).extend(c["band"])
        blankets[b] = {"ours": ours, "jpl": jpl, "n": n, "band": [float(np.median(band_cols[j])) if band_cols.get(j) else None for j in range(len(targets))]}
    # the composite: the mass-weighted mean over the bodies that have a value in that cell (the error of the system's centre of mass, if every body were off in the same direction)
    def weighted(key):
        # a composite cell exists only when EVERY body has both ours and JPL there: a composite of the inner planets alone is not the same quantity as one of the whole system
        grid = []
        for i in range(len(vintages)):
            row = []
            for j in range(len(targets)):
                vals = [blankets[b][key][i][j] for b in bodies]
                complete = all(v is not None for v in vals) and (key == "jpl" or all(blankets[b]["jpl"][i][j] is not None for b in bodies))
                row.append(sum(gm[b] * v for b, v in zip(bodies, vals)) / sum(gm[b] for b in bodies) if complete else None)
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

    # by body: each body counts once; the cell is the MEDIAN over the bodies (the giants' hundreds of km do not swamp the Moon's fraction of one), on the same complete-cell rule
    def by_body(key):
        grid = []
        for i in range(len(vintages)):
            row = []
            for j in range(len(targets)):
                vals = [blankets[b][key][i][j] for b in bodies]
                complete = all(v is not None for v in vals) and (key == "jpl" or all(blankets[b]["jpl"][i][j] is not None for b in bodies))
                row.append(float(np.median(vals)) if complete else None)
            grid.append(row)
        return grid
    band_body = [float(np.median([blankets[b]["band"][j] for b in bodies])) if all(blankets[b]["band"][j] is not None for b in bodies) else None for j in range(len(targets))]
    composite_body = {"ours": by_body("ours"), "jpl": by_body("jpl"), "band": band_body, "note": "median over the ten bodies, each counted once"}
    # by lead: at a fixed lead (1..5 yr), the error against the target year, every ephemeris and start pooled: does a later issue predict better at the same lead?
    lead_cells: dict = {}
    for r in runs:
        if r["vintage"] not in rel:
            continue
        start_year = 2000 + round((r["jd"] - 2451545.0) / 365.25)
        r_truth = r.get("truth", "DE440")
        for body, leads in r["bodies"].items():
            for lead, x in leads.items():
                L = int(round(float(lead)))
                if L < 1 or L > 5:
                    continue
                c = lead_cells.setdefault((body, L, start_year + L), {"ours": [], "jpl": [], "jpl_truth": set(), "ours_truth": set()})
                if "eO" in x and (r_truth == truth or body in INNER):
                    if r_truth == truth and truth not in c["ours_truth"]:
                        c["ours"] = []; c["ours_truth"] = {truth}
                    if r_truth == truth or not c["ours_truth"]:
                        c["ours"].append(x["eO"]); c["ours_truth"].add(r_truth)
                if "eV" in x:
                    if r_truth == truth and truth not in c["jpl_truth"]:
                        c["jpl"] = []; c["jpl_truth"] = {truth}
                    if r_truth == truth or not c["jpl_truth"]:
                        c["jpl"].append(x["eV"]); c["jpl_truth"].add(r_truth)
    by_lead = {}
    for b in bodies:
        by_lead[b] = {}
        for L in range(1, 6):
            ts = sorted(t for (bb, LL, t) in lead_cells if bb == b and LL == L and lead_cells[(bb, LL, t)]["jpl"])
            if ts:
                med = lambda key, t: float(np.median(lead_cells[(b, L, t)][key])) if lead_cells[(b, L, t)][key] else None
                by_lead[b][str(L)] = {"targets": ts, "ours": [med("ours", t) for t in ts], "jpl": [med("jpl", t) for t in ts], "n": [len(lead_cells[(b, L, t)]["ours"]) for t in ts]}
    ours_runs = [r for r in runs if any("eO" in x for d in r["bodies"].values() for x in d.values())]
    first = ours_runs[0] if ours_runs else runs[0]
    ours_note = ("ours: " + first.get("predictor", "the 2026-10-12 headline stack") + ", scored against " + ", ".join(sorted(ours_truths)) +
                 ("; shown for the inner system only until the DE442 rerun lands (DE440 and DE442 agree there to under 0.1 km)" if ours_truths and truth not in ours_truths else ""))
    data = {"meta": {"source": ", ".join(p.name for p in a.sources), "truth": truth, "predictor": first.get("predictor", "the 2026-10-12 headline stack"), "ours_note": ours_note,
                     "words": WORDS, "issue_years": [round(rel[v], 2) for v in vintages], "vintages": vintages, "targets": targets, "bodies": bodies,
                     "runs": len(ours_runs), "kernel_rows": len(runs) - len(ours_runs), "built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "how": "cell = median over the runs of that ephemeris whose lead lands on that target year; empty before the release; band = DE440 against DE442 at the target"},
            "blankets": blankets, "composite": composite, "composite_body": composite_body, "by_lead": by_lead, "sky": sky}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"{a.out} ({a.out.stat().st_size // 1024} KB): {len(vintages)} ephemerides x {len(targets)} target years, {len(bodies)} bodies, {len(runs)} runs, truth {data['meta']['truth']}")


if __name__ == "__main__":
    main()
