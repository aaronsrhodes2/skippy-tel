"""The agent's lane (lane_guard.py): a Workshop run changes files only inside its conversation's folder."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import lane_guard as lg

LANE = "D:/Aaron/development/arkanoid"
DEPLOY = "D:/Aaron/development/SkippyTel"


@pytest.fixture
def lane():
    return lg.Lane(LANE, DEPLOY, temps=["C:/Users/aaron/AppData/Local/Temp"])


@pytest.mark.parametrize("path", ["D:/Aaron/development/arkanoid/game.py", r"D:\Aaron\development\arkanoid\src\a.js",
                                  "/d/Aaron/development/arkanoid/x", "C:/Users/aaron/AppData/Local/Temp/scratch.txt"])
def test_edits_inside_the_lane_or_temp_are_fine(lane, path):
    assert lane.check("Edit", {"file_path": path}) is None and lane.check("Write", {"file_path": path}) is None


@pytest.mark.parametrize("path", ["D:/Aaron/development/SkippyTel/portal/app.py", "D:/Aaron/development/arkanoid-other/x",
                                  "C:/Windows/system32/x", "D:/Aaron/development/arkanoid/../music-collection/a.py"])
def test_edits_outside_are_refused(lane, path):
    assert "outside" in lane.check("Write", {"file_path": path})


@pytest.mark.parametrize("cmd", [
    "git status && npm test", "cat D:/Aaron/development/SkippyTel/CLAUDE.md", "grep -rn foo /d/Aaron/development/zPhone",
    "cd src && python game.py", "ls ../", "echo hi > out.txt",
    "cd D:/Aaron/development/SkippyTel && git pull && docker compose up --build -d flask-api",
    "git -C D:/Aaron/development/SkippyTel pull", "python -m pytest -q > C:/Users/aaron/AppData/Local/Temp/t.txt",
    "gh repo create aaronsrhodes2/arkanoid --private --source . --push",
])
def test_reading_anywhere_working_in_the_lane_and_deploying_are_fine(lane, cmd):
    assert lane.check("Bash", {"command": cmd}) is None, cmd


@pytest.mark.parametrize("cmd", [
    "cd D:/Aaron/development/music-collection && git commit -am x", "cd ..", "cd /d/Aaron/development/zPhone",
    "rm -rf D:/Aaron/development/SkippyTel/portal", "echo x > D:/Aaron/development/SkippyTel/portal/app.py",
    "sed -i s/a/b/ /d/Aaron/development/SkippyTel/portal/app.py", "git -C D:/Aaron/development/music-collection commit -am x",
    "git -C D:/Aaron/development/SkippyTel commit -am x", "mkdir ../elsewhere", "Remove-Item D:/Aaron/development/zPhone -Recurse",
])
def test_leaving_the_lane_or_changing_files_outside_is_refused(lane, cmd):
    assert lane.check("Bash", {"command": cmd}), cmd


def test_the_hook_refuses_with_exit_2_and_says_why(tmp_path):
    hook = Path(lg.__file__)
    outside = "C:/outside-the-lane/x.py" if sys.platform == "win32" else "/srv/outside-the-lane/x.py"   # (not a temp folder: those are allowed)
    event = {"tool_name": "Write", "tool_input": {"file_path": outside}, "cwd": str(tmp_path)}
    run = subprocess.run([sys.executable, str(hook)], input=json.dumps(event), capture_output=True, text=True,
                         env={"CLAUDE_PROJECT_DIR": str(tmp_path), "PATH": ""})
    assert run.returncode == 2 and "lane rule" in run.stderr
    event["tool_input"]["file_path"] = str(tmp_path / "x.py")
    ok = subprocess.run([sys.executable, str(hook)], input=json.dumps(event), capture_output=True, text=True,
                        env={"CLAUDE_PROJECT_DIR": str(tmp_path), "PATH": ""})
    assert ok.returncode == 0


def test_every_run_gets_the_lane_guard_and_skippytel_its_deploy_checkout(shop):
    from bridge_testkit import calls, finish
    finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    (call,) = calls(shop)
    argv = call["argv"]
    settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))
    (rule,) = settings["hooks"]["PreToolUse"]
    assert rule["matcher"] == "Edit|Write|MultiEdit|NotebookEdit|Bash" and "lane_guard.py" in rule["hooks"][0]["command"]
    assert "SKIPPY_LANE_DEPLOY" in call["env"]
