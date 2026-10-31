"""The arithmetic of the token tally: Haiku's price tag on metered tokens, less the electricity above idle.

Shared by tools/token_savings.py (the report) and core/reporter/reporter.py (the one line the portal's Status map shows), so both
always agree. Stdlib only. Prices are Anthropic list prices, USD per million tokens.
"""
from __future__ import annotations

import json
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
