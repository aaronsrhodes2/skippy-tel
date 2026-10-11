"""Skippy's task list (Claude Code's TodoWrite) reaches the chat as one tool event whose detail is the checklist, a line per item."""
import workshop_engine as engine
import conversation_log


def todos(*items):
    return {"todos": [{"content": c, "status": s, "activeForm": c} for c, s in items]}


def test_the_task_list_becomes_one_line_per_item_with_a_mark_for_its_state():
    detail = engine.tool_detail("TodoWrite", todos(("read the code", "completed"), ("write the test", "in_progress"), ("ship it", "pending")))
    assert detail == "\u2713 read the code\n\u25b6 write the test\n\u25cb ship it"


def test_the_list_is_bounded_and_secrets_in_it_are_redacted():
    many = todos(*[(f"item {i} " + "x" * 200, "pending") for i in range(40)])
    detail = engine.tool_detail("TodoWrite", many)
    assert len(detail) <= engine.MAX_TODO_DETAIL and detail.count("\n") <= 19 and all(len(line) <= 82 for line in detail.split("\n"))
    secret = engine.tool_detail("TodoWrite", todos(("use ghp_" + "a" * 36 + " to push", "pending")))
    assert "ghp_" + "a" * 36 not in secret
    assert engine.tool_detail("TodoWrite", {"todos": "nope"}) == "" and engine.tool_detail("TodoWrite", None) == ""
    assert engine.tool_detail("TodoWrite", {"todos": [5, {"content": ""}, {"nocontent": 1}]}) == ""


def test_other_tools_keep_their_short_lines():
    assert len(engine.tool_detail("Bash", {"command": "echo " + "y" * 500})) <= 160
    assert engine.tool_detail("Read", {"file_path": "D:/x/y.py"}) == "D:/x/y.py"


def test_a_checklist_survives_being_saved_and_reopened_whole():
    detail = "✓ a\n▶ b\n" + "○ long " * 100
    [kept] = conversation_log.clean_events([{"t": "tool", "name": "TodoWrite", "detail": detail}])
    assert kept["detail"].startswith("✓ a\n▶ b") and 200 < len(kept["detail"]) <= 1200         # reopened whole, not cut at a tool line's 200
    [bash] = conversation_log.clean_events([{"t": "tool", "name": "Bash", "detail": "z" * 500}])
    assert len(bash["detail"]) == 200
