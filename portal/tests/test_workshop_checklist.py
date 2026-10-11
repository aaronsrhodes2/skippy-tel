"""The portal keeps a task-list event whole (a checklist is long) and every other tool line short."""
import pytest

pytest.importorskip("flask")

import workshop as workshop_mod  # noqa: E402


def test_a_task_list_event_keeps_its_lines_and_other_tools_stay_short():
    todo = "\n".join(f"\u25cb item {i}" for i in range(20))
    kept = workshop_mod._clean({"t": "tool", "name": "TodoWrite", "detail": todo})
    assert kept["detail"] == todo and kept["name"] == "TodoWrite"
    long_detail = "x" * 900
    assert workshop_mod._clean({"t": "tool", "name": "TodoWrite", "detail": long_detail})["detail"] == "x" * 900
    assert len(workshop_mod._clean({"t": "tool", "name": "TodoWrite", "detail": "y" * 5000})["detail"]) == 1200
    assert len(workshop_mod._clean({"t": "tool", "name": "Bash", "detail": long_detail})["detail"]) == 200
