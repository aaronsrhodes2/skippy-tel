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
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Anthropic list prices, USD per million tokens, read 2026-10-30 from platform.claude.com/docs/en/about-claude/pricing.
# (input, cache read, output); Haiku 5.5 steps up for prompts over 100,000 tokens.
PRICES = {
    "haiku-5.5": {"as_of": "2026-10-30", "short": (0.10, 0.01, 0.50), "long": (0.50, 0.05, 2.50), "long_over": 100_000},
    "haiku-4.5": {"as_of": "2026-10-30", "short": (1.00, 0.10, 5.00), "long": (1.00, 0.10, 5.00), "long_over": 10**12},
}
PRIMARY = "haiku-5.5"
DEFAULT_RATE = 0.30            # USD per kWh. NOT from the Captain's bill (not in the repo or the vault): a deliberately high US guess.
DEFAULT_IDLE_W = 12.0
CORE_METER_DIR = "/var/lib/skippy-core/meter"


def haiku_cost(row: dict, model: str = PRIMARY) -> float:
    p = PRICES[model]
    prompt = row.get("in", 0) + row.get("cached", 0)
    i, c, o = p["long"] if prompt > p["long_over"] else p["short"]
    return (row.get("in", 0) * i + row.get("cached", 0) * c + row.get("out", 0) * o) / 1e6


def extra_kwh(row: dict) -> float:
    """Energy above idle for the time this request ran, shared with whatever ran at the same moment."""
    w = row.get("w")
    if w is None:
        return 0.0
    extra = max(0.0, w - row.get("idle_w", DEFAULT_IDLE_W))
    return extra * (row.get("ms", 0) / 1000) / max(1, row.get("overlap", 1)) / 3.6e6


def blank() -> dict:
    return {"calls": 0, "in": 0, "cached": 0, "out": 0, "seconds": 0.0, "kwh": 0.0, "haiku": 0.0, "haiku45": 0.0, "no_power": 0}


def add(t: dict, row: dict) -> None:
    t["calls"] += 1
    t["in"] += row.get("in", 0)
    t["cached"] += row.get("cached", 0)
    t["out"] += row.get("out", 0)
    t["seconds"] += row.get("ms", 0) / 1000
    t["kwh"] += extra_kwh(row)
    t["haiku"] += haiku_cost(row, "haiku-5.5")
    t["haiku45"] += haiku_cost(row, "haiku-4.5")
    t["no_power"] += row.get("w") is None


def summarize(rows: list[dict], now: datetime | None = None, rate: float = DEFAULT_RATE) -> dict:
    """Totals for today (local midnight on), the last 7 days, and everything; plus per-model for all time."""
    now = (now or datetime.now(timezone.utc)).astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cuts = {"today": midnight, "7 days": now - timedelta(days=7), "all time": None}
    out = {k: blank() for k in cuts}
    models: dict[str, dict] = {}
    first = None
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        first = ts if first is None or ts < first else first
        for k, cut in cuts.items():
            if cut is None or ts >= cut:
                add(out[k], row)
        add(models.setdefault(row.get("model", "?"), blank()), row)
    for t in [*out.values(), *models.values()]:
        t["electricity"] = t["kwh"] * rate
        t["net"] = t["haiku"] - t["electricity"]
        t["net45"] = t["haiku45"] - t["electricity"]
    return {"periods": out, "models": models, "since": first.isoformat() if first else None, "rate": rate}


def load_dir(path: Path) -> list[dict]:
    rows = []
    for f in sorted(path.glob("tokens-*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


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
