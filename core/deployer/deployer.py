#!/usr/bin/env python3
"""SkippyCore's pull deployer: nothing listens for inbound connections; the box fetches main and deploys itself.

A systemd timer runs `deployer.py run` every 2 minutes (skippy-core-deployer.{service,timer}). Each run:

  1. fetch origin/main
  2. the deploy key is commit + CORE_FULL: unchanged since the last good deploy -> nothing to do;
     a key that failed before -> left alone (it is never retried; a new commit or a new CORE_FULL is a new key)
  3. require the commit's run of .github/workflows/core.yml to be green (GitHub's Actions API; the repo is public, so
     a classic token with NO scopes only lifts the rate limit; fine-grained tokens can't read checks): pending -> wait
     for the next run; red -> the key is marked bad
  4. check out the commit; core/llm/models.py check: missing model files -> REFUSED (the running stack is left as
     it is, the key is not marked bad: it deploys once the files land); a broken manifest -> bad
  5. models.py render into ${CORE_STATE}/llm, docker compose up -d --build --remove-orphans --wait, core/smoke.py
  6. smoke green -> this key is the last good one; smoke red -> the key is marked bad and the last good commit is put
     back (render, compose up, smoke again). A failed build or pull (PyPI, GHCR, the network) is NOT a verdict on the
     commit: the stack is put back the same way, but the key is retried on the next run.

Every run writes ${CORE_STATE}/deployer/status.json (what the home reporter will carry to the portal) and state.json
(last good key, bad keys). Settings: /etc/skippy-core/core.env (KEY=VALUE; the process environment overrides it):
REPO_DIR, REMOTE, BRANCH, GITHUB_REPO, GITHUB_TOKEN, CI_WORKFLOW ("" = no CI gate: the simulator), CORE_FULL,
CORE_STATE, MODELS_ROOT, CORE_GPU (0 = no /dev/dri: the simulator), CORE_VAULT_ENV, MUSIC_ROOT, SMOKE_WAIT.

    deployer.py run        one pass (what the timer runs)
    deployer.py status     print status.json
    deployer.py forget     clear the bad list (after fixing whatever made a key fail, without a new commit)
Standard library only.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULTS = {
    "REPO_DIR": "/opt/skippy-core/repo", "REMOTE": "origin", "BRANCH": "main",
    "GITHUB_REPO": "aaronsrhodes2/skippy-tel", "GITHUB_TOKEN": "", "CI_WORKFLOW": "core.yml",
    "CORE_FULL": "", "CORE_STATE": "/var/lib/skippy-core", "MODELS_ROOT": "/srv/models", "CORE_GPU": "1",
    "CORE_VAULT_ENV": "/etc/skippy-core/vault.env", "MUSIC_ROOT": "/srv/music", "SMOKE_WAIT": "900",
    "OLLAMA_MODEL": "qwen3:8b",
}
KEEP_BAD = 50


def read_env_file(path: Path) -> dict:
    out = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_settings(env_file: str | None = None) -> dict:
    s = dict(DEFAULTS)
    s.update(read_env_file(Path(env_file or os.environ.get("CORE_ENV", "/etc/skippy-core/core.env"))))
    s.update({k: os.environ[k] for k in DEFAULTS if k in os.environ})
    return s


def norm_full(value: str) -> str:
    names = sorted({n.strip() for n in (value or "").split(",") if n.strip()})
    return "all" if "all" in names else ",".join(names)


class StepFailed(Exception):
    def __init__(self, stage: str, detail: str, bad: bool = True, rollback: bool | None = None):
        super().__init__(f"{stage}: {detail}")
        self.stage, self.detail, self.bad = stage, detail, bad
        self.rollback = bad if rollback is None else rollback       # put the last good stack back?


class Deployer:
    def __init__(self, settings: dict, log=print):
        self.s = settings
        self.repo = Path(settings["REPO_DIR"])
        self.dir = Path(settings["CORE_STATE"]) / "deployer"
        self.log = log

    # ── state ────────────────────────────────────────────────────────────────
    def _read(self, name: str, default):
        try:
            return json.loads((self.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _write(self, name: str, data) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.dir / name)

    def state(self) -> dict:
        return self._read("state.json", {"last_good": None, "bad": {}})

    def status(self, state: str, **fields) -> dict:
        st = self.state()
        row = {"state": state, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "last_good": st["last_good"], **fields}
        self._write("status.json", row)
        self.log(f"{state}: " + ", ".join(f"{k}={v}" for k, v in fields.items() if k != "smoke"))
        return row

    def mark_bad(self, key: str, reason: str) -> None:
        st = self.state()
        st["bad"][key] = {"reason": reason[:500], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        st["bad"] = dict(list(st["bad"].items())[-KEEP_BAD:])
        self._write("state.json", st)

    def mark_good(self, commit: str, full: str) -> None:
        st = self.state()
        st["last_good"] = {"commit": commit, "full": full, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self._write("state.json", st)

    # ── the outside world (tests replace these) ──────────────────────────────
    def env(self, full: str) -> dict:
        e = dict(os.environ)
        gpu = self.s["CORE_GPU"] != "0"
        e.update({k: self.s[k] for k in ("MODELS_ROOT", "CORE_STATE", "CORE_VAULT_ENV", "MUSIC_ROOT", "OLLAMA_MODEL")})
        e["CORE_FULL"] = full
        e.setdefault("GPU_LAYERS", "999" if gpu else "0")
        if gpu:
            for var, dev in (("RENDER_GID", "/dev/dri/renderD128"), ("VIDEO_GID", "/dev/dri/card0"), ("VIDEO_GID", "/dev/dri/card1")):
                try:
                    e.setdefault(var, str(os.stat(dev).st_gid))
                except OSError:
                    pass
        return e

    def sh(self, cmd: list[str], *, full: str = "", timeout: float = 1800, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(cmd, cwd=self.repo, env=self.env(full), capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, cmd, r.stdout, r.stderr)
        return r

    def git(self, *args: str) -> str:
        return self.sh(["git", *args], timeout=300).stdout.strip()

    def fetch(self) -> str:
        self.git("fetch", "--quiet", self.s["REMOTE"], self.s["BRANCH"])
        return self.git("rev-parse", "FETCH_HEAD")

    def checkout(self, commit: str) -> None:
        self.git("checkout", "--quiet", "--force", "--detach", commit)

    def ci_state(self, commit: str) -> tuple[str, str]:
        """("success" | "pending" | "failure", detail) for CI_WORKFLOW's newest push run on this commit."""
        workflow = self.s["CI_WORKFLOW"]
        if not workflow:
            return "success", "no CI gate"
        url = (f"https://api.github.com/repos/{self.s['GITHUB_REPO']}/actions/workflows/{workflow}/runs"
               f"?head_sha={commit}&event=push&per_page=10")
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "skippy-core-deployer"})
        if self.s["GITHUB_TOKEN"]:
            req.add_header("Authorization", f"Bearer {self.s['GITHUB_TOKEN']}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                runs = json.loads(r.read()).get("workflow_runs") or []
        except (OSError, ValueError) as e:
            return "pending", f"GitHub unreachable: {e}"
        if not runs:
            return "pending", f"no {workflow} run yet"
        newest = max(runs, key=lambda c: (c.get("run_attempt") or 0, c.get("created_at") or ""))
        if newest.get("status") != "completed":
            return "pending", f"{workflow} is {newest.get('status')}"
        if newest.get("conclusion") == "success":
            return "success", newest.get("html_url", "")
        return "failure", f"{workflow} concluded {newest.get('conclusion')}: {newest.get('html_url', '')}"

    def compose_files(self) -> list[str]:
        files = ["-f", "core/compose.core.yml"]
        if self.s["CORE_GPU"] != "0":
            files += ["-f", "core/compose.gpu.yml"]
        return files

    def models(self, cmd: str, full: str) -> subprocess.CompletedProcess:
        args = [sys.executable, "core/llm/models.py", cmd, "--full", full, "--root", self.s["MODELS_ROOT"]]
        if cmd == "render":
            args += ["--out", str(Path(self.s["CORE_STATE"]) / "llm")]
        return self.sh(args, full=full, timeout=600, check=False)

    def compose_up(self, full: str) -> None:
        for sub in ("flask-api", "drive-state"):
            (Path(self.s["CORE_STATE"]) / sub).mkdir(parents=True, exist_ok=True)
        self.sh(["docker", "compose", "-p", "skippycore", *self.compose_files(), "up", "-d", "--build", "--remove-orphans",
                 "--wait", "--wait-timeout", "900"],
                full=full, timeout=3600)

    def smoke(self, full: str) -> list[dict]:
        r = self.sh([sys.executable, "core/smoke.py", "--json", "--wait", self.s["SMOKE_WAIT"]], full=full, timeout=7200, check=False)
        try:
            return json.loads(r.stdout)
        except ValueError:
            return [{"check": "smoke", "ok": False, "detail": (r.stderr or r.stdout)[-500:]}]

    # ── one deploy of one key ────────────────────────────────────────────────
    def deploy(self, commit: str, full: str) -> list[dict]:
        self.checkout(commit)
        r = self.models("check", full)
        if r.returncode == 2:
            raise StepFailed("models", (r.stderr or r.stdout).strip()[-500:])
        if r.returncode != 0:
            raise StepFailed("models", (r.stderr or r.stdout).strip()[-500:], bad=False)
        r = self.models("render", full)
        if r.returncode != 0:
            raise StepFailed("render", (r.stderr or r.stdout).strip()[-500:])
        try:
            self.compose_up(full)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise StepFailed("compose", (getattr(e, "stderr", "") or str(e))[-500:], bad=False, rollback=True) from None
        results = self.smoke(full)
        failed = [x for x in results if not x.get("ok")]
        if failed:
            err = StepFailed("smoke", "; ".join(f"{x['check']}: {x.get('detail', '')}"[:200] for x in failed))
            err.results = results
            raise err
        return results

    def run(self) -> dict:
        full = norm_full(self.s["CORE_FULL"])
        try:
            commit = self.fetch()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return self.status("fetch-failed", detail=(getattr(e, "stderr", "") or str(e))[-300:])
        key = f"{commit}|{full}"
        st = self.state()
        good = st["last_good"]
        if good and f"{good['commit']}|{good['full']}" == key:
            return self.status("ok", commit=commit, full=full, detail="up to date")
        if key in st["bad"]:
            return self.status("known-bad", commit=commit, full=full, detail=st["bad"][key]["reason"])
        ci, detail = self.ci_state(commit)
        if ci == "pending":
            return self.status("waiting-ci", commit=commit, full=full, detail=detail)
        if ci == "failure":
            self.mark_bad(key, f"ci: {detail}")
            return self.status("ci-failed", commit=commit, full=full, detail=detail)
        self.status("deploying", commit=commit, full=full)
        try:
            results = self.deploy(commit, full)
        except StepFailed as e:
            if e.bad:
                self.mark_bad(key, str(e))
            if e.rollback:                                  # a red smoke, or a build that half-happened
                return self.rollback(commit, full, e)
            if good:                                        # the models aren't there yet: leave the running stack alone
                self.checkout(good["commit"])
            return self.status("refused", commit=commit, full=full, stage=e.stage, detail=e.detail)
        self.mark_good(commit, full)
        return self.status("ok", commit=commit, full=full, detail="deployed", smoke=results)

    def rollback(self, commit: str, full: str, err: StepFailed) -> dict:
        good = self.state()["last_good"]
        failed = {"commit": commit, "full": full, "stage": err.stage, "detail": err.detail,
                  "smoke": getattr(err, "results", None), "will_retry": not err.bad}
        if not good:
            return self.status("failed", **failed, rollback="nothing to roll back to")
        try:
            results = self.deploy(good["commit"], good["full"])
        except StepFailed as e2:
            return self.status("failed", **failed, rollback=f"rollback to {good['commit'][:10]} failed too: {e2}")
        return self.status("rolled-back", **failed, rollback=f"back on {good['commit'][:10]}", rollback_smoke=results)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "run"
    s = load_settings()
    d = Deployer(s, log=lambda m: print(f"{time.strftime('%H:%M:%S')} {m}", flush=True))
    if cmd == "status":
        print(json.dumps(d._read("status.json", {}), indent=2))
        return 0
    if cmd == "forget":
        st = d.state()
        st["bad"] = {}
        d._write("state.json", st)
        print("bad list cleared")
        return 0
    if cmd != "run":
        print(__doc__)
        return 2
    d.dir.mkdir(parents=True, exist_ok=True)
    with open(d.dir / "lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another run is going", flush=True)
            return 0
        row = d.run()
    return 0 if row["state"] in ("ok", "waiting-ci", "known-bad", "refused") else 1


if __name__ == "__main__":
    sys.exit(main())
