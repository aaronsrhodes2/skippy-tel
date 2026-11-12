"""The toolchain behind each of purple's replies (static/skippy-chain.js, services/claude-bridge/toolchain.py): the portal keeps the
bridge's `chain` event (checked, never dropped, so the relay's count stays in step), the front page loads the strip's script before
the chat, and the chat draws it: a hairline of one segment per call along the reply's bottom, its key beside the copy key, the list."""
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("flask")

import workshop as workshop_mod  # noqa: E402
from test_workshop_ui import drive, needs_node, page  # noqa: E402,F401 - the card's real script in node, and its page fixture
from test_workshop_ui import env  # noqa: E402,F401

PORTAL = Path(__file__).resolve().parents[1]
CHAIN = {"t": "chain", "calls": [{"n": "Read", "d": "portal/app.py", "k": "read", "ms": 40, "ok": True},
                                 {"n": "Bash", "d": "git push origin main", "k": "git", "ms": 2100, "ok": False}],
         "flags": [{"rule": "deploy", "text": "says deployed or live; no deploy call and no look at the live service"}]}


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_strip_helpers_pass_their_node_tests():
    run = subprocess.run([shutil.which("node"), "--test", str(PORTAL / "tests" / "js" / "chain" / "chain.test.js")],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]


def test_the_portal_keeps_a_chain_event_checked():
    kept = workshop_mod._clean(CHAIN)
    assert kept == CHAIN
    odd = workshop_mod._clean({"t": "chain", "calls": [{"n": "X" * 99, "d": "y" * 999, "k": "evil", "ms": -5, "ok": "yes"}, 7, {"d": "no name"}],
                               "flags": [{"rule": "BAD RULE", "text": "x"}, {"rule": "push", "text": "z" * 999}], "more": True})
    assert odd == {"t": "chain", "calls": [{"n": "X" * 40, "d": "y" * 160, "k": "other"}], "flags": [{"rule": "push", "text": "z" * 200}]}
    assert workshop_mod._clean({"t": "chain"}) == {"t": "chain", "calls": [], "flags": []}   # always an event: the relay counts it


def test_the_front_page_loads_the_strip_before_the_chat():
    front = (PORTAL / "templates" / "front.html").read_text(encoding="utf-8")
    assert front.index("skippy-chain.js") < front.index('{% include "_chat.html" %}')


@needs_node
def test_a_finished_reply_draws_its_strip_last_its_key_beside_the_copy_key_and_its_list(page):  # noqa: F811
    out = drive(page, [{"adv": 3000}, {"snap": "end"}],
                tasks={"t1": {"state": "done", "events": [{"t": "tool", "name": "Read", "detail": "portal/app.py"},
                                                          {"t": "text", "text": "Deployed and live."}, CHAIN]}},
                follow="t1", chain=True)
    strip = out["snaps"]["end"]["chains"][-1]
    assert strip["cls"] == "chain flagged" and strip["stripLast"]
    assert strip["segs"] == ["seg k-read", "seg k-git bad", "warn"]
    assert strip["keyIndex"] == 1 and "2" in strip["key"]                      # right after the copy key, the call count on it
    assert strip["list"].startswith("1. Read") and "says deployed" in strip["list"]
    reply = out["snaps"]["end"]["log"][-1]["text"]
    assert "Deployed and live." in reply and "Ran 1 command" in reply            # the fold of steps is still there; the chain is not a step
    assert out["snaps"]["end"]["copyTurns"][-1] == "Deployed and live."        # nor part of the reply's markdown


@needs_node
def test_a_reply_without_a_chain_and_a_page_without_the_script_draw_no_strip(page):  # noqa: F811
    tasks = {"t1": {"state": "done", "events": [{"t": "text", "text": "Hi."}]}}
    assert drive(page, [{"adv": 3000}, {"snap": "end"}], tasks=tasks, follow="t1", chain=True)["snaps"]["end"]["chains"][-1] is None
    tasks = {"t1": {"state": "done", "events": [{"t": "text", "text": "Hi."}, CHAIN]}}
    assert drive(page, [{"adv": 3000}, {"snap": "end"}], tasks=tasks, follow="t1")["snaps"]["end"]["chains"][-1] is None
