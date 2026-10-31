"""core/meter/meter.py: llama-swap's activity list becomes JSONL lines with tokens and power, once each, and never any text."""
import json
from datetime import datetime, timezone

from core_testlib import load

meter = load("meter/meter.py", "core_meter")

T0 = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def act(i, secs, model="chat", inp=10, cached=100, out=20, ms=2000, status=200):
    ts = datetime.fromtimestamp(T0 + secs, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "timestamp": ts, "src": "ip:172.18.0.8", "model": model, "resp_status_code": status, "duration_ms": ms,
            "tokens": {"input_tokens": inp, "cache_tokens": cached, "output_tokens": out},
            "prompt": "SECRET PROMPT", "response": "SECRET REPLY"}


class Clock:
    t = T0 + 100

    def __call__(self):
        return self.t


def make(tmp_path, rows, watts=lambda: 50.0, clock=None):
    clock = clock or Clock()
    m = meter.Meter(tmp_path, lambda path: {"data": rows}, watts, now=clock)
    for k in range(100):                      # 100 s of one-per-second samples
        clock.t = T0 + k
        m.sample()
    clock.t = T0 + 100
    return m


def lines(tmp_path):
    return [json.loads(x) for f in tmp_path.glob("tokens-*.jsonl") for x in f.read_text().splitlines()]


def test_new_requests_are_written_once_with_tokens_and_measured_watts(tmp_path):
    rows = [act(1, 50)]
    m = make(tmp_path, rows)
    assert m.poll() == 1 and m.poll() == 0
    got = lines(tmp_path)
    assert len(got) == 1
    r = got[0]
    assert (r["in"], r["cached"], r["out"], r["ms"], r["model"]) == (10, 100, 20, 2000, "chat")
    assert r["w"] == 50.0 and r["idle_w"] == meter.DEFAULT_IDLE_W and r["overlap"] == 1


def test_never_any_text_of_the_prompt_or_reply(tmp_path):
    m = make(tmp_path, [act(1, 50)])
    m.poll()
    assert "SECRET" not in "".join(f.read_text() for f in tmp_path.glob("tokens-*.jsonl"))


def test_a_restart_does_not_write_the_same_request_again(tmp_path):
    rows = [act(1, 50), act(2, 60)]
    make(tmp_path, rows).poll()
    again = make(tmp_path, rows)
    assert again.poll() == 0 and len(lines(tmp_path)) == 2


def test_failed_and_empty_requests_are_not_counted(tmp_path):
    rows = [act(1, 50, status=500), act(2, 52, inp=0, cached=0, out=0), act(3, 54)]
    m = make(tmp_path, rows)
    assert m.poll() == 1 and [r["id"] for r in lines(tmp_path)] == [3]


def test_overlapping_requests_know_they_share_the_watts(tmp_path):
    m = make(tmp_path, [act(1, 50, ms=4000), act(2, 51, ms=4000), act(3, 90, ms=1000)])
    m.poll()
    assert [r["overlap"] for r in lines(tmp_path)] == [1, 2, 1]


def test_llama_swap_down_is_just_nothing_this_tick(tmp_path):
    def boom(path):
        raise OSError("down")
    m = meter.Meter(tmp_path, boom, lambda: 20.0)
    assert m.poll() == 0


def test_idle_baseline_is_the_low_decile_of_the_days_minutes(tmp_path):
    m = meter.Meter(tmp_path, lambda p: {"data": []}, lambda: 1.0)
    assert m.idle_w() == meter.DEFAULT_IDLE_W                     # too little data: the measured default
    m.minutes.extend([10.0] * 90 + [100.0] * 10)
    assert m.idle_w() == 10.0


def test_power_file_is_found_by_the_amdgpu_name(tmp_path):
    for n, name in enumerate(["nvme", "amdgpu"]):
        d = tmp_path / f"hwmon{n}"
        d.mkdir()
        (d / "name").write_text(name + "\n")
    (tmp_path / "hwmon1" / "power1_average").write_text("42000000\n")
    pf = meter.find_power_file(str(tmp_path))
    assert pf is not None and meter.read_watts(pf) == 42.0
    assert meter.read_watts(None) is None
