"""holders.py: what a refused risky command would hit, from the process list and the purple runs going."""
import json

import holders as h


def test_active_runs_say_where_how_long_and_what_now(tmp_path):
    (tmp_path / "56be16bf.json").write_text(json.dumps({"task": "56be16bf", "project": "skippytel", "conv": "f2426c4e-xxxx",
        "created": 1000.0, "done": False, "events": [{"t": "tool", "name": "Bash", "detail": "python tools/loop_timer.py --watch"}]}))
    (tmp_path / "old.json").write_text(json.dumps({"task": "old", "created": 0, "done": True, "events": []}))
    (tmp_path / "engines.json").write_text(json.dumps({"core": []}))                # not a run
    (line,) = h.active_runs(str(tmp_path), now=1000.0 + 12 * 60)
    assert "56be16bf" in line and "skippytel" in line and "f2426c4e" in line and "12 min of its 120 min limit" in line
    assert "loop_timer.py --watch" in line


def test_kill_targets_are_read_from_each_shape():
    assert h._target("taskkill /IM python.exe /F") == ("image", "python.exe")
    assert h._target('taskkill //FI "WINDOWTITLE bi13jihh0*" //F') == ("title", "bi13jihh0")
    assert h._target("Stop-Process -Name node") == ("image", "node")
    assert h._target("pkill -f hub.py") == ("cmdline", "hub.py")
    assert h._target("killall node") == ("name", "node")


def test_a_title_kill_says_it_cannot_tell_and_a_reboot_lists_the_runs():
    said = h.report('taskkill //FI "WINDOWTITLE x*" //F', "kill", procs=[], runs=[])
    assert "unknown" in said and "SKIPPY_FORCE=1" in said
    said = h.report("shutdown /r", "reboot", runs=["purple run abc: running 3 min"])
    assert "Everything on that machine stops" in said and "purple run abc" in said
