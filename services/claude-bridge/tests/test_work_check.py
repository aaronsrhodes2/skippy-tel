"""work_check.py: a project's own checker, a look at what was made, and its budget hold a run back, each a limited number of times;
a project without .skippy-check.json, or a run that didn't touch the checked files, is never held."""
import json
import subprocess
import sys
import time
from pathlib import Path

import run_state as rs
import work_check as wc

PY = f'"{sys.executable}"'
GATE = str(Path(__file__).resolve().parent.parent / "finish_gate.py")


def _project(tmp_path, **cfg):
    root = tmp_path / "p"
    root.mkdir()
    (root / "check.py").write_text("import sys\nt = open(sys.argv[1]).read()\nsys.exit(0 if 'good' in t else print('bad: ' + t) or 3)\n")
    (root / "show.py").write_text("import sys, shutil\nopen(sys.argv[2], 'wb').write(b'PNG' + open(sys.argv[1], 'rb').read())\n")
    (root / wc.CONFIG).write_text(json.dumps({"checks": [{"files": "*.out", "check": PY + " check.py {file}",
                                                          "show": PY + " show.py {file} {png}"}], **cfg}))
    return root


def test_no_config_or_untouched_files_means_no_opinion(tmp_path):
    assert wc.unfinished(str(tmp_path), 0, {}) == ""
    root = _project(tmp_path)
    (root / "a.out").write_text("bad")
    assert wc.unfinished(str(root), time.time() + 60, {}) == ""        # written before the run started: not this run's work


def test_a_failing_check_sends_it_back_with_the_output_then_lets_go(tmp_path):
    root = _project(tmp_path)
    started = time.time() - 1
    (root / "a.out").write_text("bad thing")
    memo = {}
    reasons = [wc.unfinished(str(root), started, memo) for _ in range(wc.LIMITS["check"] + 1)]
    assert all("a.out fails the project's own check" in r and "bad: bad thing" in r and "exit 3" in r for r in reasons[:-1])
    assert reasons[-1] == ""


def test_good_work_must_be_looked_at_once_per_version(tmp_path):
    root = _project(tmp_path)
    started = time.time() - 1
    (root / "a.out").write_text("good v1")
    memo = {}
    first = wc.unfinished(str(root), started, memo)
    assert "You haven't looked at this version" in first
    png = first.split("viewer: ")[1].split(". You")[0]
    assert Path(png).read_bytes() == b"PNGgood v1" and not png.startswith(str(root))     # drawn outside the repo
    assert wc.unfinished(str(root), started, memo) == ""                                # seen: done
    (root / "a.out").write_text("good v2")
    assert "You haven't looked at this version" in wc.unfinished(str(root), started, memo)   # a new version is shown again


def test_the_budget_counts_this_runs_output_tokens(tmp_path):
    root = _project(tmp_path, budget=1000)
    started = time.time() - 1
    (root / "a.out").write_text("good")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(started + 0.5))
    old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(started - 3600))
    transcript = tmp_path / "t.jsonl"
    rows = [{"type": "assistant", "timestamp": old, "message": {"id": "m0", "usage": {"output_tokens": 5000}}},     # an earlier turn
            {"type": "assistant", "timestamp": stamp, "message": {"id": "m1", "usage": {"output_tokens": 300}}},
            {"type": "assistant", "timestamp": stamp, "message": {"id": "m1", "usage": {"output_tokens": 300}}}]    # the same reply again
    transcript.write_text("\n".join(json.dumps(r) for r in rows))
    assert wc.output_tokens(str(transcript), started) == 300
    memo = {}
    wc.unfinished(str(root), started, memo, str(transcript))                            # the look first
    budget = [wc.unfinished(str(root), started, memo, str(transcript)) for _ in range(wc.LIMITS["budget"] + 1)]
    assert all("budget of about 1,000 output tokens" in b and "spent 300" in b for b in budget[:-1])
    assert budget[-1] == ""


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True)


def test_the_cli_stop_hook_asks_the_work_check_first(tmp_path):
    root = _project(tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "first")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"clone": str(root), "baseline": [], "head": rs.git_text(str(root), "rev-parse", "HEAD"),
                                 "sent_back": 0, "started": time.time() - 1}))
    (root / "a.out").write_text("bad")
    import os
    out = subprocess.run([sys.executable, GATE], input="{}", capture_output=True, text=True,
                         env={**os.environ, "SKIPPY_FINISH_STATE": str(state)}).stdout
    assert "fails the project's own check" in json.loads(out)["reason"]
    assert json.loads(state.read_text())["work"]["sent"] == {"check": 1}
