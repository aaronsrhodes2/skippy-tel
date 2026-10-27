#!/usr/bin/env python3
"""The house's git, off site (docs/no-github.md): a nightly `git bundle` of every bare repo in the trunk (/mnt/pc/trunk/git, skippy-pc's
D:\\trunk\\git) into gs://skippy-tel-git-backup, run from skippy-core and signed in with its own keyless identity (core/cd/oidc.py).

For each <name>.git:
  1. fingerprint its refs (every branch, tag and GitHub pull ref, with the commit each points at); the same fingerprint as the last
     good upload = nothing to do (a quiet night uploads nothing);
  2. `git bundle create <name>.bundle --all`, then `git bundle verify` (a bundle is only a backup if git can read it back);
  3. upload it as gs://<bucket>/<name>.bundle and compare the bucket's MD5 with the file's: the upload is kept only when they agree.
The bucket is versioned (infra/bootstrap), so a changed repo's older bundles stay as non-current versions for 90 days: a bad night
never overwrites a good one. An empty repository (no commits) can't be bundled and is noted, not failed.

If skippy-pc is off (the trunk isn't mounted), the run says so and exits 0: there is nothing to back up until it is back.

    backup.py run                  one pass (the timer)
    backup.py run --force          bundle and upload everything, whatever the fingerprints say
    backup.py status               state.json: when each repo was last uploaded
    backup.py restore <name> <dir> clone <name> back from the bucket's bundle into <dir> (the proof it works)

Settings (/etc/skippy-core/core.env, the environment overrides): BACKUP_GIT_HOME (/mnt/pc/trunk/git), BACKUP_BUCKET
(skippy-tel-git-backup), BACKUP_STATE (/var/lib/skippy-core/backup), CD_GCLOUD_IMAGE. Standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent / "cd", Path("/opt/skippy-core/cd")):        # oidc.py: next to the repo's copy, or the installed one
    if (candidate / "oidc.py").is_file():
        sys.path.insert(0, str(candidate))
        break
import oidc  # noqa: E402

ENV_FILE = "/etc/skippy-core/core.env"
DEFAULTS = {"BACKUP_GIT_HOME": "/mnt/pc/trunk/git", "BACKUP_BUCKET": "skippy-tel-git-backup", "BACKUP_STATE": "/var/lib/skippy-core/backup",
            "BACKUP_PROJECT": "skippy-tel", "CD_GCLOUD_IMAGE": "gcr.io/google.com/cloudsdktool/google-cloud-cli:stable"}
OIDC_DIR = str(oidc.TOKEN_FILE.parent)
SAFE = ["-c", "safe.directory=*"]                                           # the share is owned by another uid than root


class Failed(Exception):
    pass


def settings() -> dict:
    s = dict(DEFAULTS)
    try:
        for line in Path(ENV_FILE).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in DEFAULTS:
                    s[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    s.update({k: os.environ[k] for k in DEFAULTS if k in os.environ})
    return s


def md5_b64(path: Path) -> str:
    h = hashlib.md5()                                                        # noqa: S324 — it is the bucket's own checksum, not security
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return base64.b64encode(h.digest()).decode()


class Backup:
    def __init__(self, s: dict | None = None, log=print):
        self.s = s or settings()
        self.home = Path(self.s["BACKUP_GIT_HOME"])
        self.state_dir = Path(self.s["BACKUP_STATE"])
        self.say = log
        self.state = self.load()

    def load(self) -> dict:
        try:
            return json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"repos": {}}

    def save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_dir / "state.tmp"
        tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        tmp.replace(self.state_dir / "state.json")

    # ── shell ───────────────────────────────────────────────────────────────────
    def sh(self, cmd: list[str], timeout: float = 1800) -> str:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if r.returncode != 0:
            raise Failed(f"{' '.join(cmd[:4])} failed: {(r.stderr or r.stdout).strip()[-300:]}")
        return r.stdout

    def git(self, repo: Path, *args: str) -> str:
        return self.sh(["git", *SAFE, "-C", str(repo), *args], timeout=900)

    def gcloud(self, *args: str, mounts: tuple[str, ...] = ()) -> str:
        """gcloud in its own container, signed in as skippy-deployer through this box's token (no key)."""
        vols = ["-v", f"{OIDC_DIR}:{OIDC_DIR}:ro"]
        for m in mounts:
            vols += ["-v", m]
        return self.sh(["docker", "run", "--rm", *vols, "-e", f"CLOUDSDK_CORE_PROJECT={self.s['BACKUP_PROJECT']}",
                        "-e", "CLOUDSDK_CORE_DISABLE_PROMPTS=1", self.s["CD_GCLOUD_IMAGE"], "sh", "-c",
                        f'gcloud auth login --cred-file={oidc.CRED_FILE} --quiet >/dev/null 2>&1 && exec gcloud "$@"', "gcloud", *args])

    # ── one repo ────────────────────────────────────────────────────────────────
    def repos(self) -> list[Path]:
        return sorted(p for p in self.home.glob("*.git") if p.is_dir())

    def fingerprint(self, repo: Path) -> str:
        refs = self.git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
        return hashlib.sha256(refs.encode()).hexdigest()[:20] if refs.strip() else ""

    def back_up(self, repo: Path, work: Path, force: bool = False) -> str:
        """"uploaded" | "unchanged" | "empty"; raises Failed."""
        name = repo.name[:-4]
        fp = self.fingerprint(repo)
        if not fp:
            self.state["repos"][name] = {"fingerprint": "", "empty": True, "checked": time.time()}
            return "empty"
        if not force and self.state["repos"].get(name, {}).get("fingerprint") == fp:
            return "unchanged"
        bundle = work / f"{name}.bundle"
        self.git(repo, "bundle", "create", str(bundle), "--all")
        self.git(repo, "bundle", "verify", str(bundle))
        want = md5_b64(bundle)
        object_ = f"gs://{self.s['BACKUP_BUCKET']}/{name}.bundle"
        self.gcloud("storage", "cp", f"/stage/{name}.bundle", object_, mounts=(f"{work}:/stage:ro",))
        got = self.gcloud("storage", "objects", "describe", object_, "--format=value(md5_hash)").strip()
        if got != want:
            raise Failed(f"{name}: the bucket's checksum ({got}) doesn't match the bundle's ({want})")
        self.state["repos"][name] = {"fingerprint": fp, "bytes": bundle.stat().st_size, "uploaded": time.time(), "md5": want}
        return "uploaded"

    def run(self, force: bool = False) -> dict:
        if not self.home.is_dir() or not any(self.home.glob("*.git")):
            self.say(f"backup: {self.home} isn't there or is empty (skippy-pc off?): nothing to back up")
            return {}
        oidc.token()
        results, failed = {}, []
        work = Path(tempfile.mkdtemp(prefix="backup-", dir=str(self.state_dir) if self.state_dir.is_dir() else None))
        try:
            for repo in self.repos():
                try:
                    results[repo.name[:-4]] = self.back_up(repo, work, force)
                except Failed as e:
                    results[repo.name[:-4]] = f"FAILED: {e}"
                    failed.append(repo.name)
                    self.say(f"backup: {repo.name}: {e}")
                finally:
                    (work / f"{repo.name[:-4]}.bundle").unlink(missing_ok=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.state["last_run"] = {"at": time.time(), "results": results, "failed": failed}
        self.save()
        did = ", ".join(f"{k} {v}" for k, v in results.items() if v != "unchanged")
        self.say(f"backup: {did}" if did else "backup: nothing changed")
        if failed:
            raise Failed(f"{len(failed)} repositories failed: {', '.join(failed)}")
        return results

    def restore(self, name: str, dest: str) -> str:
        """The proof: clone a repository back from the bucket's bundle."""
        oidc.token()
        work = Path(tempfile.mkdtemp(prefix="restore-"))
        try:
            self.gcloud("storage", "cp", f"gs://{self.s['BACKUP_BUCKET']}/{name}.bundle", "/stage/", mounts=(f"{work}:/stage",))
            bundle = work / f"{name}.bundle"
            self.sh(["git", *SAFE, "clone", "--quiet", "--mirror", str(bundle), dest])
            return self.sh(["git", *SAFE, "-C", dest, "for-each-ref", "--count=3", "--format=%(refname) %(objectname:short)"]).strip()
        finally:
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str]) -> int:
    b = Backup()
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        try:
            b.run(force="--force" in argv)
        except Failed as e:
            print(f"backup: {e}")
            return 1
        return 0
    if cmd == "status":
        print(json.dumps(b.state, indent=1))
        return 0
    if cmd == "restore" and len(argv) == 3:
        print(b.restore(argv[1], argv[2]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
