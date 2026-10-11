"""Continue with Skippy (Skippy Memory): the Captain's chat lists every saved AI conversation and carries one on. The list
and the text come from skippy-pc as jobs; only an admin may ask, and only an admin's `context` reaches Skippy's prompt."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import chat  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402

import app as portal  # noqa: E402

CTX = {"source": "grok", "title": "Range of a Model Y in the cold", "updated": "2026-09-28T15:09:40",
       "text": "**Captain**\n\nHow much range do I lose?\n\n**Grok**\n\nRoughly 20 to 30 percent."}


@pytest.fixture
def env():
    skippy = FakeSkippy()
    app = portal.create_app({**SETTINGS, "CHAT_ENABLED": True}, vm=FakeVm(), skippy=skippy)
    app.extensions["home"].accept({"capabilities": {}})
    return app, app.test_client(), skippy


def test_the_continued_conversation_goes_into_the_prompt_and_malformed_ones_add_nothing():
    tail = chat.continued(CTX)
    assert "continuing an earlier conversation he had with Grok (2026-09-28)" in tail and "20 to 30 percent" in tail
    long = chat.continued({**CTX, "text": "x" * 100 + "END" + "y" * chat.CONTEXT_MAX_CHARS})
    assert "END" not in long and len(long) < chat.CONTEXT_MAX_CHARS + 600
    for bad in (None, "text", {"text": ""}, {"text": 5}, []):
        assert chat.continued(bad) == ""


def test_only_the_captains_context_reaches_skippy(env):
    app, client, skippy = env
    login(client, ADMIN)
    assert client.post("/chat/send", json={"message": "carry on", "context": CTX}).status_code == 200
    assert "<earlier_conversation>" in skippy.seen[0]
    client.get("/logout")
    login(client, FRIEND)
    app.config["ADMIN_EMAILS"] = frozenset({ADMIN})
    client.post("/chat/send", json={"message": "carry on", "context": CTX})
    assert "<earlier_conversation>" not in skippy.seen[0]


def test_the_list_and_a_conversation_are_jobs_for_skippy_pc_and_only_the_asker_reads_them(env):
    app, client, _ = env
    home = app.extensions["home"]
    login(client, ADMIN)
    job = client.post("/chat/memory", json={}).get_json()["job"]
    sent = home.jobs_reply()["jobs"]
    assert [j["kind"] for j in sent] == ["memory_list"]
    assert client.get(f"/chat/memory/result?job={job}").get_json()["state"] == "running"
    home.finish(job, {"result": {"conversations": [{"key": "grok:abc", "title": "t"}], "total": 1}})
    got = client.get(f"/chat/memory/result?job={job}").get_json()
    assert got["state"] == "done" and got["result"]["total"] == 1
    job2 = client.post("/chat/memory", json={"key": "grok:abc"}).get_json()["job"]
    assert any(j["kind"] == "memory_get" and j["key"] == "grok:abc" for j in home.jobs_reply()["jobs"])
    assert client.post("/chat/memory", json={"key": "../etc"}).status_code == 400
    assert client.get("/chat/memory/result?job=deadbeefdeadbeef").get_json()["state"] == "unknown"
    assert job2


def test_friends_get_neither_the_list_nor_the_panel(env):
    _, client, _ = env
    login(client, FRIEND)
    assert client.post("/chat/memory", json={}).status_code == 403
    assert client.get("/chat/memory/result?job=abcdef12").status_code == 403
    assert 'data-sheet="memory"' not in client.get("/").data.decode()


def test_the_captain_sees_the_panel(env):
    _, client, _ = env
    login(client, ADMIN)
    page = client.get("/").data.decode()
    assert 'data-sheet="memory"' in page and 'data-open="memory"' in page          # the sheet and the key that opens it


def test_silent_home_says_so(env, monkeypatch):
    app, client, _ = env
    monkeypatch.setattr(app.extensions["home"], "online", lambda: False)
    login(client, ADMIN)
    r = client.post("/chat/memory", json={})
    assert r.status_code == 503 and "isn't reporting" in r.get_json()["error"]
