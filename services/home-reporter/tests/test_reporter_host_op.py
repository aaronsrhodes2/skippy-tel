"""The Admin page's skippy-pc buttons on the reporter's side: a host_op job goes to the bridge's /host/<op> with the home
token, its answer comes back as the job's result, and anything malformed never reaches the bridge."""
import pytest

import reporter


@pytest.fixture
def bridge(monkeypatch):
    calls = []
    answer = {"value": (200, {"ok": True, "summary": "video-gate restarted.", "output": "Up 2 seconds"})}

    def fake(method, path, body, token, timeout=40):
        calls.append((method, path, body, token))
        return answer["value"]
    monkeypatch.setattr(reporter, "BRIDGE", "http://bridge")
    monkeypatch.setattr(reporter, "_bridge", fake)
    return calls, answer


def test_a_host_op_goes_to_the_bridge_and_its_answer_is_the_result(bridge):
    calls, _ = bridge
    out = reporter.run_job({"kind": "host_op", "op": "restart-service", "arg": "video-gate"}, "tok")
    assert calls == [("POST", "/host/restart-service", {"arg": "video-gate"}, "tok")]
    assert out == {"result": {"ok": True, "summary": "video-gate restarted.", "output": "Up 2 seconds"}}


def test_a_refusal_is_a_result_that_says_not_ok(bridge):
    _, answer = bridge
    answer["value"] = (409, {"ok": False, "summary": "The private Skippy is in the middle of a run"})
    out = reporter.run_job({"kind": "host_op", "op": "restart-bridge"}, "tok")
    assert out["result"]["ok"] is False and "middle of a run" in out["result"]["summary"]


@pytest.mark.parametrize("job", [{"op": "format-c"}, {"op": "restart-service", "arg": "x; rm -rf /"},
                                 {"op": "restart-service", "arg": "A" * 60}])
def test_malformed_jobs_never_reach_the_bridge(bridge, job):
    calls, _ = bridge
    assert "error" in reporter.run_job({"kind": "host_op", **job}, "tok") and calls == []


def test_a_silent_bridge_is_explained(bridge):
    _, answer = bridge
    answer["value"] = (None, {})
    assert "isn't answering" in reporter.run_job({"kind": "host_op", "op": "funnel", "arg": "status"}, "tok")["error"]


# ── Skippy Memory jobs (the chat's "Continue with Skippy") ──────────────────────────────────────────────────────────

def test_memory_list_and_get_go_to_the_bridge(bridge):
    calls, answer = bridge
    answer["value"] = (200, {"conversations": [], "total": 0})
    assert reporter.run_job({"kind": "memory_list"}, "tok") == {"result": {"conversations": [], "total": 0}}
    answer["value"] = (200, {"key": "grok:abc", "text": "hi"})
    assert reporter.run_job({"kind": "memory_get", "key": "grok:abc"}, "tok")["result"]["text"] == "hi"
    assert [c[1] for c in calls] == ["/memory/list", "/memory/conversation?key=grok%3Aabc"]


def test_a_bad_memory_key_never_reaches_the_bridge(bridge):
    calls, _ = bridge
    assert "error" in reporter.run_job({"kind": "memory_get", "key": "../../x"}, "tok") and calls == []


def test_a_missing_conversation_is_said_plainly(bridge):
    _, answer = bridge
    answer["value"] = (404, {"error": "unknown_conversation"})
    assert "isn't in Skippy Memory" in reporter.run_job({"kind": "memory_get", "key": "grok:abc"}, "tok")["error"]
