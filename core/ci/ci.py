#!/usr/bin/env python3
"""SkippyCore's CI (docs/no-github.md, phase 2): the tests GitHub Actions ran (run-tests.yml, test.yml's shell syntax), run on this box in
plain Docker against the house's own git. Nothing listens for connections; a systemd timer runs `ci.py run` every minute.

Each run, for each repo in CI_REPOS (name=path, the bare repo on skippy-pc through the data trunk: /mnt/pc/trunk/git/<name>.git):
  1. fetch its main into a mirror on this box's own disk (so a test never reads across the network);
  2. if main's newest commit has no result yet, test it: a scratch checkout, then every job of the repo's plan in its own
     container, side by side. Each job's image is built from its requirement files and named by their hash, so dependencies are
     installed once per change, not per commit. Tests run with no network (--network none: loopback only), capped in CPU and
     memory so the models keep the box;
  3. write <state>/<name>/results/<sha>.json: {"state": "success" | "failure" | "error", "jobs": [...], "at", "finished_at", "seconds"}
     (epoch seconds; each job carries its own started_at and finished_at), with each
     job's log beside it, and latest.json. A job that fails is a failure (the commit's fault); a build or Docker that fails is an
     error (the box's fault): it is tried again next run, at most three times, and an error never blocks forever as a failure would.

The deployer reads these results as its gate (deployer.py ci_state, CI_LOCAL): it deploys a commit only when its result is success.

    ci.py run                 one pass (what the timer runs)
    ci.py status [name]       the latest result, as JSON
    ci.py test <name> <sha>   test one commit now, whatever its result says (writes the result)

Settings: /etc/skippy-core/core.env (KEY=VALUE; the environment overrides it): CI_REPOS (default
"skippy-tel=/mnt/pc/trunk/git/skippy-tel.git"), CI_STATE (/var/lib/skippy-core/ci), CI_BRANCH (main), CI_CPUS (8), CI_MEMORY (16g),
CI_TIMEOUT_S (1500). Standard library only.
"""
from __future__ import annotations

import fcntl
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
sys.path.insert(0, str(HERE.parent))
import quiet  # noqa: E402

ENV_FILE = "/etc/skippy-core/core.env"
DEFAULTS = {"CI_REPOS": "skippy-tel=/mnt/pc/trunk/git/skippy-tel.git", "CI_STATE": "/var/lib/skippy-core/ci", "CI_BRANCH": "main",
            "CI_CPUS": "8", "CI_MEMORY": "16g", "CI_TIMEOUT_S": "1500"}
MAX_ERRORS = 3
LOG_TAIL = 4000

# Each repo's jobs: what GitHub's run-tests.yml and test.yml did for it. `reqs` build the job's image (python:3.12-slim + these
# requirement files); `cmd` runs in the checkout. {cpus} is CI_CPUS: pytest's workers match the container's CPU cap ("auto" would
# count the whole box and oversubscribe it, and the timing-sensitive tests then fail). A repo without a plan gets only the shell-syntax check.
PLANS = {
    "skippy-tel": [
        {"name": "suite", "reqs": ["portal/requirements.txt", "tools/requirements-ci.txt"],
         "cmd": "python -m pytest portal/tests memory/tests tools/tests services/home-reporter/tests services/claude-bridge/tests "
                "services/wa-trickle/tests mentat/tests core/tests -q -n {cpus} --dist loadfile -p no:cacheprovider"},
        {"name": "flask-api", "reqs": ["services/flask-api/requirements.txt", "tools/requirements-ci.txt"],
         "cmd": "python -m pytest services/flask-api/tests -q -n {cpus} --dist loadfile -p no:cacheprovider"},
        # The services that build as their own images and keep their own `app`/`gate` modules, so each runs in its own pytest session (they never
        # shared the portal's). They were not in CI at all until 2026-10-27, when the readiness contract changed every one of them.
        {"name": "services", "reqs": ["services/music-gate/requirements.txt", "services/video-gate/requirements.txt", "services/voice-gate/requirements.txt",
                                      "tools/requirements-ci.txt"],
         "cmd": "cd services/music-gate && python -m pytest tests -q -p no:cacheprovider && cd ../video-gate && python -m pytest tests -q -p no:cacheprovider "
                "&& cd ../voice-gate && python -m pytest tests -q -p no:cacheprovider && cd ../tts-studio && python -m pytest tests -q -p no:cacheprovider "
                "&& cd ../purple-tools && python -m pytest tests -q -p no:cacheprovider"},
        {"name": "shell-syntax", "reqs": [],
         "cmd": "bash -n infra/gcp-games/*.sh infra/bootstrap/*.sh scripts/games.sh tools/*.sh"},
    ],
}


def settings(env_file: str = ENV_FILE) -> dict:
    s = dict(DEFAULTS)
    try:
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in DEFAULTS:
                    s[k.strip()] = v.strip()
    except OSError:
        pass
    s.update({k: os.environ[k] for k in DEFAULTS if k in os.environ})
    return s


def repos(s: dict) -> dict[str, str]:
    out = {}
    for part in s["CI_REPOS"].split(","):
        name, _, path = part.strip().partition("=")
        if name and path:
            out[name.strip()] = path.strip()
    return out


class CI:
    def __init__(self, s: dict | None = None, run=subprocess.run, clock=time.time, log=print, pause=time.sleep):
        self.s = s or settings()
        self.state = Path(self.s["CI_STATE"])
        self._run, self.clock, self.log, self.pause = run, clock, log, pause

    # ── shell ───────────────────────────────────────────────────────────────────
    def sh(self, cmd: list[str], cwd: str | Path | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
        return self._run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout)

    def git(self, *args: str, cwd: str | Path | None = None) -> str:
        r = self.sh(["git", *args], cwd=cwd, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout.strip()

    # ── results ─────────────────────────────────────────────────────────────────
    def results(self, name: str) -> Path:
        return self.state / name / "results"

    def result(self, name: str, sha: str) -> dict | None:
        try:
            return json.loads((self.results(name) / f"{sha}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def write(self, name: str, sha: str, result: dict) -> None:
        folder = self.results(name)
        folder.mkdir(parents=True, exist_ok=True)
        for path in (folder / f"{sha}.json", self.state / name / "latest.json"):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"sha": sha, **result}, indent=1), encoding="utf-8")
            tmp.replace(path)

    # ── one repo ────────────────────────────────────────────────────────────────
    def mirror(self, name: str, source: str) -> Path:
        mirror = self.state / name / "mirror.git"
        if not mirror.is_dir():
            mirror.parent.mkdir(parents=True, exist_ok=True)
            self.git("init", "--bare", "--quiet", str(mirror))
        branch = self.s["CI_BRANCH"]
        self.git("fetch", "--quiet", "--force", source, f"+refs/heads/{branch}:refs/heads/{branch}", cwd=mirror)
        return mirror

    def due(self, name: str, sha: str) -> bool:
        got = self.result(name, sha)
        if got is None:
            return True
        return got.get("state") == "error" and int(got.get("errors") or 1) < MAX_ERRORS

    def check(self, name: str, source: str, deferred: bool = False) -> dict | None:
        """Fetch; test main's newest commit if it is due. The result, or None when there was nothing to do.
        `deferred`: the quiet gate is closed (the private Skippy is working); only a commit that changes nothing but Mentat
        relative to the last commit tested here goes ahead (core/quiet.py, mentat_only)."""
        mirror = self.mirror(name, source)
        sha = self.git("rev-parse", self.s["CI_BRANCH"], cwd=mirror)
        if not self.due(name, sha):
            return None
        if deferred:
            try:
                last = json.loads((self.state / name / "latest.json").read_text(encoding="utf-8")).get("sha")
            except (OSError, ValueError):
                last = None
            if not quiet.git_mentat_only(mirror, last, sha):
                return None
        return self.test(name, sha, mirror)

    def test(self, name: str, sha: str, mirror: Path | None = None) -> dict:
        mirror = mirror or self.state / name / "mirror.git"
        before = self.result(name, sha) or {}
        started = self.clock()
        self.write(name, sha, {"state": "running", "at": started, "jobs": []})
        self.log(f"ci: {name} {sha[:7]} testing")
        work = Path(tempfile.mkdtemp(prefix=f"ci-{name}-", dir=str(self.state)))
        try:
            self.git("clone", "--quiet", "--no-checkout", str(mirror), str(work / "src"))     # a real .git: some tests read git
            self.git("checkout", "--quiet", "--detach", sha, cwd=work / "src")
            work = work / "src"
            jobs = self.run_jobs(name, sha, work)
        except Exception as e:                            # noqa: BLE001 — the box's own trouble: an error, retried
            jobs = [{"name": "setup", "state": "error", "why": str(e)[:500]}]
        finally:
            shutil.rmtree(work.parent if work.name == "src" else work, ignore_errors=True)
        states = {j["state"] for j in jobs}
        state = "error" if "error" in states else "failure" if "failure" in states else "success"
        finished = self.clock()
        result = {"state": state, "at": started, "finished_at": finished, "seconds": round(finished - started), "jobs": jobs}
        if state == "error":
            result["errors"] = int(before.get("errors") or 0) + 1
        self.write(name, sha, result)
        self.log(f"ci: {name} {sha[:7]} {state} in {result['seconds']} s ({', '.join(j['name'] + ' ' + j['state'] for j in jobs)})")
        return result

    def run_jobs(self, name: str, sha: str, work: Path) -> list[dict]:
        plan = PLANS.get(name) or [{"name": "shell-syntax", "reqs": [], "cmd": "true"}]
        started = []
        for job in plan:                                  # side by side: each in its own container
            image = self.image(job, work)
            log = self.results(name) / f"{sha}.{job['name']}.log"
            proc = subprocess.Popen(
                ["docker", "run", "--rm", "--network", "none", f"--cpus={self.s['CI_CPUS']}", f"--memory={self.s['CI_MEMORY']}",
                 "-v", f"{work}:/src", "-w", "/src", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
                 "--tmpfs", "/tmp:exec", "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory", "-e", "GIT_CONFIG_VALUE_0=*",
                 image, "bash", "-c", job["cmd"].replace("{cpus}", self.s["CI_CPUS"])],
                stdout=open(log, "w", encoding="utf-8"), stderr=subprocess.STDOUT)
            started.append((job, proc, log, self.clock()))
        # Polled, not waited on in order: each job's finished_at is when IT ended, not when the one before it did
        # (tools/loop_timer.py draws the timeline from these stamps).
        ended: dict[int, tuple[int | None, float]] = {}
        while len(ended) < len(started):
            for i, (job, proc, log, t0) in enumerate(started):
                if i in ended:
                    continue
                code = proc.poll()
                if code is not None:
                    ended[i] = (code, self.clock())
                elif self.clock() - t0 > int(self.s["CI_TIMEOUT_S"]):
                    proc.kill()
                    ended[i] = (None, self.clock())
            if len(ended) < len(started):
                self.pause(1)
        out = []
        for i, (job, proc, log, t0) in enumerate(started):
            code, t1 = ended[i]
            if code is None:
                state, why = "failure", f"timed out after {self.s['CI_TIMEOUT_S']} s"
            else:
                state, why = ("success", "") if code == 0 else ("failure", f"exit {code}")
            tail = Path(log).read_text(encoding="utf-8", errors="replace")[-LOG_TAIL:] if Path(log).is_file() else ""
            out.append({"name": job["name"], "state": state, "why": why, "seconds": round(t1 - t0), "started_at": t0,
                        "finished_at": t1, "tail": tail})
        return out

    def image(self, job: dict, work: Path) -> str:
        """python:3.12-slim with the job's requirements, named by their content: built once per change, then reused."""
        digest = hashlib.sha256(b"git nodejs curl")              # the base layer below: change it and every image is rebuilt
        for req in job["reqs"]:
            digest.update(req.encode() + b"\0" + (work / req).read_bytes())
        tag = f"skippy-ci:{digest.hexdigest()[:16]}"
        if self.sh(["docker", "image", "inspect", tag]).returncode == 0:
            return tag
        ctx = Path(tempfile.mkdtemp(prefix="ci-image-", dir=str(self.state)))
        try:
            lines = ["FROM python:3.12-slim", "RUN apt-get update -q && apt-get install -yq --no-install-recommends git nodejs curl && rm -rf /var/lib/apt/lists/*"]
            for i, req in enumerate(job["reqs"]):
                shutil.copy(work / req, ctx / f"req{i}.txt")
                lines.append(f"COPY req{i}.txt /tmp/req{i}.txt")
            if job["reqs"]:
                lines.append("RUN pip install --no-cache-dir " + " ".join(f"-r /tmp/req{i}.txt" for i in range(len(job["reqs"]))))
            (ctx / "Dockerfile").write_text("\n".join(lines) + "\n", encoding="utf-8")
            built = self.sh(["docker", "build", "-q", "-t", tag, str(ctx)], timeout=1800)
            if built.returncode != 0:
                raise RuntimeError(f"building {tag}: {(built.stderr or built.stdout).strip()[-400:]}")
        finally:
            shutil.rmtree(ctx, ignore_errors=True)
        return tag

    def run(self) -> list[dict]:
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / "ci.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return []                                 # a run is still going: this tick has nothing to add
            done = []
            deferred = quiet.should_defer(self.state / "busy-since")     # the private Skippy is working: tests wait (they use the CPUs) ...
            for name, source in repos(self.s).items():                   # ... except a Mentat-only commit (check())
                try:
                    got = self.check(name, source, deferred)
                except Exception as e:                    # noqa: BLE001 — skippy-pc away (the trunk), a bad repo: the next tick tries again
                    self.log(f"ci: {name}: {e}")
                    continue
                if got:
                    done.append({"repo": name, **got})
            return done


def gate(state_dir: str, name: str, sha: str) -> tuple[str, str]:
    """For the deployer: ("success" | "pending" | "failure", detail) for one commit, from the results above."""
    try:
        got = json.loads((Path(state_dir) / name / "results" / f"{sha}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "pending", "not tested yet"
    state = got.get("state")
    if state == "success":
        return "success", f"tested in {got.get('seconds')} s"
    if state == "failure":
        bad = [j["name"] for j in got.get("jobs") or [] if j.get("state") == "failure"]
        return "failure", "failed: " + ", ".join(bad)
    return "pending", f"CI is {state}"


def main(argv: list[str]) -> int:
    ci = CI()
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        ci.run()
        return 0
    if cmd == "status":
        name = argv[1] if len(argv) > 1 else "skippy-tel"
        try:
            print((ci.state / name / "latest.json").read_text(encoding="utf-8"))
        except OSError:
            print("{}")
        return 0
    if cmd == "test" and len(argv) == 3:
        ci.mirror(argv[1], repos(ci.s)[argv[1]])
        return 0 if ci.test(argv[1], argv[2])["state"] == "success" else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
