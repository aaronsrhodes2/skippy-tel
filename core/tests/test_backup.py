"""The nightly git backup (core/backup/backup.py) with real git (bare repos standing in for the trunk) and gcloud faked as a folder:
a repo is bundled and uploaded when its refs changed, never when they didn't; a bundle git can't read, or a bucket checksum that
disagrees, is a failure that keeps the old fingerprint; an empty repo and an absent trunk are quiet; and a bundle restores."""
import subprocess

import pytest

pytest.importorskip("fcntl")

from core_testlib import load  # noqa: E402

bk = load("backup/backup.py", "core_backup")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class FakeBackup(bk.Backup):
    """The bucket is a folder; `corrupt` makes it report a wrong checksum, `broken` makes uploads fail."""

    def __init__(self, s, bucket):
        super().__init__(s, log=lambda m: None)
        self.bucket, self.uploads, self.corrupt, self.broken = bucket, [], False, False
        bk.oidc.token = lambda: None

    def gcloud(self, *args, mounts=()):
        if args[:2] == ("storage", "cp"):
            if self.broken:
                raise bk.Failed("gcloud storage cp failed")
            src = args[2]
            if src.startswith("gs://"):                                      # a restore: the bucket to the stage folder
                (self.stage / src.rsplit("/", 1)[-1]).write_bytes((self.bucket / src.rsplit("/", 1)[-1]).read_bytes())
                return ""
            data = (self.work / src.rsplit("/", 1)[-1]).read_bytes()
            (self.bucket / args[3].rsplit("/", 1)[-1]).write_bytes(data)
            self.uploads.append(args[3].rsplit("/", 1)[-1])
            return ""
        if args[:3] == ("storage", "objects", "describe"):
            name = args[3].rsplit("/", 1)[-1]
            return "WRONG==" if self.corrupt else bk.md5_b64(self.bucket / name)
        raise AssertionError(args)

    def back_up(self, repo, work, force=False):
        self.work = work
        return super().back_up(repo, work, force)

    def restore(self, name, dest):
        self.stage = self.state_dir / "stage"
        self.stage.mkdir(parents=True, exist_ok=True)
        real = bk.tempfile.mkdtemp
        bk.tempfile.mkdtemp = lambda prefix="", dir=None: str(self.stage)
        try:
            return super().restore(name, dest)
        finally:
            bk.tempfile.mkdtemp = real


@pytest.fixture
def world(tmp_path):
    home, bucket = tmp_path / "git", tmp_path / "bucket"
    home.mkdir()
    bucket.mkdir()
    s = {**bk.DEFAULTS, "BACKUP_GIT_HOME": str(home), "BACKUP_STATE": str(tmp_path / "state")}

    def make(name, commits=1):
        bare = home / f"{name}.git"
        git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(bare))
        if commits:
            work = tmp_path / f"w-{name}"
            git(tmp_path, "clone", "--quiet", str(bare), str(work))
            for k, v in (("user.name", "T"), ("user.email", "t@example.com")):
                git(work, "config", k, v)
            for i in range(commits):
                (work / "f.txt").write_text(f"{name} {i}")
                git(work, "add", "f.txt")
                git(work, "commit", "--quiet", "-m", f"c{i}")
            git(work, "push", "--quiet", "origin", "main")
        return bare
    return s, home, bucket, make


def test_a_changed_repo_is_uploaded_once_and_a_quiet_night_uploads_nothing(world):
    s, home, bucket, make = world
    make("alpha"), make("beta")
    b = FakeBackup(s, bucket)
    assert b.run() == {"alpha": "uploaded", "beta": "uploaded"} and sorted(b.uploads) == ["alpha.bundle", "beta.bundle"]
    assert b.run() == {"alpha": "unchanged", "beta": "unchanged"} and len(b.uploads) == 2
    work = home.parent / "w-alpha"
    (work / "f.txt").write_text("more")
    git(work, "commit", "--quiet", "-am", "more")
    git(work, "push", "--quiet", "origin", "main")
    assert b.run()["alpha"] == "uploaded" and b.uploads[-1] == "alpha.bundle" and len(b.uploads) == 3
    assert b.run(force=True) == {"alpha": "uploaded", "beta": "uploaded"}


def test_what_was_uploaded_restores_to_the_same_commits(world, tmp_path):
    s, home, bucket, make = world
    bare = make("gamma", commits=3)
    b = FakeBackup(s, bucket)
    b.run()
    out = b.restore("gamma", str(tmp_path / "restored.git"))
    assert git(tmp_path / "restored.git", "rev-parse", "main") == git(bare, "rev-parse", "main") and "refs/heads/main" in out


def test_a_checksum_the_bucket_disagrees_with_is_a_failure_and_keeps_the_old_fingerprint(world):
    s, home, bucket, make = world
    make("delta")
    b = FakeBackup(s, bucket)
    b.corrupt = True
    with pytest.raises(bk.Failed, match="1 repositories failed"):
        b.run()
    assert "delta" not in b.state["repos"]                                        # not recorded as backed up
    b.corrupt = False
    assert b.run() == {"delta": "uploaded"}                                       # the next night tries again


def test_a_failed_upload_does_not_stop_the_other_repos(world):
    s, home, bucket, make = world
    make("eps"), make("zeta")
    b = FakeBackup(s, bucket)
    real = b.gcloud
    b.gcloud = lambda *a, mounts=(): (_ for _ in ()).throw(bk.Failed("down")) if a[:2] == ("storage", "cp") and "eps" in a[2] else real(*a, mounts=mounts)
    with pytest.raises(bk.Failed):
        b.run()
    assert b.state["last_run"]["results"]["zeta"] == "uploaded" and b.state["last_run"]["failed"] == ["eps.git"]


def test_an_empty_repo_is_noted_and_an_absent_trunk_is_quiet(world, tmp_path):
    s, home, bucket, make = world
    make("empty", commits=0)
    b = FakeBackup(s, bucket)
    assert b.run() == {"empty": "empty"} and b.uploads == []
    gone = FakeBackup({**s, "BACKUP_GIT_HOME": str(tmp_path / "not-mounted")}, bucket)
    assert gone.run() == {} and gone.uploads == []                                # skippy-pc off: nothing to do, not an error
