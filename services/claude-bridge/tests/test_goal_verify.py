"""goal.py's two facts beyond the task list: code changed and never tested, and the one verification pass (docs/purple-rules.md, the goal slayer)."""
import json

import goal


def _write(tmp_path, records):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(p)


def _call(name, **args):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "x", "name": name, "input": args}]}}


def _said(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def test_code_changed_and_not_tested_goes_back_until_a_test_runs(tmp_path):
    recs = [_call("Edit", file_path="portal/app.py"), _said("Done.")]
    counts = {}
    why = goal.reason(_write(tmp_path, recs), counts)
    assert "no test ran after your last change" in why and counts == {"untested": 1}
    tested = recs + [_call("Bash", command="python -m pytest portal/tests -q")]
    assert goal.untested_since_edit(tested) is False


def test_an_edit_after_the_test_counts_as_untested_again():
    recs = [_call("Edit", file_path="a.py"), _call("Bash", command="python -m pytest -q"), _call("Write", file_path="b.js")]
    assert goal.untested_since_edit(recs) is True


def test_docs_only_changes_need_no_test():
    assert goal.untested_since_edit([_call("Edit", file_path="README.md")]) is False
    assert goal.edited_code([_call("Edit", file_path="README.md")]) is False


def test_the_verification_pass_runs_once_after_everything_else_passes(tmp_path):
    recs = [_call("Edit", file_path="a.py"), _call("Bash", command="python -m pytest -q"), _said("All done, tests pass.")]
    t = _write(tmp_path, recs)
    counts = {}
    first = goal.reason(t, counts, ask=lambda p: "MET", request="fix a")
    assert first == goal.DEEPEN + goal.reread("fix a") and counts["deepen"] == 1
    assert goal.reason(t, counts, ask=lambda p: "MET", request="fix a") == ""        # once per run


def test_no_verification_pass_for_a_run_that_changed_no_code(tmp_path):
    t = _write(tmp_path, [_call("Bash", command="ls"), _said("Here is the answer.")])
    assert goal.reason(t, {}, ask=lambda p: "MET", request="what is in the folder?") == ""


def test_the_pass_also_runs_when_core_gives_no_opinion(tmp_path):
    recs = [_call("Edit", file_path="a.py"), _call("Bash", command="python -m pytest -q")]
    assert goal.reason(_write(tmp_path, recs), {}, ask=None) == goal.DEEPEN
