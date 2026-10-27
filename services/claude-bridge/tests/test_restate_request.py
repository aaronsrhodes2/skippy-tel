"""restate_request.py: after a compaction the Captain's request goes back in front of the agent, word for word."""
import json
import subprocess
import sys
from pathlib import Path

import restate_request as rr
import workshop_engine

HOOK = Path(rr.__file__).resolve()


def _line(**entry):
    return json.dumps(entry) + "\n"


def _transcript(tmp_path, *lines):
    p = tmp_path / "session.jsonl"
    p.write_text("".join(lines), encoding="utf-8")
    return str(p)


REQUEST = "Turn 1: build the generator. Commit when the tests pass."
LINES = (
    _line(type="user", message={"role": "user", "content": REQUEST}),
    _line(type="assistant", message={"role": "assistant", "content": [{"type": "text", "text": "On it."}]}),
    _line(type="user", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}),
    _line(type="system", subtype="compact_boundary", compactMetadata={"trigger": "auto"}),
    _line(type="user", isCompactSummary=True, isVisibleInTranscriptOnly=True,
          message={"role": "user", "content": "This session is being continued from a previous conversation..."}),
    _line(type="user", isMeta=True, message={"role": "user", "content": "<system-reminder>meta</system-reminder>"}),
)


def test_after_a_compaction_the_request_comes_back_word_for_word(tmp_path):
    out = rr.restated({"source": "compact", "transcript_path": _transcript(tmp_path, *LINES)})
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert ctx.endswith(REQUEST) and "compacted" in ctx and "still the job" in ctx


def test_the_newest_request_wins_in_a_conversation_of_several(tmp_path):
    later = _line(type="user", message={"role": "user", "content": [{"type": "text", "text": "Now add the tests."}]})
    out = rr.restated({"source": "compact", "transcript_path": _transcript(tmp_path, *LINES, later)})
    assert out["hookSpecificOutput"]["additionalContext"].endswith("Now add the tests.")


def test_tool_results_summaries_meta_and_commands_are_never_taken_for_the_request():
    lines = [l for l in LINES[1:]] + [_line(type="user", message={"role": "user", "content": "<command-name>/model</command-name>"})]
    assert rr.last_request(lines) is None


def test_nothing_is_said_outside_a_compaction_or_without_a_transcript(tmp_path):
    path = _transcript(tmp_path, *LINES)
    for event in ({"source": "startup", "transcript_path": path}, {"source": "resume", "transcript_path": path},
                  {"source": "compact"}, {"source": "compact", "transcript_path": str(tmp_path / "missing.jsonl")}):
        assert rr.restated(event) is None


def test_a_very_long_request_is_cut_with_a_note(tmp_path):
    long = "x" * (rr.MAX_CHARS + 500)
    out = rr.restated({"source": "compact", "transcript_path": _transcript(tmp_path, _line(type="user", message={"content": long}))})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert len(ctx) < rr.MAX_CHARS + 400 and "longer than this restatement keeps" in ctx


def test_as_a_hook_it_prints_json_or_nothing_and_never_fails(tmp_path):
    run = lambda event: subprocess.run([sys.executable, str(HOOK)], input=event, capture_output=True, text=True, timeout=30)
    good = run(json.dumps({"source": "compact", "transcript_path": _transcript(tmp_path, *LINES)}))
    assert good.returncode == 0 and json.loads(good.stdout)["hookSpecificOutput"]["additionalContext"].endswith(REQUEST)
    for bad in ("not json", "[]", json.dumps({"source": "startup"})):
        r = run(bad)
        assert r.returncode == 0 and r.stdout == ""


def test_every_run_gets_it_through_the_lane_settings(tmp_path):
    shop = workshop_engine.Workshop.__new__(workshop_engine.Workshop)
    shop.state_dir = str(tmp_path)
    settings = json.loads(Path(shop.lane_settings()).read_text(encoding="utf-8"))
    starts = settings["hooks"]["SessionStart"]
    assert [s["matcher"] for s in starts] == ["compact"]
    assert "restate_request.py" in starts[0]["hooks"][0]["command"]
    assert settings["hooks"]["PreToolUse"]                                        # the lane guard and the eyes are still there
