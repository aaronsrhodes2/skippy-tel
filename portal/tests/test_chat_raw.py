"""The chat's "Mentat only (Haiku off)" switch: the question goes to Mentat as typed, Mentat's own answer comes back
unedited, and Haiku is never called (it would otherwise read the question first and rewrite the answer)."""
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, _home_with_mentat, env, login  # noqa: E402,F401

import app as portal  # noqa: E402

ANSWER = {"value": 159.6, "units": "m/s", "interpreter": "keywords + qwen", "source": "sigma-ground"}


def _no_haiku(*_a, **_k):
    pytest.fail("Haiku was called")                                            # raw means no model at all


def _setup():
    skippy = FakeSkippy()
    skippy.reply = _no_haiku
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy)
    client = app.test_client()
    asked = []
    stop = _home_with_mentat(app.test_client(), lambda q: asked.append(q) or {"result": ANSWER})
    return client, asked, stop


def test_raw_sends_the_question_as_typed_and_returns_mentats_own_answer():
    client, asked, stop = _setup()
    login(client, FRIEND)
    r = client.post("/chat/send", json={"message": "5 cm copper ball from 10 km?", "raw": True})
    stop.set()
    d = r.get_json()
    assert r.status_code == 200 and asked == ["5 cm copper ball from 10 km?"]
    assert json.loads(d["reply"]) == ANSWER                                    # nothing added, nothing reworded
    assert d["raw"] is True and d["tools"] == ["ask_mentat"] and d["mentat_where"] == "home" and d["media"] == []


def test_raw_drops_a_physics_verb_like_the_normal_path_does():
    client, asked, stop = _setup()
    login(client, ADMIN)
    client.post("/chat/send", json={"message": "/physics escape velocity of Earth?", "raw": True})
    stop.set()
    assert asked == ["escape velocity of Earth?"]


def test_raw_with_no_question_asks_for_one_and_never_calls_mentat():
    client, asked, stop = _setup()
    login(client, FRIEND)
    for message in ("/physics", "/physics   "):
        assert client.post("/chat/send", json={"message": message, "raw": True}).status_code == 400
    stop.set()
    assert asked == []


def test_raw_says_so_when_mentat_is_not_available():
    skippy = FakeSkippy()
    skippy.reply = _no_haiku
    client = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy).test_client()
    login(client, FRIEND)
    r = client.post("/chat/send", json={"message": "how fast?", "raw": True})
    assert r.status_code == 503 and "isn't available" in r.get_json()["error"]


def test_raw_needs_the_same_sign_in_and_only_a_literal_true_turns_it_on(env):
    client, _, skippy = env
    assert client.post("/chat/send", json={"message": "hi", "raw": True}).status_code in (302, 401, 403)
    login(client, FRIEND)
    for notice in ("yes", 1, "true"):
        r = client.post("/chat/send", json={"message": "status?", "raw": notice})
        assert r.status_code == 200 and "raw" not in r.get_json()               # still the ordinary Haiku path
    assert skippy.seen is not None


def test_the_chat_has_the_switch_and_keeps_raw_turns_out_of_the_history(env):
    client, _, _ = env
    login(client, ADMIN)
    page = client.get("/chat").data.decode()
    assert 'class="raw"' in page and "Mentat only (Haiku off)" in page
    assert "if (!raw) { history.push(" in page                                  # Haiku never sees a raw answer later
