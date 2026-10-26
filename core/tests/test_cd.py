"""skippy-core's deployer (core/cd/cd.py) with real git and CI results, and Docker, gcloud, the token and the network faked: it deploys
main's newest commit only when CI passed it, once; Mentat and infra only when what they're built from changed; a failed portal deploy
puts the noted revision back; Recovery's requests are carried out once."""
import json
import subprocess

import pytest

pytest.importorskip("fcntl")

from core_testlib import load  # noqa: E402

cd_mod = load("cd/cd.py", "core_cd")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class FakeCD(cd_mod.CD):
    def __init__(self, s, fail=()):
        super().__init__(s, log=lambda m: None)
        self.fail, self.calls, self.reports = set(fail), [], []
        self.requests = []
        cd_mod.oidc.token = lambda: None

    def report(self):
        self.reports.append(json.loads(json.dumps(self.state["runs"][:1])))
        out, self.requests = self.requests, []
        return out

    def sh(self, cmd, cwd=None, timeout=1800, input=None, quiet=False):
        self.calls.append(("sh", cmd[1] if len(cmd) > 1 else cmd[0]))
        if "build" in cmd and "build" in self.fail:
            raise cd_mod.Failed("docker build failed")
        return ""

    def docker_login(self):
        return "/tmp/docker-config"

    def gcloud(self, *args, cwd=None, timeout=900, quiet=False):
        self.calls.append(("gcloud", " ".join(args[:3])))
        if args[:2] == ("run", "deploy") and "deploy" in self.fail:
            raise cd_mod.Failed("gcloud run deploy failed")
        if args[:3] == ("run", "services", "describe"):
            return "https://portal.example\n" if any("status.url" in a for a in args) else "skippy-portal-00041-old\n"
        return ""

    def smoke_portal(self, src):
        self.calls.append(("smoke", "portal"))
        if "smoke" in self.fail:
            raise cd_mod.Failed("smoke test: /health answers")

    def smoke_mentat(self, u):
        self.calls.append(("smoke", "mentat"))


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@example.com")):
        git(repo, "config", k, v)
    (repo / "core" / "cd").mkdir(parents=True)
    real = json.loads((cd_mod.HERE / "deploy.json").read_text())
    (repo / "core" / "cd" / "deploy.json").write_text(json.dumps(real))
    ci = tmp_path / "ci" / "skippy-tel"
    (ci / "results").mkdir(parents=True)

    def commit(path, text, ci_state="success"):
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        git(repo, "add", "-A")
        git(repo, "commit", "--quiet", "-m", f"change {path}")
        sha = git(repo, "rev-parse", "HEAD")
        subprocess.run(["git", "clone", "--quiet", "--mirror", str(repo), str(ci / "mirror.git")], capture_output=True)
        git(ci / "mirror.git", "fetch", "--quiet", str(repo), "+refs/heads/main:refs/heads/main")
        if ci_state:
            (ci / "results" / f"{sha}.json").write_text(json.dumps({"state": ci_state}))
        return sha
    s = {**cd_mod.DEFAULTS, "CD_STATE": str(tmp_path / "cd"), "CI_LOCAL": str(tmp_path / "ci"), "CD_VAULT": str(tmp_path / "none")}
    return s, commit


def jobs(run):
    return [(j["name"], j["conclusion"]) for j in run["jobs"]]


def test_a_green_commit_is_deployed_once_with_everything_the_first_time(world):
    s, commit = world
    sha = commit("portal/app.py", "one")
    cd = FakeCD(s)
    cd.run()
    run = cd.state["runs"][0]
    assert run["head_sha"] == sha and run["conclusion"] == "success"
    assert jobs(run) == [("test", "success"), ("portal-image", "success"), ("portal", "success"), ("mentat", "success"),
                         ("infra", "success")]
    assert cd.state["deployed"] == {"portal": sha, "mentat": sha, "infra": sha}
    before = len(cd.calls)
    cd.run()
    assert len(cd.calls) == before                                                  # already delivered: nothing again


def test_an_untested_or_red_commit_is_never_deployed(world):
    s, commit = world
    commit("portal/app.py", "x", ci_state=None)
    cd = FakeCD(s)
    cd.run()
    assert cd.state["runs"] == []
    commit("portal/app.py", "y", ci_state="failure")
    cd.run()
    assert cd.state["runs"] == []


def test_mentat_and_infra_only_when_what_they_are_built_from_changed(world):
    s, commit = world
    commit("portal/app.py", "one")
    cd = FakeCD(s)
    cd.run()
    commit("portal/app.py", "two")
    cd.run()
    assert [n for n, _ in jobs(cd.state["runs"][0])] == ["test", "portal-image", "portal"]
    commit("mentat/app.py", "three")
    cd.run()
    assert [n for n, _ in jobs(cd.state["runs"][0])] == ["test", "portal-image", "portal", "mentat"]


def test_a_failed_smoke_test_puts_the_old_revision_back(world):
    s, commit = world
    commit("portal/app.py", "one")
    cd = FakeCD(s, fail={"smoke"})
    cd.run()
    run = cd.state["runs"][0]
    assert run["conclusion"] == "failure" and ("portal", "failure") in jobs(run)
    assert ("gcloud", "run services update-traffic") in cd.calls
    assert "rolled back to skippy-portal-00041-old" in next(j["why"] for j in run["jobs"] if j["name"] == "portal")
    assert "portal" not in cd.state["deployed"]


def test_recovery_asks_and_it_is_done_once(world):
    s, commit = world
    sha = commit("portal/app.py", "one")
    cd = FakeCD(s)
    cd.run()
    cd.requests = [{"id": 5, "op": "deploy", "arg": ""}]
    cd.run()
    assert [r["head_sha"] for r in cd.state["runs"][:2]] == [sha, sha] and 5 in cd.state["done_requests"]
    cd.requests = [{"id": 5, "op": "deploy", "arg": ""}]
    cd.run()
    assert len(cd.state["runs"]) == 2                                               # the same request again: not twice


def test_the_portal_follows_each_job_live(world):
    s, commit = world
    commit("portal/app.py", "one")
    cd = FakeCD(s)
    cd.run()
    seen = [r[0]["jobs"][-1]["name"] for r in cd.reports if r and r[0]["jobs"]]
    assert {"portal-image", "portal"} <= set(seen)
