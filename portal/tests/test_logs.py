"""The log intake (portal/logs.py, status_web's /home/logs and /status/logs): one bounded record shape, secrets removed at the door,
only the machines the door knows, newest first with filters, and a batch of junk never breaks anything."""
import json
import time

import pytest

pytest.importorskip("flask")

import logs as logs_mod  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

import app as portal  # noqa: E402


def store():
    printed = []
    return logs_mod.LogStore(emit=printed.append), printed


def test_a_record_is_cleaned_bounded_and_never_carries_a_secret():
    s, printed = store()
    n = s.add("skippy-core", [
        {"source": "docker/skippycore-llm-1", "level": "error", "kind": "crash", "msg": "died with Authorization: Bearer abcdefghijklmnop1234 and ANTHROPIC key sk-ant-api03-AAAAAAAAAAAAAAAAAAAA",
         "fields": {"exit_code": 137, "token": "sup3rs3cretvalue", "deep": {"a": 1}}},
        {"msg": "x" * 5000, "ts": "not a time", "kind": "weird", "level": "loud", "trace": "chat 1f3a/../x"},
        "junk", {"msg": ""}, {"nomsg": 1}, None])
    assert n == 2
    first, second = s.query(host="skippy-core")[1], s.query(host="skippy-core")[0]
    assert "abcdefghijklmnop1234" not in first["msg"] and "sk-ant-api03" not in first["msg"] and "<redacted>" in first["msg"]
    assert first["fields"] == {"exit_code": 137, "token": "<redacted>"}                  # a nested value is dropped, a secret value is not kept
    assert len(second["msg"]) == logs_mod.MAX_MSG and second["kind"] == "info" and second["level"] == "info"
    assert second["trace"] == "chat1f3a..x" or " " not in second["trace"] and "/" not in second["trace"]
    assert len(printed) == 2 and all("sk-ant" not in json.dumps(p) for p in printed)     # what Cloud Logging gets is already clean


def test_only_known_hosts_and_a_bounded_batch():
    s, _ = store()
    with pytest.raises(ValueError):
        s.add("evil-box", [{"msg": "hi"}])
    assert s.add("skippy-pc", [{"msg": f"m{i}"} for i in range(logs_mod.MAX_BATCH + 50)]) == logs_mod.MAX_BATCH
    assert s.add("skippy-pc", "not a list") == 0


def test_newest_first_with_filters_and_a_ring_that_forgets_the_oldest():
    s, _ = store()
    s.add("skippy-pc", [{"source": "docker/a", "level": "info", "msg": "alpha"}, {"source": "docker/b", "level": "error", "kind": "crash", "msg": "Beta boom", "trace": "t1"}])
    s.add("skippy-core", [{"source": "journal/deployer", "level": "warn", "msg": "gamma"}])
    assert [r["msg"] for r in s.query()] == ["gamma", "Beta boom", "alpha"]
    assert [r["msg"] for r in s.query(host="skippy-pc", level="error")] == ["Beta boom"]
    assert [r["msg"] for r in s.query(source="docker/")] == ["Beta boom", "alpha"]           # a prefix: every container
    assert [r["msg"] for r in s.query(q="BOOM")] == ["Beta boom"] and [r["msg"] for r in s.query(trace="t1")] == ["Beta boom"]
    assert [r["msg"] for r in s.query(kind="crash")] == ["Beta boom"] and len(s.query(limit=1)) == 1
    small = logs_mod.LogStore(emit=lambda r: None)
    small._ring = type(small._ring)(maxlen=3)
    small.add("portal", [{"msg": str(i)} for i in range(5)])
    assert [r["msg"] for r in small.query()] == ["4", "3", "2"]


def test_activity_counts_what_each_source_said_lately():
    now = [time.time()]
    s = logs_mod.LogStore(emit=lambda r: None, clock=lambda: now[0])
    s.add("skippy-pc", [{"source": "docker/a", "level": "error", "msg": "e1"}, {"source": "docker/a", "level": "info", "msg": "i1"}])
    a = s.activity()["skippy-pc|docker/a"]
    assert a["n"] == 2 and a["errors"] == 1
    now[0] += 3600
    assert s.activity() == {}                                                                  # an hour later, nothing is "lately"


# ── the routes ─────────────────────────────────────────────────────────────────
@pytest.fixture
def world():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    return app, app.test_client()


def test_the_ingest_door_wants_the_home_token_and_says_who_is_calling(world):
    app, client = world
    assert client.post("/home/logs", json={"host": "skippy-pc", "records": []}).status_code == 401
    h = {"Authorization": f"Bearer {app.extensions['home'].pair()}"}
    assert client.post("/home/logs", data="nope", headers=h).status_code == 400
    assert client.post("/home/logs", json={"host": "evil", "records": [{"msg": "x"}]}, headers=h).status_code == 400
    assert client.post("/home/logs", data="x" * 700_000, headers={**h, "Content-Type": "application/json"}).status_code == 413
    ok = client.post("/home/logs", json={"host": "skippy-core", "records": [{"source": "journal/deployer", "level": "warn", "msg": "slow"}]}, headers=h)
    assert ok.status_code == 200 and ok.get_json() == {"ok": True, "kept": 1}
    login(client, ADMIN)
    rows = client.get("/status/logs?host=skippy-core").get_json()["records"]
    assert [r["msg"] for r in rows] == ["slow"] and rows[0]["host"] == "skippy-core"


def test_the_log_view_is_the_captains_only(world):
    app, client = world
    assert client.get("/status/logs").status_code in (302, 401, 403)
    login(client, FRIEND)
    assert client.get("/status/logs").status_code in (302, 401, 403)
