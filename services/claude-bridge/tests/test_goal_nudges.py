"""The goal is the request plus everything the Captain adds while a run works (the Captain, 2026-11-13): his mid-run messages reach the
model at its next tool result, and every stop that sends the run back re-reads the whole goal."""
import asyncio
import json

import goal
import sdk_engine as sdk
import workshop_engine as engine


def _write(tmp_path, records):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(p)


def _call(name, **args):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "x", "name": name, "input": args}]}}


def test_the_goal_is_the_request_and_every_addition_in_order():
    text = goal.statement("build the page", ["also make it blue", "  ", "and not on mobile"])
    assert text.startswith("build the page") and text.index("blue") < text.index("not on mobile")
    assert text.count("Added while you worked") == 2
    assert goal.statement("", []) == ""


def test_every_reason_restates_the_whole_goal(tmp_path):
    recs = [_call("Edit", file_path="a.py")]
    why = goal.reason(_write(tmp_path, recs), {}, request="build the page", vibes=["also make it blue"])
    assert "no test ran" in why and "<goal>" in why and "build the page" in why and "also make it blue" in why


def test_the_judge_sees_the_additions_too(tmp_path):
    seen = []
    ask = lambda prompt: seen.append(prompt) or "MET"            # noqa: E731
    goal.reason(_write(tmp_path, [_call("Bash", command="ls")]), {}, ask=ask, words="all done", request="build the page", vibes=["also make it blue"])
    assert seen and "also make it blue" in seen[0] and "build the page" in seen[0]


def test_a_message_joins_the_live_run_and_is_read_once():
    run = engine.Run("t1", "c1", "build the page")
    run.join("also make it blue")
    run.join("   ")
    assert run.vibes == ["also make it blue"]
    assert any("also make it blue" in e.get("text", "") for e in run.events)
    out = asyncio.run(sdk.join_hook(run)({"tool_name": "Bash"}, None, None))
    assert "also make it blue" in out["hookSpecificOutput"]["additionalContext"]
    assert asyncio.run(sdk.join_hook(run)({"tool_name": "Bash"}, None, None)) == {}
    assert run.vibes == ["also make it blue"]                    # the goal keeps it after the model has read it


def test_workshop_join_finds_the_live_run_of_that_conversation(tmp_path):
    shop = engine.Workshop(["claude"], repo=str(tmp_path / "clone"), primary=str(tmp_path), sessions=str(tmp_path / "s.json"),
                           state_dir=str(tmp_path / "state"))
    run = engine.Run("t2", "c2", "go", brain="core")
    shop.adopt(run)
    assert shop.join("c2", "and one more thing") is run
    assert shop.join("other", "x") is None
    assert shop.join("c2", "x", brain="claude") is None
    run.done = True
    assert shop.join("c2", "too late") is None


def test_a_message_that_lands_after_the_last_tool_result_blocks_the_stop(tmp_path):
    run = engine.Run("t3", "c3", "build the page")
    gate = sdk.StopGate(str(tmp_path), set(), run=run)
    run.join("also make it blue")
    out = asyncio.run(gate.hook({"transcript_path": ""}, None, None))
    assert out["decision"] == "block" and "also make it blue" in out["reason"] and "build the page" in out["reason"]
    assert run.drain_inbox() == []                               # read once: the next stop is not blocked for it again
