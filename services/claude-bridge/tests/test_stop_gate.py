"""stop_gate.py: the SDK engine's StopGate blocks when a mid-run message arrives (once only) and when the goal isn't met (with the goal re-read)."""
import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import sdk_engine as sdk
from workshop_engine import Run


def git(repo, *args):
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True)


def _mock_run(message="build it", vibes=None, inbox_items=None):
    """A Run-like object: enough attributes for StopGate.hook."""
    run = Run("aaaaaaaa", "conv-12345678", message, brain="core")
    if inbox_items:
        for item in inbox_items:
            run.join(item)
    run._inbox = inbox_items if inbox_items else []
    run.vibes = vibes or []
    return run


def _clean_env():
    """Env without goal-gate or Core settings so the goal check stays off."""
    return {k: v for k, v in os.environ.items()
            if k not in ("SKIPPY_GOAL_GATE", "SKIPPY_CORE_URL", "SKIPPY_CORE_API_KEY")}


def test_mid_run_message_blocks_once_then_inbox_drained(tmp_path):
    """When the Captain writes while the run works, the stop gate blocks that turn and drains the inbox; the next stop finds nothing."""
    repo = tmp_path / "work"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "first")

    mid_msg = "Also make it blue"
    run = _mock_run("build it", inbox_items=[mid_msg])
    said = []
    gate = sdk.StopGate(str(repo), set(), limit=0, say=said.append, run=run)

    # First stop: inbox has a message → blocks, reason has told() + reread()
    result = asyncio.run(gate.hook({"hook_event_name": "Stop"}, None, {}))
    assert result["decision"] == "block"
    reason = result["reason"]
    assert mid_msg in reason
    assert "<goal>" in reason and "</goal>" in reason

    # Second stop: inbox drained → no more block from mid-run messages
    assert asyncio.run(gate.hook({"hook_event_name": "Stop"}, None, {})) == {}

    # Inbox really is empty now
    assert run.drain_inbox() == []

    assert len(said) == 1
    assert "message arrived while I worked" in said[0]


def test_goal_not_met_sends_back_with_goal_reread(tmp_path):
    """When the goal check finds the request isn't fully met, the gate blocks with the goal re-read in the reason."""
    repo = tmp_path / "work"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "first")

    run = _mock_run("Add a test for the stop gate")
    run._inbox = []
    said = []
    gate = sdk.StopGate(str(repo), set(), limit=0, say=said.append, run=run)

    # goal.check() appends reread() internally, so the mock must include it
    full_reason = "The test is missing\n\nThe goal, re-read in full (do not let the work drift from any part of it):\n<goal>\nAdd a test for the stop gate\n</goal>"
    with patch("sdk_engine.goal.check", return_value=full_reason):
        result = asyncio.run(gate.hook({"hook_event_name": "Stop"}, None, {}))
        assert result["decision"] == "block"
        reason = result["reason"]
        assert "The test is missing" in reason
        assert "<goal>" in reason and "</goal>" in reason
        assert "Add a test for the stop gate" in reason

    # Second call: goal check passes (returns "") → gate lets it go
    assert asyncio.run(gate.hook({"hook_event_name": "Stop"}, None, {})) == {}

    assert len(said) == 1
    assert "not fully met" in said[0] or "request isn't" in said[0] or "isn't finished" in said[0]
