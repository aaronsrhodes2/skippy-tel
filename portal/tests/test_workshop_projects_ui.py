"""The card's project picker and Conversations list, run in node against the fake browser of test_workshop_ui.py (the
page's REAL inline script): a new chat asks which project, a conversation keeps to its project, the list comes from
skippy-pc and opening one shows its history and carries on in it (following a turn that is still running)."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402
from test_workshop_ui import S, drive, needs_node  # noqa: E402

PROJECTS = [{"id": "skippytel", "name": "SkippyTel", "repo": "aaronsrhodes2/skippy-tel", "branch": "main"},
            {"id": "sigma-ground", "name": "sigma-ground", "repo": "aaronsrhodes2/sigma-ground", "branch": "master"}]
NOW = 1000                                                    # the fake clock's Date.now() is 1,000,000 ms


@pytest.fixture
def page():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    app.extensions["home"].accept({"capabilities": {"workshop": {"ready": True, "model": "claude-sonnet-5-5",
                                                                 "projects": PROJECTS}}})
    client = app.test_client()
    login(client, ADMIN)
    return client.get("/me").data.decode()


def sends(out):
    return [c for c in out["calls"] if c["route"] == "send"]


@needs_node
def test_a_new_conversation_takes_the_picked_project_and_then_keeps_it(page):
    out = drive(page, [
        {"snap": "fresh"},
        {"pick": "sigma-ground"}, {"snap": "picked"},
        {"type": "fix the build"}, {"submit": True}, {"snap": "sent"},
        {"pick": "skippytel"}, {"snap": "locked"},
    ])
    s = out["snaps"]
    assert s["fresh"]["project"] == "skippytel" and not s["fresh"]["pickerLocked"]          # the default
    assert s["picked"]["project"] == "sigma-ground" and s["picked"]["storedProject"] == "sigma-ground"
    assert [c["project"] for c in sends(out)] == ["sigma-ground"]
    assert s["sent"]["pickerLocked"] and s["locked"]["project"] == "sigma-ground"           # one conversation, one repo


@needs_node
def test_an_unknown_stored_project_falls_back_to_the_first_listed(page):
    out = drive(page, [{"snap": "s"}], storage={"ps-project": "zphone"})
    assert out["snaps"]["s"]["project"] == "skippytel"


@needs_node
def test_new_chat_asks_which_project(page):
    log = '[{"role": "user", "text": "old"}, {"role": "assistant", "parts": []}]'
    out = drive(page, [{"snap": "before"}, {"click": "ps-new"}, {"snap": "after"}, {"pick": "sigma-ground"}, {"snap": "picked"}],
                storage={"ps-log": log, "ps-project": "skippytel", "ps-conv": "conv-oldoldold"})
    s = out["snaps"]
    assert s["before"]["pickerLocked"] and not s["before"]["pickHint"]
    assert not s["after"]["pickerLocked"] and s["after"]["pickHint"] and s["after"]["log"] == []
    assert s["after"]["conv"] != "conv-oldoldold"
    assert s["picked"]["project"] == "sigma-ground"


@needs_node
def test_the_discord_switch_saves_and_a_failed_save_puts_it_back(page):
    out = drive(page, [{"toggle": False}, {"snap": "off"}])
    assert [c.get("on") for c in out["calls"] if c["route"] == "notify"] == [False] and out["snaps"]["off"]["notify"] is False
    out = drive(page, [{"toggle": False}, {"snap": "kept"}], notify_mode="fail")
    assert out["snaps"]["kept"]["notify"] is True                         # the server didn't take it: still on


HISTORY = {
    "list": [{"conv": "conv-sigma0001", "project": "sigma-ground", "title": "make the solver faster", "turns": 2,
              "last": NOW - 7200, "started": NOW - 9000, "state": "done"},
             {"conv": "conv-skippy001", "project": "skippytel", "title": "orange games tile", "turns": 1,
              "last": NOW - 30, "started": NOW - 30, "state": "running"}],
    "convs": {
        "conv-sigma0001": {"conv": "conv-sigma0001", "project": "sigma-ground", "title": "make the solver faster", "trimmed": 0,
                           "turns": [{"task": "aaaa0001", "message": "make the solver faster", "state": "done", "error": None,
                                      "events": [{"t": "text", "text": "Twice as fast now."}]},
                                     {"task": "aaaa0002", "message": "and test it", "state": "error", "error": "tests failed",
                                      "events": [{"t": "tool", "name": "Bash", "detail": "pytest -q"}]}]},
        "conv-skippy001": {"conv": "conv-skippy001", "project": "skippytel", "title": "orange games tile", "trimmed": 0,
                           "turns": [{"task": "bbbb0001", "message": "orange games tile", "state": "running", "error": None,
                                      "events": [{"t": "text", "text": "stale copy"}]}]},
    },
}


@needs_node
def test_the_conversations_list_comes_from_skippy_pc_and_opening_one_shows_its_history(page):
    out = drive(page, [
        {"click": "ps-convs-btn"}, {"snap": "asking"},
        {"adv": 1500}, {"snap": "listed"},
        {"item": 0}, {"adv": 1500}, {"snap": "opened"},
        {"type": "now make it faster still"}, {"submit": True},
    ], history=HISTORY)
    s = out["snaps"]
    assert s["asking"]["convsShown"] and s["asking"]["logHidden"] and s["asking"]["convsMsg"] == "Asking skippy-pc…"
    assert s["asking"]["convsBtn"] == "Back to chat"
    assert s["listed"]["items"] == ["make the solver faster | sigma-ground · 2 messages · 2 h ago",
                                    "orange games tile | SkippyTel · 1 message · just now · working now"]
    o = s["opened"]
    assert not o["convsShown"] and not o["logHidden"] and o["conv"] == "conv-sigma0001"
    assert o["project"] == "sigma-ground" and o["pickerLocked"]
    assert [b["text"] for b in o["log"]] == ["make the solver faster", "Twice as fast now.", "and test it",
                                             "Ran 1 command | › Bash: pytest -q | tests failed"]   # (the tool steps fold into one line)
    assert "failed" in o["log"][-1]["cls"]
    [send] = sends(out)                                        # carrying on: same conversation, same project
    assert send["conv"] == "conv-sigma0001" and send["project"] == "sigma-ground"


@needs_node
def test_opening_a_conversation_whose_last_turn_is_running_follows_it(page):
    out = drive(page, [{"click": "ps-convs-btn"}, {"adv": 1500}, {"item": 1}, {"adv": 1500}, {"adv": 3 * S}, {"snap": "s"}],
                history=HISTORY, tasks={"bbbb0001": {"state": "running", "events": [{"t": "text", "text": "live step"}]}})
    s = out["snaps"]["s"]
    assert s["project"] == "skippytel" and s["task"]["task"] == "bbbb0001"
    assert s["log"][-1]["text"] == "live step"                  # the live events, not the stale copy twice
    assert s["stopShown"]


@needs_node
def test_a_list_that_fails_says_why_and_back_returns_to_the_chat(page):
    out = drive(page, [{"click": "ps-convs-btn"}, {"adv": 2 * S}, {"snap": "failed"}, {"click": "ps-convs-btn"}, {"snap": "back"},
                       {"click": "ps-convs-btn"}],
                history={"fail": "The bridge on skippy-pc isn't answering."})
    s = out["snaps"]
    assert s["failed"]["convsShown"] and s["failed"]["convsMsg"] == "The bridge on skippy-pc isn't answering."
    assert s["failed"]["items"] == []
    assert s["back"]["convsBtn"] == "Conversations" and not s["back"]["convsShown"] and not s["back"]["logHidden"]


@needs_node
def test_a_browser_that_never_opened_this_chat_opens_the_latest_conversation_not_a_blank_one(page):
    out = drive(page, [{"adv": 3000}, {"snap": "opened"}], history=HISTORY, fresh=True)
    o = out["snaps"]["opened"]
    assert o["conv"] == "conv-skippy001" and o["project"] == "skippytel"        # the most recent by its last activity
    assert [b["text"] for b in o["log"]][0] == "orange games tile" and o["pickerLocked"]


@needs_node
def test_a_browser_that_already_has_a_chat_keeps_it_and_does_not_ask(page):
    out = drive(page, [{"adv": 3000}, {"snap": "kept"}], history=HISTORY)
    assert out["snaps"]["kept"]["conv"] == "test-conv" and not [c for c in out["calls"] if c["route"] == "history"]


@needs_node
def test_a_conversation_you_are_in_is_pinned_and_listed_as_active_until_you_unpin_it(page):
    out = drive(page, [{"type": "fix the pill"}, {"submit": True}, {"adv": 2000}, {"click": "ps-convs-btn"}, {"adv": 1500}, {"snap": "list"}],
                history=HISTORY)
    items = out["snaps"]["list"]["items"]
    assert items[0] == "Active" and items[1].startswith("fix the pill") and items[1].endswith("×")      # pinned at once, with its unpin
    assert "Earlier" in items and "make the solver faster | sigma-ground · 2 messages · 2 h ago" in items   # the rest of the history below


@needs_node
def test_messages_sent_while_skippy_works_queue_and_go_together_when_the_task_returns(page):
    done = {"t": "text", "text": "Done."}
    out = drive(page, [
        {"type": "first"}, {"click": "ps-send"},
        {"tasks": {"new1": {"state": "running", "events": [{"t": "text", "text": "on it"}]}}}, {"adv": 2000},
        {"type": "then this"}, {"click": "ps-send"}, {"type": "and that"}, {"click": "ps-send"}, {"snap": "queued"},
        {"tasks": {"new1": {"state": "done", "events": [{"t": "text", "text": "on it"}, done]}}}, {"adv": 2000}, {"adv": 2000}, {"snap": "after"},
    ])
    q = out["snaps"]["queued"]
    assert q["queueShown"] and q["queue"] == ["then this | ×", "and that | ×"] and q["text"] == ""         # held, shown, and the box is free
    assert "Queue a message" in q["placeholder"]
    s = sends(out)
    assert [x["message"] for x in s] == ["first", "Several things queued while you worked, in order:\n1. then this\n2. and that"]
    assert not out["snaps"]["after"]["queueShown"] and out["snaps"]["after"]["queue"] == []                       # one message took them all


@needs_node
def test_a_single_queued_message_goes_as_itself_and_a_stopped_run_does_not_send_the_queue(page):
    out = drive(page, [
        {"type": "first"}, {"click": "ps-send"},
        {"tasks": {"new1": {"state": "running", "events": [{"t": "text", "text": "on it"}]}}}, {"adv": 2000},
        {"type": "just one"}, {"click": "ps-send"},
        {"tasks": {"new1": {"state": "done", "events": [{"t": "text", "text": "on it"}, {"t": "text", "text": "ok"}]}}}, {"adv": 2000}, {"adv": 2000},
    ])
    assert [x["message"] for x in sends(out)] == ["first", "just one"]                                              # not wrapped when alone
    out = drive(page, [
        {"type": "first"}, {"click": "ps-send"},
        {"tasks": {"new1": {"state": "running", "events": [{"t": "text", "text": "on it"}]}}}, {"adv": 2000},
        {"type": "later"}, {"click": "ps-send"},
        {"click": "ps-stop"}, {"tasks": {"new1": {"state": "error", "error": "stopped", "events": []}}}, {"adv": 3000}, {"snap": "stopped"},
    ])
    assert [x["message"] for x in sends(out)] == ["first"] and out["snaps"]["stopped"]["queue"] == ["later | ×"]   # he stopped it: the queue waits for him


LOG = [{"role": "user", "text": "fix the pill"},
       {"role": "assistant", "parts": [{"t": "tool", "name": "Bash", "detail": "pytest -q"}, {"t": "text", "text": "Fixed the **pill**."},
                                       {"t": "text", "text": "Deployed."}]}]


@needs_node
def test_a_prompt_a_reply_and_the_whole_conversation_carry_their_markdown_for_the_portals_copy_script(page):
    import json
    out = drive(page, [{"snap": "s"}], storage={"ps-conv": "conv-12345678", "ps-log": json.dumps(LOG)})
    s = out["snaps"]["s"]
    assert s["copyTurns"] == [
        "fix the pill",                                                        # a sent prompt, as typed
        "Fixed the **pill**.\n\nDeployed.",                                    # a reply: its words (the tool steps are not words)
    ]
    assert s["copyAll"] == "# SkippyTel\n\n## You\n\nfix the pill\n\n## Skippy\n\nFixed the **pill**.\n\nDeployed.\n\n_Ran 1 command_\n"


QUESTION = 'Orange or green?\n\nSKIPPY_QUESTION: {"question":"Which?","options":["Orange","Green"]}'


@needs_node
def test_a_question_becomes_buttons_and_a_tap_answers_it(page):
    import json
    log = [{"role": "user", "text": "recolour the pill"}, {"role": "assistant", "parts": [{"t": "text", "text": QUESTION}]}]
    out = drive(page, [{"snap": "asked"}, {"opt": 1}, {"snap": "answered"}], storage={"ps-conv": "conv-12345678", "ps-log": json.dumps(log)})
    asked = out["snaps"]["asked"]
    assert asked["ask"] == {"labels": ["Orange", "Green"], "hidden": False}
    assert "SKIPPY_QUESTION" not in asked["log"][-1]["text"] and "Orange or green?" in asked["log"][-1]["text"]      # the line is not shown as words
    assert [x["message"] for x in sends(out)] == ["Green"]                                                          # the tap sent that answer
    assert out["snaps"]["answered"]["log"][-2]["text"] == "Green"


@needs_node
def test_an_old_question_or_a_broken_one_has_no_buttons(page):
    import json
    old = [{"role": "user", "text": "a"}, {"role": "assistant", "parts": [{"t": "text", "text": QUESTION}]},
           {"role": "user", "text": "Green"}, {"role": "assistant", "parts": [{"t": "text", "text": "ok, green."}]}]
    assert drive(page, [{"snap": "s"}], storage={"ps-conv": "conv-12345678", "ps-log": json.dumps(old)})["snaps"]["s"]["ask"] is None     # already answered
    broken = [{"role": "user", "text": "a"}, {"role": "assistant", "parts": [{"t": "text", "text": 'hm\nSKIPPY_QUESTION: {"question":"Q?","options":["one"]}'}]}]
    out = drive(page, [{"snap": "s"}], storage={"ps-conv": "conv-12345678", "ps-log": json.dumps(broken)})
    assert out["snaps"]["s"]["ask"] is None and "SKIPPY_QUESTION" not in out["snaps"]["s"]["log"][-1]["text"]


@needs_node
def test_the_task_list_is_a_live_checklist_not_a_step(page):
    import json
    todo = "✓ read the code\n▶ write the test\n○ ship it"
    log = [{"role": "user", "text": "do it"},
           {"role": "assistant", "parts": [{"t": "tool", "name": "TodoWrite", "detail": "○ read the code\n○ write the test\n○ ship it"},
                                           {"t": "tool", "name": "Bash", "detail": "pytest -q"},
                                           {"t": "tool", "name": "TodoWrite", "detail": todo}]}]
    out = drive(page, [{"snap": "s"}], storage={"ps-conv": "conv-12345678", "ps-log": json.dumps(log)})
    text = out["snaps"]["s"]["log"][-1]["text"]
    assert "Task list 1/3" in text and "write the test" in text and "ship it" in text          # the LATEST list, with its progress
    assert "Ran 1 command" in text and "TodoWrite" not in text                                    # the list is not counted as a command
