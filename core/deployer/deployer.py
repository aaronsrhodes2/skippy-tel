#!/usr/bin/env python3
"""SkippyCore's pull deployer: nothing listens for inbound connections; the box fetches main and deploys itself.

A systemd timer runs `deployer.py run` every 2 minutes (skippy-core-deployer.{service,timer}). Each run:

  1. fetch origin/main
  2. the deploy key is commit + CORE_FULL: unchanged since the last good deploy -> nothing to do;
     a key that failed before -> left alone (it is never retried; a new commit or a new CORE_FULL is a new key)
  3. require this box's own CI (core/ci/ci.py, docs/no-github.md) to have passed the commit: its result file says success (pending -> wait
     for the next run; a failed test -> the key is marked bad; CI_LOCAL empty = no CI gate: the simulator)
  4. check out the commit; core/llm/models.py check: missing model files -> REFUSED (the running stack is left as
     it is, the key is not marked bad: it deploys once the files land); a broken manifest -> bad
  5. models.py render into ${CORE_STATE}/llm, docker compose up -d --build --remove-orphans --wait, core/smoke.py
  6. smoke green -> this key is the last good one; smoke red -> the key is marked bad and the last good commit is put
     back (render, compose up, smoke again). A failed build or pull (PyPI, GHCR, the network) is NOT a verdict on the
     commit: the stack is put back the same way, but the key is retried on the next run.

Every run writes ${CORE_STATE}/deployer/status.json (what the home reporter will carry to the portal) and state.json
(last good key, bad keys). Settings: /etc/skippy-core/core.env (KEY=VALUE; the process environment overrides it):
REPO_DIR, REMOTE, BRANCH, CI_LOCAL (the CI state folder; "" = no CI gate: the simulator), CI_NAME, CORE_FULL,
CORE_STATE, MODELS_ROOT, CORE_GPU (0 = no /dev/dri: the simulator), CORE_MATTER (1 = the house's Matter controller too:
core/compose.matter.yml, set up by core/install/matter.sh, which also writes MATTER_ARGS), CORE_TESLA (1 = the Captain's car: TeslaMate
and the car reader, core/compose.tesla.yml, set up by core/install/tesla.sh), CORE_POPUPS3D (1 = the quality 3D
and video pop-ups too: core/popups-3d/compose.yml, a ~20 GB ROCm image, so never in the simulator or CI), CORE_VAULT_ENV, MUSIC_ROOT, SMOKE_WAIT,
LLM_API_KEY (llama-swap's key: the render, the shim and the smoke test all use it; "" = an open llm port).

    deployer.py run        one pass (what the timer runs)
    deployer.py status     print status.json
    deployer.py forget     clear the bad list (after fixing whatever made a key fail, without a new commit)
    deployer.py repair     put the last good deploy back as new: render, every container RECREATED (a hung one too), smoke test.
                           What the portal's "Put everything back" asks for, through skippy-pc's starter over SSH
    deployer.py doctor     one look (skippy-core-doctor.timer, every 3 minutes): does llama-swap write a few words in time? Two
                           failures in a row restart it; still failing after two restarts in an hour, a repair
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))        # core/quiet.py (installed beside the copies in /opt/skippy-core)
try:
    import quiet
except ImportError:                           # the doctor's copy runs without it: it never defers
    quiet = None

DEFAULTS = {
    "REPO_DIR": "/opt/skippy-core/repo", "REMOTE": "origin", "BRANCH": "main",
    "CORE_FULL": "", "CORE_STATE": "/var/lib/skippy-core", "MODELS_ROOT": "/srv/models", "CORE_GPU": "1",
    "CORE_VAULT_ENV": "/etc/skippy-core/vault.env", "MUSIC_ROOT": "/srv/music", "SMOKE_WAIT": "900",
    "OLLAMA_MODEL": "qwen3:8b", "LLM_API_KEY": "", "CORE_MATTER": "0", "MATTER_ARGS": "", "CORE_TESLA": "0",
    "CORE_POPUPS3D": "0", "CI_LOCAL": "", "CI_NAME": "skippy-tel",
    "CD_PY": "/opt/skippy-core/cd/cd.py", "RETRY_S": "1800", "QUIET": "1",
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
        e.update({k: self.s[k] for k in ("MODELS_ROOT", "CORE_STATE", "CORE_VAULT_ENV", "MUSIC_ROOT", "OLLAMA_MODEL",
                                         "LLM_API_KEY", "CORE_MATTER", "MATTER_ARGS", "CORE_TESLA")})
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
        """("success" | "pending" | "failure", detail): this box's own CI (core/ci/ci.py) when CI_LOCAL names its state folder, else
        no gate at all (the simulator)."""
        if not self.s["CI_LOCAL"]:
            return "success", "no CI gate"
        return self.local_ci_state(commit)

    def local_ci_state(self, commit: str) -> tuple[str, str]:
        """core/ci/ci.py's result for this commit (<CI_LOCAL>/<CI_NAME>/results/<sha>.json). Not tested yet, running or an
        error on the box's side = pending; only a failed test is a failure."""
        path = Path(self.s["CI_LOCAL"]) / self.s["CI_NAME"] / "results" / f"{commit}.json"
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "pending", "not tested yet by this box's CI"
        if got.get("state") == "success":
            return "success", f"this box's CI, {got.get('seconds')} s"
        if got.get("state") == "failure":
            bad = [j.get("name") for j in got.get("jobs") or [] if j.get("state") == "failure"]
            return "failure", "this box's CI failed: " + ", ".join(str(b) for b in bad)
        return "pending", f"this box's CI is {got.get('state')}"

    def compose_files(self) -> list[str]:
        files = ["-f", "core/compose.core.yml"]
        if self.s["CORE_GPU"] != "0":
            files += ["-f", "core/compose.gpu.yml"]
        if self.s["CORE_MATTER"] == "1":
            files += ["-f", "core/compose.matter.yml"]
        if self.s["CORE_TESLA"] == "1":
            files += ["-f", "core/compose.tesla.yml"]
        if self.s["CORE_POPUPS3D"] == "1":
            files += ["-f", "core/popups-3d/compose.yml"]
        return files

    def models(self, cmd: str, full: str) -> subprocess.CompletedProcess:
        args = [sys.executable, "core/llm/models.py", cmd, "--full", full, "--root", self.s["MODELS_ROOT"]]
        if cmd == "render":
            args += ["--out", str(Path(self.s["CORE_STATE"]) / "llm")]
        return self.sh(args, full=full, timeout=600, check=False)

    def prepare_vendor(self) -> None:
        """The Mentat build copies .vendor/sigma-ground from the build context: have core/cd/cd.py lay it out from the trunk."""
        cd = Path(self.s["CD_PY"])
        if cd.is_file():
            self.sh([sys.executable, str(cd), "vendor", str(self.repo)], timeout=600)

    def compose_build(self, full: str) -> None:
        self.prepare_vendor()
        self.sh(["docker", "compose", "-p", "skippycore", *self.compose_files(), "build"], full=full, timeout=3600)

    def compose_up(self, full: str, recreate: bool = False) -> None:
        for sub in ("flask-api", "drive-state"):
            (Path(self.s["CORE_STATE"]) / sub).mkdir(parents=True, exist_ok=True)
        self.prepare_vendor()
        self.sh(["docker", "compose", "-p", "skippycore", *self.compose_files(), "up", "-d", "--build", "--remove-orphans",
                 *(["--force-recreate"] if recreate else []), "--wait", "--wait-timeout", "900"],
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
        try:                                                # build first: a build that fails touches nothing that is running
            self.compose_build(full)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise StepFailed("build", (getattr(e, "stderr", "") or str(e))[-500:], bad=False, rollback=False) from None
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

    def defer(self) -> bool:
        if quiet is None or self.s["QUIET"] == "0":
            return False
        return quiet.should_defer(self.dir / "busy-since")

    def retry_wait(self, key: str) -> float:
        """Seconds left before a key whose deploy failed for a reason that may pass (a build, models not there) is tried again."""
        at = self._read("retry.json", {}).get(key)
        if not at:
            return 0
        return max(0.0, float(at) + float(self.s["RETRY_S"]) - time.time())

    def note_retry(self, key: str) -> None:
        self._write("retry.json", {key: time.time()})

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
        wait = self.retry_wait(key)
        if wait:
            return self.status("retry-later", commit=commit, full=full, detail=f"last try failed; next in {int(wait // 60)} min")
        ci, detail = self.ci_state(commit)
        if ci == "pending":
            return self.status("waiting-ci", commit=commit, full=full, detail=detail)
        if ci == "failure":
            self.mark_bad(key, f"ci: {detail}")
            return self.status("ci-failed", commit=commit, full=full, detail=detail)
        if self.defer():
            return self.status("deferred", commit=commit, full=full, detail="the private Skippy is working: holding the deploy")
        self.status("deploying", commit=commit, full=full)
        try:
            results = self.deploy(commit, full)
        except StepFailed as e:
            if e.bad:
                self.mark_bad(key, str(e))
            else:
                self.note_retry(key)
            if e.rollback:                                  # a red smoke, or a build that half-happened
                return self.rollback(commit, full, e)
            if good:                                        # the models aren't there yet: leave the running stack alone
                self.checkout(good["commit"])
            return self.status("refused", commit=commit, full=full, stage=e.stage, detail=e.detail)
        self.mark_good(commit, full)
        return self.status("ok", commit=commit, full=full, detail="deployed", smoke=results)

    # ── repair: the last good deploy, recreated (the portal's one Recovery operation) ──
    def repair(self) -> dict:
        good = self.state()["last_good"]
        full = good["full"] if good else norm_full(self.s["CORE_FULL"])
        try:
            if good:
                self.checkout(good["commit"])
                r = self.models("render", full)
                if r.returncode != 0:
                    raise StepFailed("render", (r.stderr or r.stdout).strip()[-500:], bad=False)
                self.compose_up(full, recreate=True)
            else:
                # No deploy of record (the stack was set up by hand): restart what runs, exactly as it is configured. Rendering
                # from whatever the checkout holds could put an older model set back.
                self.sh(["docker", "compose", "-p", "skippycore", *self.compose_files(), "restart"], full=full, timeout=1800)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return self.status("repair-failed", stage="compose", detail=(getattr(e, "stderr", "") or str(e))[-300:])
        except StepFailed as e:
            return self.status("repair-failed", stage=e.stage, detail=e.detail)
        results = self.smoke(full)
        failed = [x for x in results if not x.get("ok")]
        if failed:
            return self.status("repair-failed", stage="smoke", detail="; ".join(f"{x['check']}: {x.get('detail', '')}"[:200] for x in failed))
        return self.status("ok", commit=good["commit"] if good else None, full=full, detail="repaired", smoke=results)

    # ── the doctor: a real reply from llama-swap, every few minutes ──
    def llm_answers(self, timeout: float = 180) -> tuple[bool, str]:
        """(ok, why): llama-swap's chat slot writes a few words within `timeout` (it may be busy with a long reply: it waits its turn)."""
        key = self.s["LLM_API_KEY"]
        body = json.dumps({"model": "chat", "max_tokens": 8, "messages": [{"role": "user", "content": "Say OK. /no_think"}]}).encode()
        req = urllib.request.Request("http://127.0.0.1:8080/v1/messages", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01",
                                              **({"x-api-key": key, "Authorization": "Bearer " + key} if key else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                got = json.loads(r.read() or b"{}")
        except (OSError, ValueError) as e:
            return False, f"no answer: {type(e).__name__}"
        wrote = any(isinstance(b, dict) and (b.get("text") or b.get("thinking")) for b in got.get("content") or [])
        return wrote, "" if wrote else "it answered with nothing"

    def models_loaded(self) -> bool:
        """llama-swap answers /running (no GPU work) with every resident slot loaded and ready: alive, however slow its replies."""
        key = self.s["LLM_API_KEY"]
        req = urllib.request.Request("http://127.0.0.1:8080/running",
                                     headers={**({"x-api-key": key, "Authorization": "Bearer " + key} if key else {})})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                got = json.loads(r.read() or b"{}")
        except (OSError, ValueError):
            return False
        ready = {m.get("model") for m in got.get("running") or [] if isinstance(m, dict) and m.get("state") == "ready"}
        return all(n in ready for n in ("chat", "coder", "vision"))

    def doctor(self, answers=None, now=None, loaded=None) -> dict:
        now = time.time() if now is None else now
        d = self._read("doctor.json", {"fails": 0, "restarts": [], "repairs": []})
        ok, why = (answers or self.llm_answers)()
        if not ok and (loaded or self.models_loaded)():
            # Slow, not dead: every model is loaded, so a long job is sharing the GPU. Restarting llama-swap now only reloads every
            # model while that job carries on (2026-10-24). Leave it, and look again next time.
            d["last"] = {"at": now, "why": why, "did": "left alone: busy, every model loaded"}
            self._write("doctor.json", d)
            return {"ok": True, "busy": True}
        if ok:
            d["fails"] = 0
            self._write("doctor.json", d)
            return {"ok": True}
        d["fails"] = d.get("fails", 0) + 1
        d["restarts"] = [t for t in d.get("restarts", []) if now - t < 3600]
        d["repairs"] = [t for t in d.get("repairs", []) if now - t < 3600]
        did = "watching"
        if d["fails"] >= 2:
            if len(d["restarts"]) < 2:
                self.sh(["docker", "compose", "-p", "skippycore", *self.compose_files(), "restart", "llm"], full="", timeout=600, check=False)
                d["restarts"].append(now)
                did = "restarted llama-swap"
            elif not d["repairs"]:
                d["repairs"].append(now)
                self._write("doctor.json", d)
                row = self.repair()
                did = f"repaired: {row['state']}"
            else:
                did = "still failing after two restarts and a repair this hour: waiting for the next hour"
        d["last"] = {"at": now, "why": why, "did": did}
        self._write("doctor.json", d)
        self.log(f"doctor: llama-swap {why}; {did}")
        return {"ok": False, "why": why, "did": did}

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
    if cmd not in ("run", "repair", "doctor"):
        print(__doc__)
        return 2
    d.dir.mkdir(parents=True, exist_ok=True)
    with open(d.dir / "lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("a deploy or repair is going: it ends with a smoke test of its own", flush=True)
            return 0
        if cmd == "doctor":
            return 0 if d.doctor()["ok"] else 1
        row = d.repair() if cmd == "repair" else d.run()
    if cmd == "repair":
        print(json.dumps({k: row.get(k) for k in ("state", "detail", "stage", "commit")}), flush=True)
    return 0 if row["state"] in ("ok", "waiting-ci", "known-bad", "refused", "deferred", "retry-later") else 1


if __name__ == "__main__":
    sys.exit(main())
