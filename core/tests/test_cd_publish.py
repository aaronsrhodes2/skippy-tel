"""skippy-core's publisher (core/cd/cd.py publish_all) with real git (bare repos standing in for the trunk) and Docker and gcloud faked:
a repo is published when its tip changes and not otherwise; a tarball's version is the TREE of its folder, so a commit elsewhere
publishes nothing; a failure is said once and retried later without stopping the rest; and what is uploaded is what the VM expects."""
import io
import json
import subprocess
import tarfile

import pytest

pytest.importorskip("fcntl")

from core_testlib import load  # noqa: E402

cd_mod = load("cd/cd.py", "core_cd_publish")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class FakeCD(cd_mod.CD):
    def __init__(self, s):
        super().__init__(s, log=lambda m: None)
        self.cfg = json.loads((cd_mod.HERE / "deploy.json").read_text())
        self.docker_runs, self.uploads, self.fail_steps = [], [], False
        self.said = []
        self.say = self.said.append
        cd_mod.oidc.token = lambda: None

    def sh(self, cmd, cwd=None, timeout=1800, input=None, quiet=False):
        if cmd[0] == "docker":
            self.docker_runs.append(cmd)
            if self.fail_steps:
                raise cd_mod.Failed("npm test failed")
            # the "build": the container would leave dist/ in the checkout
            src = next(a.split(":")[0] for a in cmd if a.endswith(":/src"))
            from pathlib import Path
            (Path(src) / "dist").mkdir(exist_ok=True)
            (Path(src) / "dist" / "index.html").write_text("<html>")
            (Path(src) / "dist" / "version.json").write_text('{"build": "x"}')
            return ""
        return super().sh(cmd, cwd=cwd, timeout=timeout, input=input, quiet=quiet)

    def gcloud(self, *args, cwd=None, timeout=900, quiet=False):
        listing = sorted(p.name for p in cwd.iterdir()) if cwd else []
        self.uploads.append((args, listing, cd_mod.tarfile.open(cwd / "addon.tar.gz").getnames() if cwd and (cwd / "addon.tar.gz").exists() else None))
        return ""


@pytest.fixture
def trunk(tmp_path):
    home = tmp_path / "trunk-git"
    home.mkdir()

    def repo(name, branch):
        bare, work = home / f"{name}.git", tmp_path / f"w-{name}"
        git(tmp_path, "init", "--quiet", "--bare", "-b", branch, str(bare))
        git(tmp_path, "clone", "--quiet", str(bare), str(work))
        for k, v in (("user.name", "T"), ("user.email", "t@example.com")):
            git(work, "config", k, v)
        return work

    def commit(work, branch, path, text):
        f = work / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        git(work, "add", "-A")
        git(work, "commit", "--quiet", "-m", f"change {path}")
        git(work, "push", "--quiet", "origin", f"HEAD:{branch}")
        return git(work, "rev-parse", "HEAD")
    s = {**cd_mod.DEFAULTS, "CD_STATE": str(tmp_path / "cd"), "CD_TRUNK_GIT": str(home)}
    return s, repo, commit


def specs():
    return json.loads((cd_mod.HERE / "publish.json").read_text())["publish"]


def test_the_publish_file_names_what_the_github_workflows_and_the_vm_used_to_fetch():
    s = specs()
    assert s["doom-loop"]["kind"] == "site" and s["doom-loop"]["bucket"] == "skippy-tel-doomloop-dev" and s["doom-loop"]["last"] == "version.json"
    assert s["gmod-addon"]["kind"] == "tarball" and s["gmod-addon"]["path"] == "stage/gmod/addon" and s["gmod-addon"]["repo"] == "sigma-ground"


def test_a_new_tip_of_a_site_is_tested_built_and_synced_with_the_version_file_last(trunk, tmp_path):
    s, repo, commit = trunk
    work = repo("doom-loop", "main")
    tip = commit(work, "main", "package.json", "{}")
    cd = FakeCD(s)
    cd.publish_all()
    (cmd,) = cd.docker_runs
    assert "node:24" in cmd and cmd[-1].startswith("set -e; npm ci && npm test && bash scripts/build-web && node scripts/check-dist.mjs")
    assert f"GITHUB_REF_NAME=main" in cmd
    rsync, cp = [u[0] for u in cd.uploads if u[0][1] in ("rsync", "cp")][:2]
    assert rsync[:2] == ("storage", "rsync") and "gs://skippy-tel-doomloop-dev/dev" in rsync and "--delete-unmatched-destination-objects" in rsync
    assert any(a.startswith("--exclude=^version") for a in rsync)                          # the version file is not part of the sync...
    assert cp[:2] == ("storage", "cp") and cp[3].endswith("/dev/version.json") and "--cache-control=no-store" in cp   # ...it goes last
    assert cd.state["published"]["doom-loop"]["key"] == tip
    n = len(cd.docker_runs)
    cd.publish_all()
    assert len(cd.docker_runs) == n                                                       # the same tip: nothing again


def test_a_failed_test_uploads_nothing_is_said_once_and_retried_after_the_wait(trunk):
    s, repo, commit = trunk
    work = repo("doom-loop", "main")
    commit(work, "main", "package.json", "{}")
    cd = FakeCD(s)
    cd.fail_steps = True
    cd.publish_all()
    cd.publish_all()
    assert cd.uploads == [] and len(cd.docker_runs) == 1                                  # not retried at once
    assert sum("publishing doom-loop failed" in m for m in cd.said) == 1                  # said once
    cd.state["published"]["doom-loop"]["failed_at"] -= cd.PUBLISH_RETRY_S + 1
    cd.fail_steps = False
    cd.publish_all()
    assert len(cd.docker_runs) == 2 and "error" not in cd.state["published"]["doom-loop"] and cd.uploads


def test_the_addon_tarball_is_keyed_by_its_folder_so_other_commits_publish_nothing(trunk):
    s, repo, commit = trunk
    work = repo("sigma-ground", "master")
    tip1 = commit(work, "master", "stage/gmod/addon/lua/autorun/mentat_stage.lua", "print('v1')")
    commit(work, "master", "README.md", "x")
    cd = FakeCD(s)
    cd.publish_all()
    uploads = [u for u in cd.uploads if u[0][:2] == ("storage", "cp")]
    names = sorted(u[0][3].rsplit("/", 1)[-1] for u in uploads)
    tip2 = git(work, "rev-parse", "HEAD")
    assert names == sorted([f"{tip2}.tar.gz", "master.tar.gz"])                           # a pin and the tip
    assert "addon/lua/autorun/mentat_stage.lua" in uploads[0][2] and "SOURCE" in uploads[0][2]
    n = len(cd.uploads)
    commit(work, "master", "docs/other.md", "y")                                           # a commit outside the addon
    cd.publish_all()
    assert len(cd.uploads) == n
    commit(work, "master", "stage/gmod/addon/lua/autorun/mentat_stage.lua", "print('v2')")
    cd.publish_all()
    assert len(cd.uploads) == n + 2


def test_the_tarball_carries_the_tree_and_commit_the_vm_records(trunk, tmp_path):
    s, repo, commit = trunk
    work = repo("sigma-ground", "master")
    tip = commit(work, "master", "stage/gmod/addon/lua/a.lua", "print(1)")
    tree = git(work, "rev-parse", f"{tip}:stage/gmod/addon")
    captured = {}
    cd = FakeCD(s)
    real = cd.gcloud

    def keep(*args, cwd=None, **k):
        if args[:2] == ("storage", "cp") and cwd and "bytes" not in captured:
            captured["bytes"] = (cwd / "addon.tar.gz").read_bytes()
        return real(*args, cwd=cwd, **k)
    cd.gcloud = keep
    cd.publish_all()
    with tarfile.open(fileobj=io.BytesIO(captured["bytes"])) as t:
        assert t.extractfile("SOURCE").read().decode().strip() == f"{tree} {tip}"
        assert t.extractfile("addon/lua/a.lua").read() == b"print(1)"


def test_a_trunk_that_is_away_is_a_said_failure_not_a_crash(tmp_path):
    cd = FakeCD({**cd_mod.DEFAULTS, "CD_STATE": str(tmp_path / "cd"), "CD_TRUNK_GIT": str(tmp_path / "gone")})
    cd.publish_all()
    assert cd.uploads == [] and any("is skippy-pc on" in m for m in cd.said)
