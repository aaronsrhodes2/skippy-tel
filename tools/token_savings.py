#!/usr/bin/env python3
"""What SkippyTel's local models saved against a paid Haiku, net of electricity: the running tally.

The data is skippy-core's token meter (core/meter/meter.py): one JSONL line per request llama-swap served, with tokens and the box's
measured power. This reads it (over ssh by default, or --dir for a local copy) and prints today / 7 days / all time, with the assumptions
next to the numbers.

    python tools/token_savings.py                 # reads skippy-core over ssh
    python tools/token_savings.py --dir ./meter   # a local copy of /var/lib/skippy-core/meter
    python tools/token_savings.py --json          # the same numbers as JSON

Honest by construction: Haiku is priced at list, cached prompt tokens at its cache-read rate (a paid model would cache the same prefix),
the electricity charged is ONLY the watts above the box's idle while a request ran (idle is paid either way), and what is not counted is
printed at the bottom. "Saved" is not "would have spent": nobody would have sent these calls to a paid API at this volume, and a local
Qwen is not Haiku. It is the price tag on the work, not a budget.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "meter"))
from savings import DEFAULT_RATE, DEFAULT_IDLE_W, PRICES, PRIMARY, haiku_cost, extra_kwh, blank, add, summarize, load_dir  # noqa: E402,F401

CORE_METER_DIR = "/var/lib/skippy-core/meter"

def load_ssh(host: str = "skippy-core") -> list[dict]:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, f"sudo sh -c 'cat {CORE_METER_DIR}/tokens-*.jsonl'"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and not r.stdout:
        raise SystemExit(f"cannot read the meter on {host}: {r.stderr.strip() or 'ssh failed'}")
    rows = []
    for line in r.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def money(x: float) -> str:
    return f"${x:,.4f}" if abs(x) < 1 else f"${x:,.2f}"


def fmt_tokens(n: float) -> str:
    return f"{n:,.0f}" if n < 100_000 else f"{n / 1e6:,.2f}M"


def render(s: dict) -> str:
    rate = s["rate"]
    lines = ["SkippyTel local AI vs a paid Haiku (counted from skippy-core's llama-swap)", ""]
    head = f"{'':10}{'calls':>8}{'tokens in':>12}{'cached':>10}{'tokens out':>12}{'Haiku 5.5':>11}{'power':>9}{'NET SAVED':>11}{'vs Haiku 4.5':>14}"
    lines.append(head)
    for name, t in s["periods"].items():
        lines.append(f"{name:10}{t['calls']:>8,}{fmt_tokens(t['in']):>12}{fmt_tokens(t['cached']):>10}{fmt_tokens(t['out']):>12}"
                     f"{money(t['haiku']):>11}{money(t['electricity']):>9}{money(t['net']):>11}{money(t['net45']):>14}")
    a = s["periods"]["all time"]
    lines += ["", f"all time: {a['seconds'] / 3600:,.2f} h of generating, {a['kwh'] * 1000:,.1f} Wh above idle; "
              f"counting since {s['since'] or 'nothing recorded yet'}", ""]
    if s["models"]:
        lines.append("by model (all time):")
        for m, t in sorted(s["models"].items(), key=lambda kv: -kv[1]["haiku"]):
            lines.append(f"  {m:14}{t['calls']:>7,} calls  {fmt_tokens(t['in'] + t['cached']):>9} in  {fmt_tokens(t['out']):>9} out  "
                         f"Haiku would be {money(t['haiku'])}")
        lines.append("")
    lines += [
        "Assumptions (read these next to the numbers):",
        f"  Haiku 5.5 list price (read {PRICES['haiku-5.5']['as_of']}): $0.10 in / $0.50 out per million, $0.01 cached input; "
        "$0.50 / $2.50 for prompts over 100k.",
        f"  Haiku 4.5 (the column on the right): $1 in / $5 out per million, $0.10 cached. Cached prompt tokens are priced at the cache-read rate.",
        f"  Electricity {rate * 100:.0f} cents/kWh is a GUESS (set ELECTRICITY_USD_PER_KWH; the real rate is not in the repo or vault).",
        "  Power is measured: skippy-core's amdgpu package power (CPU + iGPU, per second). Only watts ABOVE the idle baseline are charged,",
        "  for the time each request ran, shared when requests overlap. The box's idle draw is paid whether or not it works. The wall draw",
        "  (PSU loss, SSD, fan) is a little higher than the package number, so this under-counts power by perhaps 10-20 %.",
        "Not counted: skippy-pc's GPU work (pictures, songs, speech, whisper: no honest token equivalent), the Claude/orange-Skippy traffic",
        "  (that is paid anyway), pre-meter history, and any call that never reached llama-swap.",
        "Caveats: a local Qwen is not Haiku (quality differs); 'saved' is the price tag of the work, not money you would otherwise have spent;",
        "  output tokens include the model's own thinking when it thinks; agent runs on the coder re-send long prompts, which a paid API would",
        "  cache too (hence the cache-read pricing), but cache-hit rates would differ.",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", help="a local copy of the meter directory (default: read skippy-core over ssh)")
    ap.add_argument("--host", default="skippy-core")
    ap.add_argument("--rate", type=float, default=float(os.environ.get("ELECTRICITY_USD_PER_KWH", DEFAULT_RATE)), help="USD per kWh")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = load_dir(Path(a.dir)) if a.dir else load_ssh(a.host)
    s = summarize(rows, rate=a.rate)
    print(json.dumps(s, indent=1) if a.json else render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
