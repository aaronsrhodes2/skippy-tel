"""Skippy Chat, slice 1: the Captain's identity is the only key, the model is chosen on the server, and nothing reaches the
bridge for anyone else. Plan: /mnt/project-files/skippy-chat/plan.md (section 0 is non-negotiable)."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import skippy_chat  # noqa: E402
import workshop as workshop_mod  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

import app as portal  # noqa: E402

SECOND_ADMIN = "second-admin@example.com"
CONV = "conv-12345678"


# ── the identity: one address, verified, nothing else ─────────────────────────────────────────────
def test_only_the_captains_verified_address_is_the_captain():
    assert skippy_chat.is_captain(ADMIN)
    assert skippy_chat.is_captain("  " + ADMIN.upper() + " ")          # case and spaces don't matter
    assert not skippy_chat.is_captain(ADMIN, verified=False)          # an unverified Google email is nobody


@pytest.mark.parametrize("who", [FRIEND, SECOND_ADMIN, "", None, 42, ADMIN + ".evil.com", "aaron.s.rhodes@gmail.co"])
def test_nobody_else_is_the_captain(who):
    assert not skippy_chat.is_captain(who)


# ── the models: the server's list, never the page's ───────────────────────────────────────────────
def test_only_the_model_that_really_runs_is_offered_as_runnable():
    runnable = {m["key"] for m in skippy_chat.options() if m["runs"]}
    assert runnable == {"sonnet"}
    assert skippy_chat.DEFAULT_MODEL == "sonnet"


def test_choosing_an_unavailable_or_unknown_model_is_refused_with_a_reason():
    assert skippy_chat.choose("sonnet") == ("sonnet", "")
    key, why = skippy_chat.choose("opus")
    assert key is None and "Opus" in why
    key, why = skippy_chat.choose("claude-sonnet-5-5")           # a raw model id from the page is not a key
    assert key is None and why == "Unknown model."
    assert skippy_chat.choose(None)[0] is None
    assert skippy_chat.choose(["sonnet"])[0] is None


# ── the routes ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def app_and_sent(monkeypatch):
    app = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=FakeSkippy())
    sent = []

    def fake_send(owner, conv, message, project="", images=None):
        sent.append({"owner": owner, "conv": conv, "message": message})
        return "task-1"

    monkeypatch.setattr(app.extensions["workshop"], "send", fake_send)
    return app, sent


def test_the_captain_gets_the_picker_only_with_the_header(app_and_sent):
    app, _ = app_and_sent
    client = app.test_client()
    login(client, ADMIN)
    assert client.get("/skippy-chat/models").status_code == 400          # no header: a cross-site page can't do this
    resp = client.get("/skippy-chat/models", headers={"X-Workshop": "1"})
    assert resp.status_code == 200
    assert resp.get_json()["default"] == "sonnet"
    assert resp.headers["Cache-Control"] == "no-store"


def test_the_captain_sends_to_the_bridge_with_the_server_side_model(app_and_sent):
    app, sent = app_and_sent
    client = app.test_client()
    login(client, ADMIN)
    resp = client.post("/skippy-chat/send", json={"conv": CONV, "message": "hi", "model": "sonnet"},
                       headers={"X-Workshop": "1"})
    assert resp.status_code == 200 and resp.get_json() == {"task": "task-1", "model": "sonnet"}
    assert sent == [{"owner": ADMIN, "conv": CONV, "message": "hi"}]


def test_an_unavailable_model_is_refused_before_the_bridge(app_and_sent):
    app, sent = app_and_sent
    client = app.test_client()
    login(client, ADMIN)
    for model in ("opus", "skippycore", "claude-sonnet-5-5", "nope"):
        resp = client.post("/skippy-chat/send", json={"conv": CONV, "message": "hi", "model": model},
                           headers={"X-Workshop": "1"})
        assert resp.status_code == 400, model
    assert sent == []


@pytest.mark.parametrize("who", [None, FRIEND, "stranger@example.com"])
def test_nobody_but_the_captain_reaches_the_routes(app_and_sent, who):
    app, sent = app_and_sent
    client = app.test_client()
    if who:
        login(client, who)
    for method, url in (("get", "/skippy-chat/models"), ("post", "/skippy-chat/send")):
        resp = getattr(client, method)(url, json={"conv": CONV, "message": "deploy"}, headers={"X-Workshop": "1"})
        assert resp.status_code == 403, (who, url)
    assert sent == []


def test_a_second_admin_is_refused_even_though_the_admin_pages_open_for_them(monkeypatch):
    """ADMIN_EMAILS is a list that can grow. Skippy Chat is keyed to the Captain's address alone, so an admin added there
    still gets the admin pages and not this one."""
    settings = {**SETTINGS, "ALLOWED_EMAILS": frozenset({ADMIN, FRIEND, SECOND_ADMIN}),
                "ADMIN_EMAILS": frozenset({ADMIN, SECOND_ADMIN})}
    app = portal.create_app(settings, vm=FakeVm(), skippy=FakeSkippy())
    sent = []
    monkeypatch.setattr(app.extensions["workshop"], "send", lambda *a, **k: sent.append(a) or "t")
    client = app.test_client()
    login(client, SECOND_ADMIN)
    assert client.get("/skippy-chat/models", headers={"X-Workshop": "1"}).status_code == 403
    assert client.post("/skippy-chat/send", json={"conv": CONV, "message": "hi"}, headers={"X-Workshop": "1"}).status_code == 403
    assert sent == []
