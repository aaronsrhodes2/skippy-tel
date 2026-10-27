"""Mentat suggests inputs instead of filling them in (the Captain, 2026-10-23), and Skippy's chat relays that.

A question that leaves an input out gets a clarification from Mentat (value null, nothing computed). The ⚛ card shows the
suggestion, not a number; a plain "yes" as the next message makes the portal ask Mentat again with exactly the suggested
values (the follow-up Mentat made), and every Mentat call carries this turn's trace id into Mentat's research record.
"""
import json
import threading
import time

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

from test_app import ADMIN, FRIEND, REPORT, SETTINGS, FakeSkippy, FakeVm, _pair, login  # noqa: E402

import app as portal  # noqa: E402
import chat  # noqa: E402
import chat_tools  # noqa: E402

FOLLOW = {"question": "range of a projectile launched at 45 degrees", "given": {"speed_m_s": 30.0}}
SUGGESTION = {"value": None, "units": "", "source": "sigma_ground.materia.inputs", "provenance_tag": "SPECULATIVE-PENDING",
              "notes": "Your question doesn't give everything I need. I'd assume 30 m/s for the launch speed (a hard throw "
                       "by hand, about 67 mph). Use that?",
              "error": {"code": "needs_input"}, "inputs": {"route": "deterministic"},
              "clarification": {"needs": ["speed_m_s"],
                                "suggested": {"speed_m_s": {"value": 30.0, "units": "m/s", "why": "a hard throw by hand",
                                                            "label": "launch speed"}},
                                "sentence": "I'd assume 30 m/s for the launch speed. Use that?", "follow_up": FOLLOW}}
ANSWER = {"value": 91.77, "units": "m", "source": "sigma_ground.materia (projectile_motion)", "provenance_tag": "DERIVED",
          "inputs": {"chain": []}}


def _home(client, jobs):
    """A fake skippy-core Mentat (the chat asks it straight) that keeps each whole ask: a suggestion unless `given` came with it."""
    from test_app import AskMentat
    m = AskMentat(lambda q: {"result": ANSWER if isinstance(q, tuple) else SUGGESTION})
    m.asked = jobs
    client.application.extensions["mentat"] = m
    return threading.Event()


@pytest.fixture
def setup():
    skippy = FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy)
    jobs = []
    stop = _home(app.test_client(), jobs)
    yield app.test_client(), skippy, jobs
    stop.set()


def test_a_suggestion_is_relayed_as_a_suggestion_card_and_is_not_a_gap(setup, capsys):
    client, skippy, jobs = setup
    login(client, ADMIN)
    skippy.reply = lambda system, messages, tools=None, run_tool=None, **_: (
        run_tool("ask_mentat", {"question": FOLLOW["question"]}) and "Mentat would assume 30 m/s for the launch speed. Use that?")
    d = client.post("/chat/send", json={"message": FOLLOW["question"]}).get_json()
    card, = d["mentat"]
    assert card["value"] is None and card["suggestion"]["follow_up"] == FOLLOW
    assert card["suggestion"]["suggested"]["speed_m_s"]["value"] == 30.0
    assert "mentat_gap" not in capsys.readouterr().out                  # asking for an input is not "failed due to incompetence"
    assert jobs[0]["question"] == FOLLOW["question"] and "given" not in jobs[0]
    assert jobs[0]["trace"].startswith("chat-")                        # the turn's id travels to Mentat's research record


def test_a_plain_yes_sends_mentats_own_follow_up_and_the_brain_only_explains(setup):
    client, skippy, jobs = setup
    login(client, ADMIN)
    seen = []

    def reply(system, messages, tools=None, run_tool=None, **_):
        seen.append((tools, messages[-1]["content"]))
        return "Mentat computed 91.77 m."
    skippy.reply = reply
    d = client.post("/chat/send", json={"message": "yes", "mentat_follow_up": FOLLOW}).get_json()
    assert jobs[-1]["question"] == FOLLOW["question"] and jobs[-1]["given"] == {"speed_m_s": 30.0}
    (tools, handed), = seen
    assert tools is None and "91.77" in handed and "speed_m_s" in handed     # handed Mentat's answer, no tools to retype numbers
    assert d["mentat"][0]["value"] == 91.77 and d["reply"] == "Mentat computed 91.77 m."


def test_anything_but_a_plain_yes_leaves_the_suggestion_unused(setup):
    client, skippy, jobs = setup
    login(client, ADMIN)
    skippy.reply = lambda system, messages, tools=None, run_tool=None, **_: "ok"
    client.post("/chat/send", json={"message": "yes but make it 40 m/s", "mentat_follow_up": FOLLOW})
    assert jobs == []                                                  # the brain decides; nothing was sent for it


def test_a_guest_with_mentat_straight_gets_the_yes_too(setup, monkeypatch):
    client, skippy, jobs = setup
    login(client, FRIEND)
    d = client.post("/chat/send", json={"message": "sure, go ahead", "raw": True, "mentat_follow_up": FOLLOW}).get_json()
    assert jobs[-1]["given"] == {"speed_m_s": 30.0} and json.loads(d["reply"])["value"] == 91.77


def test_the_tool_passes_given_only_as_mentat_made_it():
    class Home:
        def __init__(self):
            self.calls = []

        def ask_mentat(self, question, given=None, trace=""):
            self.calls.append((question, given, trace))
            return ANSWER
    home = Home()
    tools = chat_tools.ChatTools(FakeVm(), is_admin=True, home_mentat=home, trace="chat-1")
    tools.run("ask_mentat", {"question": "q", "given": {"speed_m_s": 30}})
    assert home.calls == [("q", {"speed_m_s": 30}, "chat-1")]
    with pytest.raises(chat_tools.ToolError):
        tools.run("ask_mentat", {"question": "q", "given": {"speed_m_s": [30]}})
    schema = next(t for t in tools.schemas() if t["name"] == "ask_mentat")
    assert "given" in schema["input_schema"]["properties"] and "never fills" in schema["description"].lower()


@pytest.mark.parametrize("text, yes", [
    ("yes", True), ("Yes!", True), ("sure", True), ("ok go ahead", True), ("yes please, use those", True), ("yep", True),
    ("go ahead", True), ("no", False), ("yes but 40 m/s", False), ("what about 50 mph?", False), ("", False),
    ("yes 30", False),
])
def test_what_counts_as_a_yes(text, yes):
    assert chat.is_yes(text) is yes


def test_follow_ups_are_checked_before_they_reach_mentat():
    assert chat_tools.clean_follow_up(FOLLOW) == FOLLOW
    for bad in (None, {}, {"question": "q"}, {"question": "q", "given": {}}, {"question": "q", "given": {"a": True}},
                {"question": "q", "given": {"a": {"b": 1}}}, {"question": "", "given": {"a": 1}}):
        assert chat_tools.clean_follow_up(bad) is None


def test_the_prompt_says_mentat_suggests_and_never_invents():
    prompt = chat.system_prompt("Aaron", is_admin=True)
    assert "never fills in an input" in prompt and "follow_up" in prompt
    assert "marks them as its own inventions" not in prompt and "which values it invented" not in prompt
