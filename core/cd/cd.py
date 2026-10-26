#!/usr/bin/env python3
"""skippy-core deploys the cloud (docs/no-github.md, phase 3): what .github/workflows/deploy.yml and rollback.yml did, on this box,
in plain Docker, signed in to Google with no key (core/cd/oidc.py). A systemd timer runs `cd.py run` every minute.

Each run:
  1. report the recent runs to the portal (POST /home/deploys, the home token) and take back what Recovery asked: deploy main
     again, roll the portal back to a revision, re-run a run, cancel (nothing to cancel: one run at a time, start to finish);
  2. if main's newest commit passed this box's CI (core/ci) and isn't deployed yet, deploy it, as one run with these jobs:
       test          CI's result (already green, or nothing happens)
       portal-image  docker build portal/Dockerfile (pins from core/cd/deploy.json) and push it to Artifact Registry
       portal        copy the vault's Skippy Core secrets into Secret Manager, note the serving revision, gcloud run deploy (with
                     DEPLOYS_FROM=house, so the portal reads its deploys from here), send it all traffic, smoke-test it; on any
                     failure, ALL traffic goes back to the noted revision
       mentat        only when mentat/, .dockerignore or deploy.json changed since Mentat's last good deploy: build, push, deploy,
                     smoke (the same checks deploy.yml made)
       infra         only when infra/gcp-games/ or deploy.json changed since its last good apply: terraform apply
  3. report again after every job, so the ship card follows it live.

State: /var/lib/skippy-core/cd/state.json (runs, what is deployed where); each run's log in runs/<id>.log.

    cd.py run                one pass (the timer)
    cd.py deploy [sha]       deploy now (main's newest green commit, or that one), whatever state.json says
    cd.py rollback [rev]     send the portal's traffic to that revision (blank: the one before the serving one)
    cd.py status             state.json

Settings (/etc/skippy-core/core.env, the environment overrides): CD_STATE, CI_LOCAL, CI_NAME, CD_VAULT (/etc/skippy-core/vault.env:
SKIPPY_HOME_TOKEN, SKIPPY_CORE_URL, SKIPPY_CORE_API_KEY), CD_PORTAL (the portal's run.app address), CD_GCLOUD_IMAGE,
CD_TERRAFORM_IMAGE. Standard library only.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import oidc  # noqa: E402

ENV_FILE = "/etc/skippy-core/core.env"
DEFAULTS = {"CD_STATE": "/var/lib/skippy-core/cd", "CI_LOCAL": "/var/lib/skippy-core/ci", "CI_NAME": "skippy-tel", "CI_BRANCH": "main",
            "CD_VAULT": "/etc/skippy-core/vault.env", "CD_PORTAL": "https://skippy-portal-1015050363686.us-west1.run.app",
            "CD_GCLOUD_IMAGE": "gcr.io/google.com/cloudsdktool/google-cloud-cli:stable",
            "CD_TERRAFORM_IMAGE": "hashicorp/terraform:1.9"}
OIDC_DIR = str(oidc.TOKEN_FILE.parent)
KEEP_RUNS = 30


def read_env(path: str) -> dict:
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return out


def settings() -> dict:
    s = dict(DEFAULTS)
    s.update({k: v for k, v in read_env(ENV_FILE).items() if k in DEFAULTS})
    s.update({k: os.environ[k] for k in DEFAULTS if k in os.environ})
    return s


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Failed(Exception):
    """A job failed: the message says why, plainly."""


class CD:
    def __init__(self, s: dict | None = None, log=print):
        self.s = s or settings()
        self.home = Path(self.s["CD_STATE"])
        self.vault = read_env(self.s["CD_VAULT"])
        self.say = log
        self.state = self.load()
        self.logfile = None

    # ── state ───────────────────────────────────────────────────────────────────
    def load(self) -> dict:
        try:
            return json.loads((self.home / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"runs": [], "next_id": 1, "deployed": {}, "done_requests": []}

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.state["runs"] = self.state["runs"][:KEEP_RUNS]
        tmp = self.home / "state.tmp"
        tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        tmp.replace(self.home / "state.json")

    # ── the portal ──────────────────────────────────────────────────────────────
    def report(self) -> list[dict]:
        """Send the recent runs; get back Recovery's requests. Never fails a deploy: the portal may be the thing that's down."""
        token = self.vault.get("SKIPPY_HOME_TOKEN", "")
        if not token:
            return []
        body = json.dumps({"runs": self.state["runs"], "done": self.state.get("done_requests", [])}).encode()
        req = urllib.request.Request(f"{self.s['CD_PORTAL']}/home/deploys", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return (json.loads(r.read() or b"{}") or {}).get("requests") or []
        except (OSError, ValueError) as e:
            self.say(f"cd: couldn't report to the portal ({e})")
            return []

    # ── shell ───────────────────────────────────────────────────────────────────
    def log(self, text: str) -> None:
        if self.logfile:
            with open(self.logfile, "a", encoding="utf-8") as f:
                f.write(text.rstrip("\n") + "\n")

    def sh(self, cmd: list[str], cwd=None, timeout: float = 1800, input: str | None = None, quiet: bool = False) -> str:
        self.log(f"$ {' '.join(c if len(c) < 200 else c[:200] + '…' for c in cmd)}")
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, input=input)
        if not quiet:
            self.log((r.stdout or "")[-6000:] + (r.stderr or "")[-6000:])
        if r.returncode != 0:
            raise Failed(f"{cmd[0]} {' '.join(cmd[1:3])} failed: {((r.stderr or r.stdout) or '').strip()[-400:]}")
        return r.stdout

    def gcloud(self, *args: str, cwd=None, timeout: float = 900, quiet: bool = False) -> str:
        """gcloud in its own container, signed in as skippy-deployer through skippy-core's own token (no key)."""
        mounts = ["-v", f"{OIDC_DIR}:{OIDC_DIR}:ro"] + (["-v", f"{cwd}:/src:ro", "-w", "/src"] if cwd else [])
        # (a fresh container each time: it signs in with the token's credential config, then runs the one command)
        return self.sh(["docker", "run", "--rm", *mounts, "-e", f"CLOUDSDK_CORE_PROJECT={self.cfg['project']}",
                        "-e", "CLOUDSDK_CORE_DISABLE_PROMPTS=1", self.s["CD_GCLOUD_IMAGE"], "sh", "-c",
                        f'gcloud auth login --cred-file={oidc.CRED_FILE} --quiet >/dev/null 2>&1 && exec gcloud "$@"', "gcloud", *args],
                       timeout=timeout, quiet=quiet)

    # ── a run ───────────────────────────────────────────────────────────────────
    def green(self) -> str | None:
        """main's newest commit, when this box's CI passed it."""
        mirror = Path(self.s["CI_LOCAL"]) / self.s["CI_NAME"] / "mirror.git"
        try:
            sha = subprocess.run(["git", "rev-parse", self.s["CI_BRANCH"]], cwd=mirror, capture_output=True, text=True,
                                 check=True).stdout.strip()
            result = json.loads((Path(self.s["CI_LOCAL"]) / self.s["CI_NAME"] / "results" / f"{sha}.json").read_text())
        except (OSError, ValueError, subprocess.CalledProcessError):
            return None
        return sha if result.get("state") == "success" else None

    def new_run(self, sha: str, title: str) -> dict:
        run = {"id": self.state["next_id"], "head_sha": sha, "status": "in_progress", "conclusion": "", "created_at": now_iso(),
               "updated_at": now_iso(), "title": title, "jobs": [{"name": "test", "status": "completed", "conclusion": "success"}]}
        self.state["next_id"] += 1
        self.state["runs"].insert(0, run)
        self.save()
        return run

    def job(self, run: dict, name: str, fn, *args) -> bool:
        """Run one job, keeping the run's record (and the portal) up to date. True when it passed."""
        entry = {"name": name, "status": "in_progress", "conclusion": ""}
        run["jobs"].append(entry)
        run["updated_at"] = now_iso()
        self.save()
        self.report()
        self.log(f"── {name}")
        try:
            skipped = fn(*args) == "skipped"
            entry.update(status="completed", conclusion="skipped" if skipped else "success")
            return True
        except Exception as e:                            # noqa: BLE001 — every failure is said, never raised past the run
            self.log(traceback.format_exc())
            entry.update(status="completed", conclusion="failure", why=str(e)[:300])
            self.say(f"cd: {name} failed: {e}")
            return False
        finally:
            run["updated_at"] = now_iso()
            self.save()

    def deploy(self, sha: str, why: str = "push") -> dict:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "runs").mkdir(exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="cd-", dir=str(self.home)))
        try:
            mirror = Path(self.s["CI_LOCAL"]) / self.s["CI_NAME"] / "mirror.git"
            subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(mirror), str(work / "src")], check=True)
            subprocess.run(["git", "checkout", "--quiet", "--detach", sha], cwd=work / "src", check=True)
            src = work / "src"
            subject = subprocess.run(["git", "log", "-1", "--format=%s", sha], cwd=src, capture_output=True, text=True).stdout.strip()
            self.cfg = json.loads((src / "core" / "cd" / "deploy.json").read_text(encoding="utf-8"))
            run = self.new_run(sha, subject or why)
            self.logfile = self.home / "runs" / f"{run['id']}.log"
            self.say(f"cd: run {run['id']}: deploying {sha[:7]} ({why})")
            oidc.token()
            ok = self.job(run, "portal-image", self.portal_image, src, sha) and self.job(run, "portal", self.portal, src, sha)
            if ok:
                self.state["deployed"]["portal"] = sha
            if self.changed(src, "mentat", sha):
                if self.job(run, "mentat", self.mentat, src, sha):
                    self.state["deployed"]["mentat"] = sha
                else:
                    ok = False
            if self.changed(src, "infra", sha):
                if self.job(run, "infra", self.infra, src):
                    self.state["deployed"]["infra"] = sha
                else:
                    ok = False
            run.update(status="completed", conclusion="success" if ok else "failure", updated_at=now_iso())
            self.state["delivered"] = sha                 # tried: a failed commit is not retried by the timer (a new push, or Recovery's redeploy)
            self.save()
            self.report()
            self.say(f"cd: run {run['id']}: {run['conclusion']}")
            return run
        finally:
            self.logfile = None
            shutil.rmtree(work, ignore_errors=True)

    def changed(self, src: Path, part: str, sha: str) -> bool:
        """Did anything `part` is built from change since its last good deploy? Any doubt = yes."""
        base = self.state["deployed"].get(part)
        if not base:
            return True
        r = subprocess.run(["git", "diff", "--name-only", base, sha], cwd=src, capture_output=True, text=True)
        if r.returncode != 0:
            return True
        inputs = tuple(self.cfg[part]["inputs"])
        return any(p.startswith(inputs) for p in r.stdout.split())

    # ── the jobs ────────────────────────────────────────────────────────────────
    def docker_login(self) -> str:
        registry = f"{self.cfg['region']}-docker.pkg.dev"
        token = self.gcloud("auth", "print-access-token", quiet=True).strip()
        config = self.home / "docker-config"
        config.mkdir(exist_ok=True)
        self.sh(["docker", "--config", str(config), "login", "-u", "oauth2accesstoken", "--password-stdin", f"https://{registry}"],
                input=token, quiet=True)
        return str(config)

    def portal_image(self, src: Path, sha: str) -> None:
        p, pins = self.cfg["portal"], self.cfg["pins"]
        args = []
        for k, v in {**pins, "GIT_SHA": sha, "BUILD_TIME": now_iso()}.items():
            args += ["--build-arg", f"{k}={v}"]
        tag = f"{p['image']}:{sha}"
        self.sh(["docker", "build", "-q", "-f", "portal/Dockerfile", *args, "-t", tag, "."], cwd=src, timeout=2400)
        self.sh(["docker", "--config", self.docker_login(), "push", "-q", tag], timeout=1800)

    def sync_vault_secrets(self, part: dict) -> list[str]:
        """The vault's values for the portal's extra secrets, copied into Secret Manager only when they changed."""
        added = []
        runtime = part["service_account"]
        for env_name, secret in part.get("vault_secrets", {}).items():
            if env_name.startswith("_"):
                continue
            value = self.vault.get(env_name, "")
            try:
                self.gcloud("secrets", "describe", secret, quiet=True)
                exists = True
            except Failed:
                exists = False
            if not value:
                if exists:                                # not in this box's vault, but Google has it: keep handing it over
                    added.append(f"{env_name}={secret}:latest")
                else:
                    self.log(f"no {env_name} in the vault or Secret Manager: that brain isn't offered")
                continue
            if not exists:
                self.gcloud("secrets", "create", secret, "--replication-policy=automatic", quiet=True)
            try:
                current = self.gcloud("secrets", "versions", "access", "latest", f"--secret={secret}", quiet=True)
            except Failed:
                current = None
            if current != value:
                tmp = Path(OIDC_DIR) / f"secret-{secret}"
                old = os.umask(0o077)
                try:
                    tmp.write_text(value, encoding="utf-8")
                    self.gcloud("secrets", "versions", "add", secret, f"--data-file={tmp}", quiet=True)
                finally:
                    os.umask(old)
                    tmp.unlink(missing_ok=True)
                self.log(f"stored a new version of {secret}")
            self.gcloud("secrets", "add-iam-policy-binding", secret, f"--member=serviceAccount:{runtime}",
                        "--role=roles/secretmanager.secretAccessor", quiet=True)
            added.append(f"{env_name}={secret}:latest")
        return added

    def serving(self, service: str) -> str:
        return self.gcloud("run", "services", "describe", service, f"--region={self.cfg['region']}",
                           "--format=value(status.traffic[0].revisionName)").strip()

    def portal(self, src: Path, sha: str) -> None:
        p, region = self.cfg["portal"], self.cfg["region"]
        extra = self.sync_vault_secrets(p)
        try:
            prev = self.serving(p["service"])
        except Failed:
            prev = ""
        self.log(f"serving before this deploy: {prev or '(none)'}")
        env = "^|^" + "|".join(f"{k}={v}" for k, v in p["env"].items())
        secrets = ",".join([f"{k}={v}:latest" for k, v in p["secrets"].items()] + extra)
        try:
            self.gcloud("run", "deploy", p["service"], f"--image={p['image']}:{sha}", f"--region={region}",
                        f"--service-account={p['service_account']}", *p["flags"], f"--set-env-vars={env}",
                        f"--set-secrets={secrets}")
            self.gcloud("run", "services", "update-traffic", p["service"], f"--region={region}", "--to-latest")
            self.smoke_portal(src)
        except Exception:
            if prev:
                self.gcloud("run", "services", "update-traffic", p["service"], f"--region={region}", f"--to-revisions={prev}=100")
                self.log(f"ROLLED BACK: all traffic is on {prev} again")
                raise Failed(f"the deploy failed and was rolled back to {prev}")
            raise

    def url(self, service: str) -> str:
        return self.gcloud("run", "services", "describe", service, f"--region={self.cfg['region']}", "--format=value(status.url)").strip()

    def http(self, method: str, url: str, body: dict | None = None, follow: bool = True, timeout: float = 30,
             tries: int = 1, headers: dict | None = None) -> tuple[int, str]:
        for attempt in range(tries):
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                         headers={"Content-Type": "application/json", **(headers or {})})
            opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
            try:
                with opener.open(req, timeout=timeout) as r:
                    return r.status, r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == tries - 1:
                    return e.code, e.read().decode("utf-8", "replace")
            except OSError:
                if attempt == tries - 1:
                    raise
            time.sleep(5)
        return 0, ""

    def expect(self, ok: bool, what: str) -> None:
        self.log(("ok    " if ok else "FAIL  ") + what)
        if not ok:
            raise Failed(f"smoke test: {what}")

    def smoke_portal(self, src: Path) -> None:
        """deploy.yml's smoke test, the same checks."""
        u = self.url(self.cfg["portal"]["service"])
        code, _ = self.http("GET", f"{u}/health", tries=4, timeout=20)
        self.expect(code == 200, "/health answers")
        self.expect(self.http("POST", f"{u}/home/report", {}, timeout=20)[0] == 401, "/home/report is locked")
        self.expect(self.http("GET", f"{u}/home/workshop", tries=3)[0] == 401, "/home/workshop is locked")
        self.expect(self.http("POST", f"{u}/home/deploys", {}, timeout=20)[0] == 401, "/home/deploys is locked")
        self.expect(self.http("GET", f"{u}/privacy", tries=3)[0] == 200, "/privacy renders")
        self.expect(self.http("GET", f"{u}/me", follow=False, tries=3)[0] in (200, 301, 302, 303, 307, 308), "/me signed out")
        self.expect(self.http("GET", f"{u}/")[0] == 200, "the sign-in page renders")
        self.expect("Wolfram" in self.http("GET", f"{u}/experiments/benchmark")[1], "the benchmark is bundled")
        self.expect("viewer.js" in self.http("GET", f"{u}/experiments/radiance/")[1], "Radiance is bundled")
        self.expect('"bundled": true' in self.http("GET", f"{u}/starcontrol/version")[1].replace('"bundled":true', '"bundled": true'),
                    "Star Control Zero is bundled")
        for path in ("/doomloop/version", "/shifting-prophesy/haiku/version"):
            self.expect('"bundled"' in self.http("GET", f"{u}{path}")[1], f"{path} answers")

    def mentat(self, src: Path, sha: str) -> str:
        m, region = self.cfg["mentat"], self.cfg["region"]
        try:
            self.gcloud("iam", "service-accounts", "describe", m["service_account"], quiet=True)
        except Failed:
            self.log(f"can't see {m['service_account']}: re-run infra/bootstrap. Skipping Mentat.")
            return "skipped"
        tag = f"{m['image']}:{sha}"
        self.sh(["docker", "build", "-q", "-f", "mentat/Dockerfile", "--build-arg", f"SIGMA_GROUND_REF={self.cfg['pins']['SIGMA_GROUND_REF']}",
                 "-t", tag, "."], cwd=src, timeout=2400)
        self.sh(["docker", "--config", self.docker_login(), "push", "-q", tag], timeout=1800)
        env = ",".join(f"{k}={v}" for k, v in m["env"].items())
        self.gcloud("run", "deploy", m["service"], f"--image={tag}", f"--region={region}", f"--service-account={m['service_account']}",
                    *m["flags"], f"--set-env-vars={env}")
        self.smoke_mentat(self.url(m["service"]))
        return ""

    def smoke_mentat(self, u: str) -> None:
        """deploy.yml's Mentat checks."""
        self.expect(self.http("GET", f"{u}/health")[0] == 200, "Mentat /health")
        self.expect(len(json.loads(self.http("GET", f"{u}/tools")[1])) > 100, "more than 100 tools")
        self.expect(self.http("POST", f"{u}/tools/convert_units", {"value": 1, "from_units": "mile", "to_units": "km"})[0] == 200,
                    "convert_units")
        caller = {"X-Skippy-Caller": "deploy-smoke"}
        r = json.loads(self.http("POST", f"{u}/ask", {"question": "If I throw a baseball at 45 degrees at 100 miles per hour, how far will "
                                 "it travel before it touches the ground on a flat surface?"}, timeout=60, headers=caller)[1])["result"]
        self.expect(203 < r["value"] < 205, f"the baseball goes {r['value']} {r.get('units')}")
        r = json.loads(self.http("POST", f"{u}/ask", {"question": "range of a projectile launched at 45 degrees"}, timeout=60,
                                 headers=caller)[1])["result"]
        self.expect(r["value"] is None and r["clarification"]["needs"] == ["speed_m_s"], "a missing input is asked for, not filled in")
        self.expect(self.http("GET", f"{u}/research")[0] == 200, "/research")
        accept = {"Accept": "application/json, text/event-stream"}
        r = json.loads(self.http("POST", f"{u}/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "deploy-smoke", "version": "0"}}},
            headers=accept)[1])["result"]
        self.expect("tools" in r["capabilities"], "/mcp initializes")
        r = json.loads(self.http("POST", f"{u}/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "schwarzschild_radius", "arguments": {"mass_kg": 1.989e30}}}, headers=accept)[1])["result"]
        v = json.loads(r["content"][0]["text"])["value"]
        self.expect(not r["isError"] and 2950 < v < 2960, f"/mcp r_s = {v}")
        r = json.loads(self.http("GET", f"{u}/wva/catalog")[1])["result"]
        self.expect(sum("weapon" in w for w in r["weapons"]) >= 20, "Weapon vs. Armor presets")

    def infra(self, src: Path) -> None:
        i = self.cfg["infra"]
        base = ["docker", "run", "--rm", "-v", f"{OIDC_DIR}:{OIDC_DIR}:ro", "-v", f"{src}:/src", "-w", f"/src/{i['dir']}",
                "-e", f"GOOGLE_APPLICATION_CREDENTIALS={oidc.CRED_FILE}", "-e", f"TF_VAR_billing_account={self.vault.get('BILLING_ACCOUNT', '')}",
                self.s["CD_TERRAFORM_IMAGE"]]
        self.sh([*base, "init", "-input=false", f"-backend-config=bucket={i['state_bucket']}"], timeout=600)
        self.sh([*base, "apply", "-input=false", "-auto-approve", "-var", f"project={self.cfg['project']}"], timeout=1800)

    def rollback(self, revision: str = "") -> str:
        """The portal's traffic onto `revision` (blank: the one before the serving one; "latest": the newest)."""
        if not getattr(self, "cfg", None):
            self.cfg = json.loads((HERE / "deploy.json").read_text(encoding="utf-8"))
        oidc.token()
        service, region = self.cfg["portal"]["service"], self.cfg["region"]
        if revision == "latest":
            self.gcloud("run", "services", "update-traffic", service, f"--region={region}", "--to-latest")
            return "latest"
        if not revision:
            names = self.gcloud("run", "revisions", "list", f"--service={service}", f"--region={region}",
                                "--sort-by=~metadata.creationTimestamp", "--format=value(metadata.name)", "--limit=10").split()
            now = self.serving(service)
            later = names[names.index(now) + 1:] if now in names else names[1:]
            if not later:
                raise Failed("no earlier revision to roll back to")
            revision = later[0]
        self.gcloud("run", "services", "update-traffic", service, f"--region={region}", f"--to-revisions={revision}=100")
        return revision

    # ── the timer ───────────────────────────────────────────────────────────────
    def handle(self, requests: list[dict]) -> None:
        done = self.state.setdefault("done_requests", [])
        for q in requests:
            if not isinstance(q, dict) or q.get("id") in done:
                continue
            op, arg = q.get("op"), str(q.get("arg") or "")
            self.say(f"cd: Recovery asked: {op} {arg}")
            try:
                if op in ("deploy", "rerun"):
                    sha = self.green()
                    if sha:
                        self.deploy(sha, why=f"Recovery: {op}")
                elif op == "rollback":
                    self.say(f"cd: rolled back to {self.rollback(arg)}")
            except Exception as e:                        # noqa: BLE001
                self.say(f"cd: {op} failed: {e}")
            done.append(q["id"])
            self.state["done_requests"] = done[-50:]
            self.save()

    def run(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        with open(self.home / "cd.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return
            self.handle(self.report())
            sha = self.green()
            if sha and sha != self.state.get("delivered"):
                self.deploy(sha)
            self.report()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main(argv: list[str]) -> int:
    cd = CD()
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        cd.run()
        return 0
    if cmd == "deploy":
        sha = argv[1] if len(argv) > 1 else cd.green()
        if not sha:
            print("main's newest commit hasn't passed CI")
            return 1
        return 0 if cd.deploy(sha, why="by hand")["conclusion"] == "success" else 1
    if cmd == "rollback":
        print(cd.rollback(argv[1] if len(argv) > 1 else ""))
        return 0
    if cmd == "status":
        print(json.dumps(cd.state, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
