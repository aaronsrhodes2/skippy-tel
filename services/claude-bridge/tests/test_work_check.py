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


def _eyes_run(tmp_path, said):
    """A Core run (the coder can't see) with a task in its transcript and a fake Core whose reviews are `said`, in turn."""
    root = _project(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(d) for d in [
        {"type": "user", "message": {"content": "build a choo-choo train"}},
        {"type": "user", "message": {"content": "Stop hook feedback:\nthe gate said something"}}]) + "\n")
    asked = []

    def post(req):
        body = json.loads(req.data)
        asked.append(body["messages"][0]["content"][1]["text"])
        return {"content": [{"type": "text", "text": said[min(len(asked), len(said)) - 1]}]}
    env = {"SKIPPY_CORE_EYES": "1", "ANTHROPIC_BASE_URL": "http://core", "ANTHROPIC_API_KEY": "k"}
    return root, str(transcript), env, post, asked


def test_a_core_run_is_sent_back_with_the_worst_fault_until_a_review_names_none(tmp_path):
    root, transcript, env, post, asked = _eyes_run(tmp_path, [
        "A train. The wheels float above the rails.\nWORST FAULT: the wheels float above the rails",
        "Fixed: yes, the wheels sit on the rails.\n**WORST FAULT:** none"])
    started, memo = time.time() - 1, {}
    (root / "a.out").write_text("good v1")
    first = wc.unfinished(str(root), started, memo, transcript, env=env, post=post)
    assert "The worst fault: the wheels float above the rails" in first and "no need to open it" in first
    assert "build a choo-choo train" in asked[0] and "builder's a.out as the project's own viewer draws it" in asked[0] and "the gate said" not in asked[0]       # the task, never the gate's own words
    assert "hasn't changed since its worst fault" in wc.unfinished(str(root), started, memo, transcript, env=env, post=post)
    assert len(asked) == 1                                                               # unchanged: not reviewed again
    (root / "a.out").write_text("good v2")
    assert wc.unfinished(str(root), started, memo, transcript, env=env, post=post) == ""
    assert "fix this fault last time: the wheels float above the rails" in asked[1]      # checked first for that fault
    assert memo["faults"] == {}


def test_the_fault_loop_ends_when_its_sends_are_spent(tmp_path):
    root, transcript, env, post, asked = _eyes_run(tmp_path, ["Still broken.\nWORST FAULT: it is sideways"])
    started, memo = time.time() - 1, {}
    reasons = []
    for i in range(wc.LIMITS["fault"] + 2):
        (root / "a.out").write_text(f"good v{i}")
        reasons.append(wc.unfinished(str(root), started, memo, transcript, env=env, post=post))
    assert all("it is sideways" in r for r in reasons[:wc.LIMITS["fault"]]) and reasons[-1] == ""
    assert len(asked) == wc.LIMITS["fault"]                                              # no reviews nobody can act on


def test_core_unable_to_review_falls_back_to_the_plain_look(tmp_path):
    root, transcript, env, _, _ = _eyes_run(tmp_path, [])

    def down(req):
        raise OSError("Core is away")
    (root / "a.out").write_text("good")
    assert "You haven't looked at this version" in wc.unfinished(str(root), time.time() - 1, {}, transcript, env=env, post=down)


def test_a_viewer_that_cannot_draw_is_said_once(tmp_path):
    root = _project(tmp_path)
    cfg = json.loads((root / wc.CONFIG).read_text())
    cfg["checks"][0]["show"] = PY + ' -c "import sys; print(\'no GPU\'); sys.exit(5)"'
    (root / wc.CONFIG).write_text(json.dumps(cfg))
    (root / "a.out").write_text("good")
    started, memo = time.time() - 1, {}
    first = wc.unfinished(str(root), started, memo)
    assert "couldn't draw a.out" in first and "exit 5" in first and "no GPU" in first
    assert wc.unfinished(str(root), started, memo) == ""
