"""SkippyCore's CI (core/ci/ci.py) and the deployer's gate on it, with real git (a bare repo standing in for skippy-pc's
D:\\trunk\\git) and Docker faked: a commit is tested once; a failed test is a failure the deployer refuses; the box's own
trouble is an error, retried at most three times and never a failure; the deployer deploys only a tested-green commit."""
import json
import subprocess

import pytest

pytest.importorskip("fcntl")                      # skippy-core is Linux; the CI on it runs these

from core_testlib import load  # noqa: E402

ci_mod = load("ci/ci.py", "core_ci")
dep = load("deployer/deployer.py", "core_deployer_ci")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class FakeCI(ci_mod.CI):
    """Docker is faked: each job's outcome comes from `outcomes` (name -> "success" | "failure" | "error")."""

    def __init__(self, s, outcomes=None):
        super().__init__(s, log=lambda m: None)
        self.outcomes, self.tested = outcomes or {}, []

    def run_jobs(self, name, sha, work):
        assert (work / ".git").exists() and (work / "f.txt").is_file()        # a real checkout, with its .git
        self.tested.append(sha)
        out = []
        for job in ci_mod.PLANS["skippy-tel"]:
            state = self.outcomes.get(job["name"], "success")
            if state == "error":
                raise RuntimeError("docker is down")
            out.append({"name": job["name"], "state": state, "why": "", "seconds": 1, "tail": ""})
        return out


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "skippy-tel.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(home))
    git(tmp_path, "clone", "--quiet", str(home), str(work))
    for k, v in (("user.name", "T"), ("user.email", "t@example.com")):
        git(work, "config", k, v)

    def commit(text):
        (work / "f.txt").write_text(text)
        git(work, "add", "f.txt")
        git(work, "commit", "--quiet", "-m", text)
        git(work, "push", "--quiet", "origin", "main")
        return git(work, "rev-parse", "HEAD")
    s = {**ci_mod.DEFAULTS, "CI_REPOS": f"skippy-tel={home}", "CI_STATE": str(tmp_path / "ci")}
    return s, commit


def test_the_newest_commit_is_tested_once(world):
    s, commit = world
    sha = commit("one")
    ci = FakeCI(s)
    assert [r["state"] for r in ci.run()] == ["success"] and ci.tested == [sha]
    assert ci.run() == [] and ci.tested == [sha]                              # nothing new: nothing tested
    assert ci_mod.gate(s["CI_STATE"], "skippy-tel", sha) == ("success", "tested in 0 s")
    two = commit("two")
    ci.run()
    assert ci.tested == [sha, two] and json.loads((ci.state / "skippy-tel" / "latest.json").read_text())["sha"] == two


def test_a_failed_test_is_a_failure_and_never_retried(world):
    s, commit = world
    sha = commit("broken")
    ci = FakeCI(s, {"suite": "failure"})
    ci.run()
    ci.run()
    assert ci.tested == [sha]
    assert ci_mod.gate(s["CI_STATE"], "skippy-tel", sha) == ("failure", "failed: suite")


def test_the_boxs_own_trouble_is_an_error_tried_three_times_and_never_a_failure(world):
    s, commit = world
    sha = commit("fine")
    ci = FakeCI(s, {"suite": "error"})
    for _ in range(5):
        ci.run()
    assert ci.tested == [sha] * 3
    state, _ = ci_mod.gate(s["CI_STATE"], "skippy-tel", sha)
    assert state == "pending"
    ci.outcomes = {}
    ci.test("skippy-tel", sha)                                                # `ci.py test` after the box is fixed
    assert ci_mod.gate(s["CI_STATE"], "skippy-tel", sha)[0] == "success"


def test_an_unreachable_home_is_skipped_quietly(tmp_path):
    s = {**ci_mod.DEFAULTS, "CI_REPOS": f"skippy-tel={tmp_path / 'away.git'}", "CI_STATE": str(tmp_path / "ci")}
    assert FakeCI(s).run() == []


def test_the_deployer_gates_on_this_boxs_ci(world, tmp_path):
    s, commit = world
    good, bad = commit("good"), None
    ci = FakeCI(s)
    ci.run()
    bad = commit("bad")
    FakeCI(s, {"flask-api": "failure"}).run()
    d = dep.Deployer({**dep.DEFAULTS, "CI_LOCAL": s["CI_STATE"], "CI_NAME": "skippy-tel", "CORE_STATE": str(tmp_path / "core")},
                     log=lambda m: None)
    assert d.ci_state(good)[0] == "success"
    assert d.ci_state(bad) == ("failure", "this box's CI failed: flask-api")
    assert d.ci_state("0" * 40)[0] == "pending"
