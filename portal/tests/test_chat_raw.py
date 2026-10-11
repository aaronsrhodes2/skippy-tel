"""The chat's "Barney Style" switch (off = straight to Mentat, the page then sends raw: true): the question goes to Mentat as typed, Mentat's own answer comes back
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


def _setup(answer=ANSWER, skippy=None):
    skippy = skippy or FakeSkippy()
    if not hasattr(skippy, "_normal"):
        skippy.reply = _no_haiku
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy)
    client = app.test_client()
    asked = []
    stop = _home_with_mentat(app.test_client(), lambda q: asked.append(q) or {"result": answer})
    return client, asked, stop


GAP = {"value": None, "units": None, "source": "sigma_ground.materia.translator", "provenance_tag": "SPECULATIVE-PENDING",
       "notes": "Not a scenario Materia can model: no deformation physics.", "inputs": {"route": "qwen"}}


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
    login(client, ADMIN)
    for notice in ("yes", 1, "true"):
        r = client.post("/chat/send", json={"message": "status?", "raw": notice})
        assert r.status_code == 200 and "raw" not in r.get_json()               # still the ordinary Haiku path
    assert skippy.seen is not None


def test_the_chat_has_the_switch_and_keeps_raw_turns_out_of_the_history(env):
    client, _, _ = env
    login(client, ADMIN)
    page = client.get("/chat").data.decode()
    assert 'class="key barney" aria-pressed="false"' in page and "Barney Style" in page      # a glyph key, unlit by default
    assert "Mentat only" not in page
    assert "const raw = !barney.on" in page                                      # not lit means straight to Mentat
    assert "barney.on = false" not in page and 'aria-pressed="true"' not in page.split('class="key barney"')[1][:60]
    assert 'const BARNEY_PHRASE = "Give it to me Barney Style"' in page          # switching it on fills the box
    assert "Mentat's own answer, no Haiku:" in page                              # raw answers are kept for Barney Style to explain
    assert "if (!raw) { history.push(" not in page


# ── a physics gap is logged and answered "failed due to incompetence" ──────────────────────────────────────────

def _events(capsys, name):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{") and f'"{name}"' in line]


def test_a_gap_straight_to_mentat_says_failed_due_to_incompetence_and_logs_it_for_the_captain(capsys):
    client, asked, stop = _setup(GAP)
    login(client, ADMIN)
    d = client.post("/chat/send", json={"message": "how much does a steel bar bend under 2 tonnes?", "raw": True}).get_json()
    stop.set()
    assert d["reply"] == "failed due to incompetence" and d["raw"] is True and d["gap"]["logged"] is True
    assert d["gap"]["reason"] == GAP["notes"]                                  # the Captain sees why
    [gap] = _events(capsys, "mentat_gap")
    assert gap["how"] == "straight to Mentat" and gap["route"] == "qwen" and gap["tag"] == "SPECULATIVE-PENDING"
    assert gap["question"] == "how much does a steel bar bend under 2 tonnes?" and gap["reason"] == GAP["notes"]


def test_a_gap_from_a_friend_keeps_no_question_unless_they_allowed_saving(capsys):
    client, _, stop = _setup(GAP)
    login(client, FRIEND)
    d = client.post("/chat/send", json={"message": "my private question", "raw": True}).get_json()
    assert d["reply"] == "failed due to incompetence" and d["gap"] == {"logged": True, "reason": None}
    [gap] = _events(capsys, "mentat_gap")
    assert "question" not in gap and "reason" not in gap and gap["route"] == "qwen"   # it happened, and how: nothing said
    d = client.post("/chat/send", json={"message": "an allowed question", "raw": True, "save": True}).get_json()
    stop.set()
    [gap] = _events(capsys, "mentat_gap")
    assert gap["question"] == "an allowed question" and d["gap"]["reason"] == GAP["notes"]


def test_a_modelled_answer_or_an_outage_is_not_a_gap(capsys):
    client, _, stop = _setup(ANSWER)
    login(client, ADMIN)
    d = client.post("/chat/send", json={"message": "escape velocity of Earth?", "raw": True}).get_json()
    stop.set()
    assert "gap" not in d and _events(capsys, "mentat_gap") == []
    import chat_tools
    assert chat_tools.mentat_gap({"value": 0.0}) is None and chat_tools.mentat_gap("not json") is None
    assert chat_tools.mentat_gap({"error": "Qwen (Ollama) is unreachable"}) is None     # an outage, not missing physics
    assert chat_tools.mentat_gap({"value": None, "notes": "x"})["reason"] == "x"


def test_a_gap_through_haiku_is_logged_too_and_haiku_is_told_to_say_it(capsys):
    skippy = FakeSkippy()
    skippy._normal = True
    client, _, stop = _setup(GAP, skippy)
    login(client, ADMIN)
    skippy.reply = lambda system, messages, tools=None, run_tool=None, **_: (
        run_tool("ask_mentat", {"question": "bend a steel bar"}) and "ok")
    client.post("/chat/send", json={"message": "bend a steel bar"})
    stop.set()
    [gap] = _events(capsys, "mentat_gap")
    assert gap["how"] == "through Haiku" and gap["question"] == "bend a steel bar"
    import chat
    assert "failed due to incompetence" in chat.system_prompt("Pat", is_admin=False)


# ── Barney Style: Haiku explains the whole conversation from what Mentat found ─────────────────────────────

def test_barney_style_reads_the_whole_chat_with_no_tools_and_a_grounding_instruction():
    skippy = FakeSkippy()
    skippy._normal = True                                                      # this one is allowed to call the model
    client, asked, stop = _setup(ANSWER, skippy)
    login(client, FRIEND)
    history = [{"role": "user", "content": "5 cm copper ball from 10 km?"},
               {"role": "assistant", "content": "Mentat's own answer, no Haiku:\n" + json.dumps(ANSWER)}]
    d = client.post("/chat/send", json={"message": "Give it to me Barney Style", "history": history}).get_json()
    stop.set()
    assert d["reply"] == "Obviously."                                          # Haiku answered
    system, messages = skippy.seen
    assert "## Barney Style" in system and "never add physics of your own" in system
    assert skippy.tools is None and asked == []                                # nothing new is computed or looked up
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "159.6" in messages[1]["content"] and messages[2]["content"] == "Give it to me Barney Style"


def test_an_ordinary_message_gets_no_barney_instruction_and_keeps_its_tools(env):
    client, _, skippy = env
    login(client, ADMIN)
    client.post("/chat/send", json={"message": "is the server up?"})
    system, _ = skippy.seen
    assert "## Barney Style" not in system and skippy.tools is not None
