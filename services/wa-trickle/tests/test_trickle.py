"""wa-trickle: the queue spends a fixed free-tier budget, asks Wolfram once per question ever, and re-asks Mentat per commit."""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parents[1] / "trickle.py"
_spec = importlib.util.spec_from_file_location("wa_trickle", _path)
T = importlib.util.module_from_spec(_spec)
sys.modules["wa_trickle"] = T
_spec.loader.exec_module(T)


class Clock:
    def __init__(self, start="2026-10-14T00:00:00"):
        self.t = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class FakeWolfram:
    def __init__(self):
        self.asked = []
        self.raise_next = None

    def __call__(self, app_id, question):
        if self.raise_next:
            exc, self.raise_next = self.raise_next, None
            raise exc
        self.asked.append(question)
        return {"status": "ok", "pods": [{"id": "Result", "title": "Result", "plaintext": [f"answer to {question}"]}],
                "primary_text": f"answer to {question}", "extra": {}}


class FakeMentat:
    def __init__(self):
        self.ref = "aaaa"
        self.asked = []
        self.down = False

    def ask(self, question):
        if self.down:
            raise T.Unavailable("Qwen is down")
        self.asked.append((self.ref, question))
        return {"ok": True, "result": {"value": f"mentat says {question}"}}

    def version(self):
        return self.ref


@pytest.fixture
def rig(tmp_path):
    clock, wolfram, mentat = Clock(), FakeWolfram(), FakeMentat()
    def make(**kw):
        return T.Trickle(str(tmp_path / "t.sqlite"), wolfram_fn=wolfram, mentat_fn=mentat.ask, version_fn=mentat.version, now_fn=clock,
                         daily_budget=kw.get("daily_budget", 3), monthly_cap=kw.get("monthly_cap", 1900), app_id=kw.get("app_id", "KEY"))
    return type("Rig", (), {"clock": clock, "wolfram": wolfram, "mentat": mentat, "make": staticmethod(make)})


def items(n, prefix="q"):
    return [{"id": f"{prefix}{i}", "question": f"question {prefix}{i}", "priority": i} for i in range(n)]


def test_one_wolfram_call_per_tick_highest_priority_first(rig):
    t = rig.make()
    t.enqueue("s", items(4))
    t.tick()
    assert rig.wolfram.asked == ["question q3"]
    t.tick()
    assert rig.wolfram.asked == ["question q3", "question q2"]


def test_the_daily_budget_is_never_exceeded_and_resets_at_midnight_utc(rig):
    t = rig.make(daily_budget=3)
    t.enqueue("s", items(10))
    for _ in range(8):
        t.tick()
    assert len(rig.wolfram.asked) == 3 and t.calls_today() == 3
    assert "budget is spent" in t.wolfram_step()
    rig.clock.advance(days=1)
    t.tick()
    assert len(rig.wolfram.asked) == 4 and t.calls_today() == 1


def test_the_monthly_cap_stops_the_queue_and_a_new_month_restarts_it(rig):
    t = rig.make(daily_budget=100, monthly_cap=5)
    t.enqueue("s", items(20))
    for _ in range(9):
        t.tick()
    assert len(rig.wolfram.asked) == 5
    assert "month's cap" in t.wolfram_step()
    rig.clock.advance(days=20)                         # 2026-11-03
    t.tick()
    assert len(rig.wolfram.asked) == 6


def test_a_question_is_never_sent_to_wolfram_twice_even_across_restarts(rig):
    t = rig.make(daily_budget=10)
    t.enqueue("s", items(3))
    for _ in range(3):
        t.tick()
    assert sorted(rig.wolfram.asked) == ["question q0", "question q1", "question q2"]
    t2 = rig.make(daily_budget=10)                     # a restart reopens the same file
    t2.enqueue("s", items(3))                          # the same questions enqueued again are ignored
    t2.tick()
    assert len(rig.wolfram.asked) == 3 and "every question has a Wolfram answer" in t2.wolfram_step()


def test_a_network_error_costs_no_call_and_the_question_stays_queued(rig):
    t = rig.make()
    t.enqueue("s", items(1))
    rig.wolfram.raise_next = TimeoutError("slow")
    assert "will retry" in t.wolfram_step()
    assert t.calls_today() == 0
    t.tick()
    assert rig.wolfram.asked == ["question q0"] and t.calls_today() == 1


def test_quota_refusal_pauses_until_next_month(rig):
    t = rig.make()
    t.enqueue("s", items(2))
    rig.wolfram.raise_next = T.WolframQuota("HTTP 403")
    assert "paused until 2026-11-01" in t.wolfram_step()
    assert "paused until" in t.wolfram_step() and rig.wolfram.asked == []
    rig.clock.advance(days=20)
    t.tick()
    assert len(rig.wolfram.asked) == 1


def test_without_an_app_id_it_only_asks_mentat(rig):
    t = rig.make(app_id="")
    t.enqueue("s", items(2))
    t.tick()
    assert rig.wolfram.asked == [] and len(rig.mentat.asked) == 2
    assert "no WOLFRAM_ALPHA_APP_ID" in t.wolfram_step()


def test_mentat_is_asked_again_when_its_commit_changes_but_not_otherwise(rig):
    t = rig.make(daily_budget=0)
    t.enqueue("s", items(3))
    t.tick()
    t.tick()
    assert len(rig.mentat.asked) == 3                                  # once per question at commit aaaa
    rig.mentat.ref = "bbbb"
    t.tick()
    assert len(rig.mentat.asked) == 6 and {r for r, _ in rig.mentat.asked} == {"aaaa", "bbbb"}
    out = {r["id"]: r for r in t.export("s")}
    assert all(r["mentat"]["sigma_ground_ref"] == "bbbb" for r in out.values())          # the export carries the latest commit's answer


def test_mentat_down_spends_no_wolfram_call_on_its_account_and_loses_nothing(rig):
    t = rig.make(daily_budget=10)
    t.enqueue("s", items(2))
    rig.mentat.down = True
    msg = t.tick()
    assert "Qwen is down" in msg and rig.mentat.asked == []
    assert len(rig.wolfram.asked) == 1                                  # Wolfram is independent of Mentat's health
    rig.mentat.down = False
    t.tick()
    assert len(rig.mentat.asked) == 2


def test_historic_answers_are_stored_as_answered_and_never_asked(rig):
    t = rig.make(daily_budget=10)
    n = t.import_answers("old", [{"id": "h1", "question": "free fall 10 m", "wolfram": {"answer_text": "[Result] 1.43 s", "primary_result_pod": "1.43 s"}},
                                 {"id": "h2", "question": "broken", "wolfram": {"answer_text": "<ERROR: timeout>"}},
                                 {"id": "h3", "question": "unparsed by Wolfram", "wolfram": {"answer_text": ""}}])
    assert n == 2
    t.tick()
    assert rig.wolfram.asked == ["broken"]          # h1 is done; h3 is Wolfram's own "no result" (done); h2 was an error, so it is still wanted
    assert next(r for r in t.export("old") if r["id"] == "h3")["wolfram"]["status"] == "no_result"
    row = next(r for r in t.export("old") if r["id"] == "h1")
    assert row["wolfram"]["source"] == "historic" and row["wolfram"]["primary_text"] == "1.43 s"


def test_status_reports_progress_and_the_end_date(rig):
    t = rig.make(daily_budget=2)
    t.enqueue("s", items(10))
    t.tick()
    st = t.status()
    assert st["questions"] == 10 and st["wolfram"]["asked_by_api"] == 1 and st["wolfram"]["waiting"] == 9
    assert st["wolfram"]["days_to_finish_at_budget"] == 4.5 and st["mentat"]["sigma_ground_ref"] == "aaaa"


def test_http_surface(rig):
    t = rig.make()
    c = T.create_app(t).test_client()
    assert c.get("/health").data == b"ok"
    assert c.post("/enqueue", json={"suite": "s", "items": items(2)}).get_json() == {"ok": True, "added": 2}
    assert c.post("/enqueue", json={"suite": "s", "items": items(2)}).get_json()["added"] == 0           # idempotent
    assert c.post("/enqueue", json={"items": []}).status_code == 400
    t.tick()
    lines = [json.loads(x) for x in c.get("/export?suite=s").data.decode().splitlines()]
    assert len(lines) == 2 and lines[0]["wolfram"]["status"] == "ok" and lines[0]["mentat"]["ok"] is True
    assert c.get("/status").get_json()["suites"] == {"s": 2}


def test_the_wolfram_json_is_parsed_into_pods(monkeypatch):
    body = {"queryresult": {"success": True, "pods": [
        {"id": "Input", "title": "Input interpretation", "subpods": [{"plaintext": "free fall"}]},
        {"id": "Result", "title": "Result", "subpods": [{"plaintext": "1.43 seconds"}, {"plaintext": ""}]}]}}
    class Resp:
        def read(self): return json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(T.urllib.request, "urlopen", lambda url, timeout=0: Resp())
    out = T.ask_wolfram("K", "free fall 10 m")
    assert out["status"] == "ok" and out["primary_text"] == "1.43 seconds" and out["pods"][0]["title"] == "Input interpretation"
