"""Re-run sigma-ground's rolling-window N-body shootout (the one behind the portal's N-body experiment) with the integrator as it is now
(the 1PN sign fixes of 2026-10-10 included) and keep the raw samples for the flip-book (scripts/nbody_flipbook_build.py turns them into the
page's data).

It only READS sigma-ground: the shootout module writes its results to a file of its own, so that path is pointed at ours before it runs,
and nothing under sigma-ground is touched (another session works there). The numbers are whatever its integrator and the JPL DE440
snapshots give; nothing is calculated here.

    python scripts/nbody_run.py --out build/nbody_raw.json                 # all 16 windows, 5 years each, every predictor
    python scripts/nbody_run.py --out /tmp/one.json --windows j2015        # a quick try
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SIGMA = Path(os.environ.get("SIGMA_GROUND_DIR", r"D:\Aaron\development\sigma-ground"))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SIGMA))

ALL_WINDOWS = [f"j{y}" for y in range(2005, 2021)]


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(SIGMA), *args], capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def frames(rs, de440: dict, predictor_name: str, window: str, kind: str) -> dict:
    """Positions of every body at many instants of one integration, for the flip-book's time-lapse plates: 'inner' is five years
    sampled monthly (with DE440's own positions at the yearly snapshots beside them), 'jovian' is ninety days sampled daily (the moons
    go round far too fast for a monthly sample). Straight from the shootout's own integrator; positions in km, heliocentric."""
    import numpy as np
    pred = next(p for p in rs.PREDICTORS if p.name == predictor_name)
    bodies, names, sun_idx, parent_idx = rs._build_bodies_at_snapshot(window, de440, pred)
    start = de440["snapshots"][window]["epoch"]["jd_tdb"]
    if kind == "inner":
        step, count = 365.25 / 12, 61
    else:
        step, count = 1.0, 91
    jds = [start + k * step for k in range(count)]
    pos = rs._integrate_nbody(pred, bodies, names, sun_idx, start, jds, parent_attractor_indices=parent_idx)
    out = {"kind": kind, "predictor": predictor_name, "window": window, "start_jd": start, "step_days": step, "jds": jds,
           "bodies": {n: np.round(a, 1).tolist() for n, a in pos.items() if n != "Sun"}}
    if kind == "inner":
        truth = {}
        for key, snap in de440["snapshots"].items():
            jd = snap["epoch"]["jd_tdb"]
            if start - 0.5 <= jd <= jds[-1] + 0.5:
                for n in names:
                    v = rs._de440_heliocentric_km(de440, key, n)
                    if v is not None and n != "Sun":
                        truth.setdefault(n, []).append([jd] + [round(float(x), 1) for x in v])
        out["truth"] = truth
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--windows", nargs="*", default=ALL_WINDOWS)
    ap.add_argument("--horizon-yr", type=float, default=5.0)
    ap.add_argument("--predictors", nargs="*", default=None, help="names from rolling_shootout.PREDICTORS (default: the portal's set)")
    ap.add_argument("--frames-predictor", default="jpl_de440", help="the predictor the time-lapse plates are drawn from ('' for none)")
    ap.add_argument("--frames-window", default="j2010")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    import sigma_ground.field.interface.rolling_shootout as rs
    rs._RESULTS_FILE = out.with_suffix(".shootout.json")         # never the repo's own fixture
    chosen = args.predictors or ["pure_newton", "standard", "over_physics_no_j2", "over_physics", "over_physics_finedt", "jpl_de440", "kepler"]
    preds = [p for p in rs.PREDICTORS if p.name in chosen]
    t0 = time.time()
    samples = rs.run_rolling_shootout(window_start_keys=args.windows, prediction_horizon_yr=args.horizon_yr,
                                      sample_interval_yr=1.0, predictors=preds, verbose=True)
    fr = {}
    if args.frames_predictor:
        de440 = rs._load_de440()
        for kind in ("inner", "jovian"):
            print(f"frames: {kind} ...", flush=True)
            fr[kind] = frames(rs, de440, args.frames_predictor, args.frames_window, kind)
    meta = {
        "windows": args.windows, "horizon_yr": args.horizon_yr, "sample_interval_yr": 1.0,
        "predictors": [{"name": p.name, "description": p.description, "dt_days": getattr(p, "dt_days", None)} for p in preds],
        "sigma_ground": {"head": git("rev-parse", "HEAD"), "subject": git("log", "-1", "--format=%s"),
                         "dirty_in_interface": git("status", "--short", "sigma_ground/field/interface").splitlines()},
        "ran_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "elapsed_s": round(time.time() - t0, 1),
    }
    out.write_text(json.dumps({"metadata": meta, "samples": [s.__dict__ for s in samples], "frames": fr}, separators=(",", ":")), encoding="utf-8")
    print(f"{len(samples)} samples -> {out} in {meta['elapsed_s']} s")


if __name__ == "__main__":
    main()
