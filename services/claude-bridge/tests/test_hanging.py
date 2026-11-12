"""hanging.py: a turn may not end while its background commands run, on a promise, or (once) on a question."""
import json

import hanging


def _write(tmp_path, records):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(p)


def _launch(i):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": f"Command running in background with ID: {i}. Output is being written to: f"}]}}


def _said(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def test_a_background_command_still_running_sends_the_turn_back(tmp_path):
    t = _write(tmp_path, [_launch("b1"), _launch("b2"),
                          {"type": "queue-operation", "content": "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>"}])
    counts = {}
    why = hanging.reason(t, counts)
    assert "b2" in why and "b1" not in why and counts == {"background": 1}


def test_a_finished_or_stopped_command_is_not_waited_on(tmp_path):
    t = _write(tmp_path, [_launch("b1"), _launch("b2"),
                          {"type": "user", "message": {"content": "<task-notification>\n<task-id>b1</task-id>"}},
                          {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "TaskStop", "input": {"task_id": "b2"}}]}}])
    assert hanging.still_running(hanging._records(t)) == []
    assert hanging.reason(t, {}) == ""


def test_the_background_wait_has_a_limit(tmp_path):
    t = _write(tmp_path, [_launch("b1")])
    counts = {}
    outs = [hanging.reason(t, counts) for _ in range(hanging.LIMITS["background"] + 1)]
    assert all(outs[:-1]) and outs[-1] == ""


def test_a_promise_goes_back_and_a_done_passes(tmp_path):
    t = _write(tmp_path, [_said("Tests pass. I'll report once the full suite finishes.")])
    assert "no later" in hanging.reason(t, {}, ask=lambda p: "PROMISE", request="fix it")
    assert hanging.reason(t, {}, ask=lambda p: "DONE", request="fix it") == ""


def test_a_question_goes_back_once_then_it_is_his(tmp_path):
    t = _write(tmp_path, [_said("Want me to push it?")])
    counts = {}
    assert "decisions made" in hanging.reason(t, counts, ask=lambda p: "ASK", request="fix it")
    assert hanging.reason(t, counts, ask=lambda p: "ASK", request="fix it") == ""


def test_the_judge_sees_the_request_and_the_last_words_and_no_core_is_no_opinion(tmp_path):
    seen = []
    t = _write(tmp_path, [_said("first"), _said("the last words")])
    hanging.reason(t, {}, ask=lambda p: seen.append(p) or "DONE", request="the request")
    assert "the request" in seen[0] and "the last words" in seen[0] and "first" not in seen[0]

    def down(p):
        raise OSError("core is down")
    assert hanging.reason(t, {}, ask=down, request="r") == ""
    assert hanging.judge("r", "", lambda p: "ASK") == ""                   # nothing said: nothing to judge


def test_check_without_a_transcript_or_words_has_no_opinion():
    assert hanging.check("", {}) == ""
