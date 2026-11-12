"""finish_gate.py: the CLI engine's Stop hook blocks a turn with loose ends, counting, and has no opinion without its state file."""
import json
import subprocess
import sys
from pathlib import Path

import run_state as rs

GATE = str(Path(__file__).resolve().parent.parent / "finish_gate.py")


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True)


def _hook(env_state):
    import os
    env = {**os.environ, "SKIPPY_FINISH_STATE": env_state}
    return subprocess.run([sys.executable, GATE], input="{}", capture_output=True, text=True, env=env)


def test_blocks_uncommitted_work_up_to_the_limit_then_lets_go(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "first")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"clone": str(repo), "baseline": [], "head": rs.git_text(str(repo), "rev-parse", "HEAD"), "sent_back": 0}))
    assert _hook(str(state)).stdout == ""                                       # clean: no opinion
    (repo / "new.js").write_text("mine")
    outs = [_hook(str(state)).stdout for _ in range(rs.GATE_LIMIT + 1)]
    assert all(json.loads(o) == {"decision": "block", "reason": "These paths changed during this run and are not committed: new.js."}
               for o in outs[:-1])
    assert outs[-1] == ""                                                       # told enough times: it may go


def test_no_state_file_means_no_opinion(tmp_path):
    run = _hook(str(tmp_path / "missing.json"))
    assert run.returncode == 0 and run.stdout == ""
