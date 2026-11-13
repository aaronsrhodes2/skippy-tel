"""goal.py: a turn may not end with its own task list unfinished, or with the request, by its own report, not met."""
import json

import goal


def _write(tmp_path, records):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(p)


def _create(use, subject, n):
    return [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": use, "name": "TaskCreate", "input": {"subject": subject}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": use, "content": f"Task #{n} created successfully: {subject}"}]}}]


def _update(n, status):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "u", "name": "TaskUpdate", "input": {"taskId": str(n), "status": status}}]}}


def _said(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def test_an_unfinished_task_sends_the_turn_back_and_a_closed_list_does_not(tmp_path):
    recs = _create("a", "fix the parser", 1) + _create("b", "run the suite", 2) + [_update(1, "completed"), _update(2, "in_progress")]
    counts = {}
    why = goal.reason(_write(tmp_path, recs), counts)
    assert "#2 run the suite" in why and "fix the parser" not in why and counts == {"tasks": 1}
    assert goal.reason(_write(tmp_path, recs + [_update(2, "deleted")]), {}) == ""


def test_the_task_check_has_a_limit(tmp_path):
    t = _write(tmp_path, _create("a", "x", 1))
    counts = {}
    outs = [goal.reason(t, counts) for _ in range(goal.LIMITS["tasks"] + 1)]
    assert all(outs[:-1]) and outs[-1] == ""


def test_an_open_goal_goes_back_with_the_missing_thing_and_met_passes(tmp_path):
    t = _write(tmp_path, [_said("Fixed the parser. The deploy is left for you.")])
    seen = []
    why = goal.reason(t, {}, ask=lambda p: seen.append(p) or "OPEN: the deploy", request="fix and deploy")
    assert "the deploy" in why and "fix and deploy" in seen[0] and "left for you" in seen[0]
    assert goal.reason(t, {}, ask=lambda p: "MET", request="fix and deploy") == ""


def test_the_goal_check_has_a_limit_and_no_core_is_no_opinion(tmp_path):
    t = _write(tmp_path, [_said("half done")])
    counts = {}
    outs = [goal.reason(t, counts, ask=lambda p: "OPEN: the rest", request="r") for _ in range(goal.LIMITS["goal"] + 1)]
    assert all(outs[:-1]) and outs[-1] == ""

    def down(p):
        raise OSError("core is down")
    assert goal.reason(t, {}, ask=down, request="r") == ""


def test_the_gate_is_off_unless_the_run_s_environment_says_so(tmp_path):
    t = _write(tmp_path, _create("a", "x", 1))
    assert goal.check(t, {}, env={}) == ""
    assert "#1 x" in goal.check(t, {}, env={"SKIPPY_GOAL_GATE": "1"})


def test_purple_runs_get_the_task_tools_and_the_goal_gate():
    import workshop_engine as we
    env = we.child_env({})
    assert env["CLAUDE_CODE_ENABLE_TODO_TOOLS"] == "1" and env["SKIPPY_GOAL_GATE"] == "1"
