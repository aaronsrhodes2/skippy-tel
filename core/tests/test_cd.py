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

    def vendor(self, src, star_control):
        self.calls.append(("vendor", star_control))

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


def test_each_job_is_stamped_and_the_portal_says_when_it_served(world):
    """tools/loop_timer.py times the loop from these: every job's started_at and completed_at, and the portal job's serving_at (all
    traffic on the new revision), which falls inside the job, before its smoke test ends."""
    s, commit = world
    commit("portal/app.py", "one")
    cd = FakeCD(s)
    cd.run()
    for job in cd.state["runs"][0]["jobs"][1:]:
        assert job["started_at"] <= job["completed_at"]
    portal = next(j for j in cd.state["runs"][0]["jobs"] if j["name"] == "portal")
    assert portal["started_at"] <= portal["serving_at"] <= portal["completed_at"]
    assert all("serving_at" not in j for j in cd.state["runs"][0]["jobs"] if j["name"] != "portal")


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


# ── .vendor: our own code for the builds, from the trunk, never the internet ───────────────────────────────

class VendorCD(cd_mod.CD):
    def __init__(self, s):
        super().__init__(s, log=lambda m: None)
        self.cfg = json.loads((cd_mod.HERE / "deploy.json").read_text())


@pytest.fixture
def trunk(tmp_path):
    """A stand-in trunk: sigma-ground as a bare repo with the pinned commit, and the Star Control tarball with its checksum."""
    import hashlib
    import tarfile
    work = tmp_path / "sg"
    work.mkdir()
    git(work, "init", "--quiet", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@example.com")):
        git(work, "config", k, v)
    (work / "sigma_ground" / "radiance" / "web").mkdir(parents=True)
    (work / "sigma_ground" / "radiance" / "web" / "viewer.js").write_text("x")
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", "sg")
    pin = git(work, "rev-parse", "HEAD")
    bare = tmp_path / "trunk-git" / "sigma-ground.git"
    bare.parent.mkdir()
    git(tmp_path, "clone", "--quiet", "--bare", str(work), str(bare))
    art = tmp_path / "trunk-art" / "star-control" / "web-test"
    art.mkdir(parents=True)
    (tmp_path / "scz" / "scz-web").mkdir(parents=True)
    (tmp_path / "scz" / "scz-web" / "index.html").write_text("<html>")
    with tarfile.open(art / "scz-web.tar.gz", "w:gz") as t:
        t.add(tmp_path / "scz" / "scz-web", arcname="scz-web")
    sha = hashlib.sha256((art / "scz-web.tar.gz").read_bytes()).hexdigest()
    s = {**cd_mod.DEFAULTS, "CD_STATE": str(tmp_path / "cd"), "CD_TRUNK_GIT": str(bare.parent), "CD_TRUNK_ARTIFACTS": str(tmp_path / "trunk-art")}
    cd = VendorCD(s)
    cd.cfg["pins"].update(SIGMA_GROUND_REF=pin, STARCONTROL_REF="web-test", STARCONTROL_SHA256=sha)
    src = tmp_path / "src"
    src.mkdir()
    return cd, src, tmp_path


def test_vendor_puts_the_pinned_sigma_ground_and_the_game_in_the_build_context(trunk):
    cd, src, _ = trunk
    cd.vendor(src, star_control=True)
    assert (src / ".vendor" / "sigma-ground" / "sigma_ground" / "radiance" / "web" / "viewer.js").read_text() == "x"
    assert (src / ".vendor" / "scz-web.tar.gz").is_file()
    cd.vendor(src, star_control=False)                                           # Mentat: sigma-ground only
    assert (src / ".vendor" / "sigma-ground").is_dir()


def test_vendor_lays_out_every_pop_up_registry_file_for_the_portal_image(trunk):
    """The portal image once held only the test pop-up's registry (the classic builder ignored the wildcard .dockerignore exception), so the
    portal did not know the vision, search, 3D or video tools. Now the registry is copied into the build context and counted."""
    cd, src, _ = trunk
    (src / "services" / "popups").mkdir(parents=True)
    (src / "services" / "popups" / "registry.json").write_text(json.dumps({"popups": [{"name": "echo"}]}))
    for fam, names in (("vision-popups", ["a", "b", "c"]), ("3d-video-popups", ["d"])):
        (src / "services" / fam).mkdir()
        (src / "services" / fam / "popups.json").write_text(json.dumps({"popups": [{"name": n} for n in names]}))
    (src / "services" / "flask-api").mkdir()                                     # not a registry: never copied
    (src / "services" / "flask-api" / "popups.json").write_text("{}")
    cd.vendor(src, star_control=True)
    got = sorted(p.relative_to(src / ".vendor" / "popups").as_posix() for p in (src / ".vendor" / "popups").rglob("*.json"))
    assert got == ["services/3d-video-popups/popups.json", "services/popups/registry.json", "services/vision-popups/popups.json"]
    assert cd.popup_count(src) == 5                                              # echo + 3 + 1: what the portal must say it knows after the deploy
    cd.vendor(src, star_control=True)                                            # again: no stale file survives
    assert len(list((src / ".vendor" / "popups").rglob("*.json"))) == 3


def test_a_swapped_game_file_is_refused(trunk):
    cd, src, tmp = trunk
    (tmp / "trunk-art" / "star-control" / "web-test" / "scz-web.tar.gz").write_bytes(b"not the game")
    with pytest.raises(cd_mod.Failed, match="sha256"):
        cd.vendor(src, star_control=True)


def test_a_pin_that_is_not_in_the_trunk_or_the_cache_stops_the_deploy_plainly(trunk):
    cd, src, _ = trunk
    cd.cfg["pins"]["SIGMA_GROUND_REF"] = "0" * 40
    with pytest.raises(cd_mod.Failed, match="is skippy-pc on"):
        cd.vendor(src, star_control=False)


def test_with_skippy_pc_off_a_deploy_still_works_from_what_this_box_has_seen(trunk):
    cd, src, tmp = trunk
    cd.vendor(src, star_control=True)                                            # seen once: cached on this box
    cd.s["CD_TRUNK_GIT"], cd.s["CD_TRUNK_ARTIFACTS"] = str(tmp / "gone"), str(tmp / "gone")
    src2 = tmp / "src2"
    src2.mkdir()
    cd.vendor(src2, star_control=True)
    assert (src2 / ".vendor" / "scz-web.tar.gz").is_file() and (src2 / ".vendor" / "sigma-ground" / "sigma_ground").is_dir()


class HealthOnly(cd_mod.CD):
    """Just the smoke test's /health wait: each GET answers the next code in `codes` (the last one repeats), no sleeping."""
    def __init__(self, codes):
        self.logfile, self.codes, self.asked, self.lines = None, list(codes), 0, []

    def log(self, text):
        self.lines.append(text)

    def http(self, method, url, body=None, follow=True, timeout=30, tries=1, headers=None):
        self.asked += 1
        code = self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]
        if code == 0:
            raise OSError("connection reset")
        return code, '{"ready": %s, "detail": "not ready"}' % ("true" if code == 200 else "false")


def test_mentat_health_waits_out_a_warming_new_revision_but_still_fails_one_that_never_gets_ready(monkeypatch):
    monkeypatch.setattr(cd_mod.time, "sleep", lambda s: None)
    warming = HealthOnly([503, 0, 503, 200])                     # a new revision says 503 while warming, a reset on the way
    assert warming.wait_ready("https://m.example/health") and warming.asked == 4
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(cd_mod.time, "monotonic", lambda: next(clock))
    never = HealthOnly([503])
    assert not never.wait_ready("https://m.example/health", wait=90)
    assert 10 <= never.asked <= 25 and "503" in never.lines[-1]  # bounded, and the last answer is in the run's log
    with pytest.raises(cd_mod.Failed, match="Mentat /health"):
        never.expect(never.wait_ready("https://m.example/health", wait=0), "Mentat /health")
