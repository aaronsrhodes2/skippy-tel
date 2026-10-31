"""tools/token_savings.py: Haiku's price tag on the metered tokens, less the electricity above idle."""
import json
from datetime import datetime, timedelta, timezone

import token_savings as ts

NOW = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)


def row(days_ago=0.0, **kw):
    when = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"ts": when, "model": "chat", "in": 50_000, "cached": 0, "out": 1_000_000, "ms": 3_600_000,
            "w": 112.0, "idle_w": 12.0, "overlap": 1, **kw}


def test_haiku_55_list_price_input_cache_output():
    assert abs(ts.haiku_cost({"in": 100_000, "cached": 0, "out": 0}) - 0.01) < 1e-9
    assert abs(ts.haiku_cost({"in": 0, "cached": 100_000, "out": 0}) - 0.001) < 1e-9
    assert abs(ts.haiku_cost({"in": 0, "cached": 0, "out": 1_000_000}) - 0.50) < 1e-9
    assert abs(ts.haiku_cost({"in": 50_000, "cached": 0, "out": 1_000_000}, "haiku-4.5") - 5.05) < 1e-9


def test_a_prompt_over_100k_is_priced_at_haikus_long_context_rate():
    assert abs(ts.haiku_cost({"in": 100_001, "cached": 0, "out": 0}) - 100_001 * 0.50 / 1e6) < 1e-9


def test_only_watts_above_idle_for_the_time_it_ran_are_charged():
    # 100 W above idle for one hour = 0.1 kWh
    assert abs(ts.extra_kwh(row()) - 0.1) < 1e-9
    assert abs(ts.extra_kwh(row(overlap=2)) - 0.05) < 1e-9          # shared with a request running at the same time
    assert ts.extra_kwh(row(w=5.0)) == 0.0                          # below idle never makes a negative cost
    assert ts.extra_kwh(row(w=None)) == 0.0


def test_net_saving_is_haiku_minus_electricity_per_period():
    s = ts.summarize([row(0.01), row(3), row(30)], now=NOW, rate=0.30)
    p = s["periods"]
    assert (p["today"]["calls"], p["7 days"]["calls"], p["all time"]["calls"]) == (1, 2, 3)
    one = p["today"]
    assert abs(one["haiku"] - 0.505) < 1e-9 and abs(one["electricity"] - 0.03) < 1e-9 and abs(one["net"] - 0.475) < 1e-9
    assert abs(p["all time"]["haiku45"] - 15.15) < 1e-9


def test_it_can_lose_money_and_says_so():
    s = ts.summarize([row(0.01, **{"in": 10, "out": 10})], now=NOW, rate=0.30)
    assert s["periods"]["today"]["net"] < 0


def test_report_prints_the_assumptions_next_to_the_numbers(tmp_path):
    (tmp_path / "tokens-2026-10.jsonl").write_text("\n".join(json.dumps(r) for r in [row(0.01), row(2)]) + "\nnot json\n")
    out = ts.render(ts.summarize(ts.load_dir(tmp_path), now=NOW))
    for needle in ("Haiku 5.5", "NET SAVED", "GUESS", "idle", "Not counted", "by model"):
        assert needle in out
    assert ts.main(["--dir", str(tmp_path), "--json"]) == 0


def test_empty_meter_is_not_a_crash():
    out = ts.render(ts.summarize([], now=NOW))
    assert "nothing recorded yet" in out
