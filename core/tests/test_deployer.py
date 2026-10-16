"""The pull deployer's promises, with real git (a bare origin and the box's clone) and fake models/compose/smoke:
deploy a green commit once, wait for CI, never retry a bad key, refuse missing models without touching the running
stack, and put the last good commit back when a deploy goes red."""
import json
import subprocess
from pathlib import Path

import pytest

from core_testlib import load

dep = load("deployer/deployer.py", "core_deployer")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Fake(dep.Deployer):
    """Each commit carries fake.json saying how its deploy goes: {"models": 0|1|2, "compose": bool, "smoke": bool}."""

    def __init__(self, settings):
        super().__init__(settings, log=lambda m: None)
        self.ci, self.deployed, self.running = {}, [], None

    def ci_state(self, commit):
        return self.ci.get(commit, ("success", "green"))

    def _fake(self):
        return json.loads((self.repo / "fake.json").read_text())

    def models(self, cmd, full):
        rc = self._fake().get("models", 0) if cmd == "check" else 0
        if full == "general" and cmd == "check":
            rc = 1                                           # the big general model's files aren't there
        return subprocess.CompletedProcess([], rc, "", "missing /srv/models/x" if rc else "")

    def compose_up(self, full):
        if not self._fake().get("compose", True):
            raise subprocess.CalledProcessError(1, "compose", "", "build failed")
        self.running = (git(self.repo, "rev-parse", "HEAD"), full)
        self.deployed.append(self.running)

    def smoke(self, full):
        ok = self._fake().get("smoke", True)
        return [{"check": "chat", "ok": ok, "detail": "" if ok else "no reply"}]


@pytest.fixture
def box(tmp_path):
    origin, work, clone = tmp_path / "origin.git", tmp_path / "work", tmp_path / "clone"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "--quiet", str(origin), str(work))
    git(work, "config", "user.email", "t@t")
    git(work, "config", "user.name", "t")

    counter = {"n": 0}

    def commit(**behaviour):
        (work / "fake.json").write_text(json.dumps(behaviour))
        counter["n"] += 1
        (work / "n").write_text(str(counter["n"]))       # every commit differs
        git(work, "add", "-A")
        git(work, "commit", "--quiet", "-m", "c")
        git(work, "push", "--quiet", "origin", "HEAD:main")
        return git(work, "rev-parse", "HEAD")

    first = commit()
    git(tmp_path, "clone", "--quiet", str(origin), str(clone))
    settings = {**dep.DEFAULTS, "REPO_DIR": str(clone), "CORE_STATE": str(tmp_path / "state"), "CI_WORKFLOW": "core.yml"}
    return Fake(settings), commit, first


def test_a_green_commit_deploys_once(box):
    d, commit, first = box
    assert d.run()["state"] == "ok"
    assert d.deployed == [(first, "")]
    row = d.run()
    assert row["state"] == "ok" and row["detail"] == "up to date" and len(d.deployed) == 1
    assert json.loads((d.dir / "status.json").read_text())["last_good"]["commit"] == first


def test_it_waits_for_ci_and_never_deploys_a_red_one(box):
    d, commit, first = box
    d.run()
    second = commit()
    d.ci[second] = ("pending", "running")
    assert d.run()["state"] == "waiting-ci" and d.running == (first, "")
    d.ci[second] = ("failure", "red")
    assert d.run()["state"] == "ci-failed"
    d.ci[second] = ("success", "")                          # a re-run going green doesn't resurrect a bad key...
    assert d.run()["state"] == "known-bad" and d.running == (first, "")
    d._write("state.json", {**d.state(), "bad": {}})        # ...`deployer.py forget` does
    assert d.run()["state"] == "ok" and d.running == (second, "")


def test_a_red_smoke_rolls_back_and_is_never_retried(box):
    d, commit, first = box
    d.run()
    broken = commit(smoke=False)
    row = d.run()
    assert row["state"] == "rolled-back" and row["stage"] == "smoke" and "no reply" in row["detail"]
    assert d.deployed[-2:] == [(broken, ""), (first, "")] and d.running == (first, "")
    assert git(d.repo, "rev-parse", "HEAD") == first
    assert d.run()["state"] == "known-bad" and len(d.deployed) == 3
    fixed = commit()
    assert d.run()["state"] == "ok" and d.running == (fixed, "")


def test_a_failed_build_rolls_back_but_is_retried(box):
    """PyPI or GHCR being down is not a verdict on the commit: never mark it bad for that."""
    d, commit, first = box
    d.run()
    flaky = commit(compose=False)
    row = d.run()
    assert row["state"] == "rolled-back" and row["will_retry"] is True and d.running == (first, "")
    assert f"{flaky}|" not in d.state()["bad"]
    (d.repo / "fake.json").write_text("{}")             # the network is back (the fake reads the checkout)
    d._fake = lambda: {}
    assert d.run()["state"] == "ok" and d.running == (flaky, "")


def test_a_broken_manifest_is_bad_but_missing_models_are_only_refused(box):
    d, commit, first = box
    d.run()
    commit(models=2)
    assert d.run()["state"] == "rolled-back"
    good = commit()
    assert d.run()["state"] == "ok"
    d.s["CORE_FULL"] = "general"
    row = d.run()
    assert row["state"] == "refused" and "missing" in row["detail"]
    assert d.running == (good, "") and git(d.repo, "rev-parse", "HEAD") == good, "the running stack is left alone"
    assert f"{good}|general" not in d.state()["bad"], "it deploys once the files land"


def test_onlining_a_slot_is_a_new_key(box):
    d, commit, first = box
    d.run()
    d.s["CORE_FULL"] = "coder, chat"
    assert d.run()["state"] == "ok" and d.running == (first, "chat,coder")


def test_nothing_to_roll_back_to(box):
    d, commit, first = box
    d._fake = lambda: {"smoke": False}
    row = d.run()
    assert row["state"] == "failed" and row["rollback"] == "nothing to roll back to"


def test_settings_file_then_environment(tmp_path, monkeypatch):
    f = tmp_path / "core.env"
    f.write_text("# c\nCORE_FULL=chat\nGITHUB_TOKEN='t0k'\nCI_WORKFLOW=\n")
    for k in dep.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    s = dep.load_settings(str(f))
    assert s["CORE_FULL"] == "chat" and s["GITHUB_TOKEN"] == "t0k" and s["CI_WORKFLOW"] == "" and s["BRANCH"] == "main"
    monkeypatch.setenv("CORE_FULL", "all")
    assert dep.load_settings(str(f))["CORE_FULL"] == "all"
    assert dep.norm_full(" coder,chat,") == "chat,coder" and dep.norm_full("chat,all") == "all"


def test_ci_state_reads_the_newest_check_run(box, monkeypatch):
    d, _, _ = box
    real = dep.Deployer(d.s, log=lambda m: None)
    payloads = []

    class R:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    def urlopen(req, timeout):
        payloads.append(req.full_url)
        return R(answer)

    monkeypatch.setattr(dep.urllib.request, "urlopen", urlopen)
    answer = {"workflow_runs": []}
    assert real.ci_state("abc")[0] == "pending"
    assert "/actions/workflows/core.yml/runs?head_sha=abc&event=push" in payloads[-1]
    answer = {"workflow_runs": [{"status": "completed", "conclusion": "failure", "run_attempt": 1, "created_at": "1"},
                                {"status": "completed", "conclusion": "success", "run_attempt": 2, "created_at": "1"}]}
    assert real.ci_state("abc")[0] == "success", "a re-run that went green counts"
    answer = {"workflow_runs": [{"status": "completed", "conclusion": "failure", "run_attempt": 1, "created_at": "2"}]}
    assert real.ci_state("abc")[0] == "failure"
    answer = {"workflow_runs": [{"status": "in_progress", "run_attempt": 1, "created_at": "3"}]}
    assert real.ci_state("abc")[0] == "pending"
    real.s = {**real.s, "CI_WORKFLOW": ""}
    assert real.ci_state("abc") == ("success", "no CI gate")
