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
       (no Mentat job: there is ONE Mentat, on skippy-core, built by the core deployer, and its Tailscale Funnel is the public address,
        core/install/mentat_funnel.sh. The Cloud Run copy was retired 2026-10-27: the Captain, needless cost, and two copies drift.)
       infra         only when infra/gcp-games/ or deploy.json changed since its last good apply: terraform apply
  3. report again after every job, so the ship card follows it live.

State: /var/lib/skippy-core/cd/state.json (runs, what is deployed where; each job's started_at and completed_at, and the portal job's
serving_at: when all traffic moved to the new revision); each run's log in runs/<id>.log.

    cd.py run                one pass (the timer)
    cd.py deploy [sha]       deploy now (main's newest green commit, or that one), whatever state.json says
    cd.py rollback [rev]     send the portal's traffic to that revision (blank: one step back from the serving one; latest: undo)
    cd.py ops <command> ...  scripts/remote.py on this box's identity: status, logs, start, stop, restart, tail, search, stats,
                             restart-game, backups, restore-game, discord-setup, home, configure (what the `ops` workflow did)
    cd.py domains            tools/domains.py: the portal's domains (what the `domains` workflow did)
    cd.py status             state.json

Settings (/etc/skippy-core/core.env, the environment overrides): CD_STATE, CI_LOCAL, CI_NAME, CD_VAULT (/etc/skippy-core/vault.env:
SKIPPY_HOME_TOKEN, SKIPPY_CORE_URL, SKIPPY_CORE_API_KEY), CD_PORTAL (the portal's run.app address), CD_GCLOUD_IMAGE,
CD_TERRAFORM_IMAGE. Standard library only.
"""
from __future__ import annotations

import fcntl
import json
import os
import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import quiet  # noqa: E402
import oidc  # noqa: E402
import rollback_pick  # noqa: E402

ENV_FILE = "/etc/skippy-core/core.env"
DEFAULTS = {"CD_STATE": "/var/lib/skippy-core/cd", "CI_LOCAL": "/var/lib/skippy-core/ci", "CI_NAME": "skippy-tel", "CI_BRANCH": "main",
            "CD_VAULT": "/etc/skippy-core/vault.env", "CD_PORTAL": "https://skippy-portal-1015050363686.us-west1.run.app",
            "CD_GCLOUD_IMAGE": "gcr.io/google.com/cloudsdktool/google-cloud-cli:stable",
            "CD_TERRAFORM_IMAGE": "hashicorp/terraform:1.9",
            "CD_TRUNK_GIT": "/mnt/pc/trunk/git", "CD_TRUNK_ARTIFACTS": "/mnt/pc/trunk/artifacts"}
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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DOMAINS_RUN = (
    "import os, sys, google.auth, google.auth.transport.requests as r\n"
    "c, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform', "
    "'https://www.googleapis.com/auth/siteverification'])\n"
    "c.refresh(r.Request())\n"
    "os.environ['TOKEN'] = c.token\n"
    "os.execv(sys.executable, [sys.executable, 'tools/domains.py'])\n")


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
        self.entry = None                                 # the job running now (job())

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
        entry = {"name": name, "status": "in_progress", "conclusion": "", "started_at": now_iso()}
        run["jobs"].append(entry)
        self.entry = entry                                # a job may stamp its own moments on it (portal: serving_at)
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
            entry["completed_at"] = run["updated_at"] = now_iso()
            self.entry = None
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


    # ── .vendor: what the builds need from our own repos, prepared here, never fetched from the internet ──────────
    def vendor(self, src: Path, star_control: bool) -> None:
        """src/.vendor/sigma-ground (the pinned commit, `git archive`) and, for the portal, src/.vendor/scz-web.tar.gz (the pinned
        Star Control Zero web build, checked against its sha256). Both come from the trunk (skippy-pc's D:\trunk, through the data
        trunk share) and are cached on this box, so a deploy still works while skippy-pc is off, once it has been seen here."""
        out = src / ".vendor"
        out.mkdir(exist_ok=True)
        self.vendor_sigma_ground(out / "sigma-ground")
        if star_control:
            self.vendor_star_control(out)
            self.vendor_popups(src, out / "popups")

    @staticmethod
    def popup_registry_files(src: Path) -> list[Path]:
        """The pop-up registry: services/popups/registry.json and each family's services/<family>-popups/popups.json (docs/popups.md)."""
        return [p for p in [src / "services" / "popups" / "registry.json", *sorted((src / "services").glob("*-popups/popups.json"))] if p.is_file()]

    def vendor_popups(self, src: Path, dest: Path) -> None:
        """The registry files, laid out as the portal image wants them (.vendor/popups/services/...). The Dockerfile used to COPY them through
        .dockerignore exceptions, which the classic builder on this box ignores for a wildcard path: the image then held only the test pop-up, and the
        portal did not know the vision, search, 3D or video tools (2026-10-27)."""
        shutil.rmtree(dest, ignore_errors=True)
        for path in self.popup_registry_files(src):
            target = dest / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    def popup_count(self, src: Path) -> int:
        n = 0
        for path in self.popup_registry_files(src):
            try:
                n += len(json.loads(path.read_text(encoding="utf-8")).get("popups") or [])
            except (OSError, ValueError):
                pass
        return n

    def vendor_sigma_ground(self, dest: Path) -> None:
        pin = self.cfg["pins"]["SIGMA_GROUND_REF"]
        cache = self.home / "vendor" / "sigma-ground.git"
        trunk = Path(self.s["CD_TRUNK_GIT"]) / "sigma-ground.git"
        if not cache.is_dir():
            cache.parent.mkdir(parents=True, exist_ok=True)
            self.sh(["git", "init", "--bare", "--quiet", str(cache)], quiet=True)
        if trunk.is_dir():
            try:                                              # best effort: the cache may already hold the pin
                self.sh(["git", "-c", "safe.directory=*", "-C", str(cache), "fetch", "--quiet", "--force", str(trunk),
                         "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"], quiet=True)
            except Failed as e:
                self.log(f"couldn't refresh the sigma-ground cache from the trunk: {e}")
        have = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(cache), "cat-file", "-e", f"{pin}^{{commit}}"],
                              capture_output=True).returncode == 0
        if not have:
            raise Failed(f"sigma-ground {pin[:7]} is in neither the trunk ({trunk}) nor this box's cache: is skippy-pc on?")
        tar = self.home / "vendor" / f"sigma-ground-{pin[:12]}.tar"
        self.sh(["git", "-c", "safe.directory=*", "-C", str(cache), "archive", "--format=tar", "-o", str(tar), pin], quiet=True)
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        with tarfile.open(tar) as t:
            t.extractall(dest, filter="data")
        tar.unlink(missing_ok=True)

    def vendor_star_control(self, out: Path) -> None:
        pins = self.cfg["pins"]
        tag, want = pins.get("STARCONTROL_REF", ""), pins.get("STARCONTROL_SHA256", "")
        if not tag:
            return
        cached = self.home / "vendor" / f"scz-web-{tag}.tar.gz"
        if not (cached.is_file() and sha256_file(cached) == want):
            source = Path(self.s["CD_TRUNK_ARTIFACTS"]) / "star-control" / tag / "scz-web.tar.gz"
            if not source.is_file():
                raise Failed(f"Star Control {tag} isn't in the trunk ({source}) or this box's cache: is skippy-pc on?")
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, cached)
        if sha256_file(cached) != want:
            cached.unlink(missing_ok=True)
            raise Failed(f"Star Control {tag}: the file's sha256 isn't the pinned {want[:12]}")
        (out / "scz-web.tar.gz").unlink(missing_ok=True)      # (a second vendor() in the same folder)
        try:
            os.link(cached, out / "scz-web.tar.gz")           # no second copy of half a gigabyte
        except OSError:
            shutil.copyfile(cached, out / "scz-web.tar.gz")

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
        self.vendor(src, star_control=True)
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
            if self.entry is not None:                    # run deploy (no --no-traffic) returns once the new revision has the traffic:
                self.entry["serving_at"] = now_iso()      # tools/loop_timer.py's "serving" (stamped after update-traffic it ran ~20 s late)
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

    def wait_ready(self, url: str, wait: float = 90, every: float = 5) -> bool:
        """A just-deployed service's /health (the readiness contract, core/ready.py): its 503 says "warming up, not yet", so ask again until
        it says 200 or `wait` runs out. A service that never gets ready still fails; the last answer goes in the log."""
        deadline = time.monotonic() + wait
        while True:
            try:
                code, body = self.http("GET", url, timeout=20)
            except OSError as e:
                code, body = 0, str(e)
            if code == 200:
                return True
            if time.monotonic() >= deadline:
                self.log(f"      {url}: {code} {body[:300]}")
                return False
            time.sleep(every)

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
        want = self.popup_count(src)                                       # every registered pop-up must have reached the image (the portal answers how many it knows)
        got = self.http("GET", f"{u}/version", tries=3)[1]
        try:
            known = int(json.loads(got).get("popups", -1))
        except (ValueError, TypeError):
            known = -1
        self.expect(known == want, f"the portal knows {known} pop-ups, the registry has {want}")
        self.expect("Wolfram" in self.http("GET", f"{u}/experiments/benchmark")[1], "the benchmark is bundled")
        self.expect("viewer.js" in self.http("GET", f"{u}/experiments/radiance/")[1], "Radiance is bundled")
        self.expect('"bundled": true' in self.http("GET", f"{u}/starcontrol/version")[1].replace('"bundled":true', '"bundled": true'),
                    "Star Control Zero is bundled")
        for path in ("/doomloop/version", "/shifting-prophesy/haiku/version"):
            self.expect('"bundled"' in self.http("GET", f"{u}{path}")[1], f"{path} answers")

    def infra(self, src: Path) -> None:
        i = self.cfg["infra"]
        base = ["docker", "run", "--rm", "-v", f"{OIDC_DIR}:{OIDC_DIR}:ro", "-v", f"{src}:/src", "-w", f"/src/{i['dir']}",
                "-e", f"GOOGLE_APPLICATION_CREDENTIALS={oidc.CRED_FILE}", "-e", f"TF_VAR_billing_account={self.vault.get('BILLING_ACCOUNT', '')}",
                self.s["CD_TERRAFORM_IMAGE"]]
        self.sh([*base, "init", "-input=false", f"-backend-config=bucket={i['state_bucket']}"], timeout=600)
        self.sh([*base, "apply", "-input=false", "-auto-approve", "-var", f"project={self.cfg['project']}"], timeout=1800)

    # ── bootstrap: infra/bootstrap from this box, so a new bucket or a permission never needs Cloud Shell again ────
    BOOTSTRAP_SA = "skippy-bootstrap@skippy-tel.iam.gserviceaccount.com"

    def terraform(self, cmd: list[str]) -> int:
        """A command whose output the operator watches (stdout and stderr are the caller's own); its exit code."""
        return subprocess.run(cmd).returncode

    def bootstrap(self, apply: bool = False) -> int:
        """infra/bootstrap (the project's buckets, IAM and the skippy-core trust) from main, in the Terraform container, signed in as
        skippy-bootstrap (skippy-deployer impersonates it: infra/bootstrap/main.tf). A PLAN unless `apply` is typed. The state is in
        gs://skippy-tel-tfstate, prefix bootstrap. It cannot run before the one Cloud Shell run that creates skippy-bootstrap."""
        self.cfg = json.loads((HERE / "deploy.json").read_text(encoding="utf-8"))
        oidc.token()
        work = self.checkout_main()
        try:
            src = work / "src"
            base = ["docker", "run", "--rm", "--network", "host", "-v", f"{OIDC_DIR}:{OIDC_DIR}:ro", "-v", f"{src}:/src", "-w", "/src/infra/bootstrap",
                    "-e", f"GOOGLE_APPLICATION_CREDENTIALS={oidc.CRED_FILE}", "-e", f"GOOGLE_IMPERSONATE_SERVICE_ACCOUNT={self.BOOTSTRAP_SA}",
                    "-e", "TF_IN_AUTOMATION=1", self.s["CD_TERRAFORM_IMAGE"]]
            bucket = self.cfg["infra"]["state_bucket"]
            code = self.terraform([*base, "init", "-input=false", f"-backend-config=bucket={bucket}"])
            if code:
                return code
            args = ["-input=false", "-var", f"project={self.cfg['project']}", "-var", f"region={self.cfg['region']}"]
            return self.terraform([*base, "apply", "-auto-approve", *args] if apply else [*base, "plan", *args])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def rollback(self, revision: str = "") -> str:
        """The portal's traffic onto `revision`: blank = one step back from the revision SERVING NOW (never "the newest other than
        serving": after a failed deploy rolled itself back, the newest is the broken one), `latest` = undo a rollback, or a name.
        core/cd/rollback_pick.py decides; afterwards /health must answer."""
        if not getattr(self, "cfg", None):
            self.cfg = json.loads((HERE / "deploy.json").read_text(encoding="utf-8"))
        oidc.token()
        service, region = self.cfg["portal"]["service"], self.cfg["region"]
        serving = self.serving(service)
        listing = self.gcloud("run", "revisions", "list", f"--service={service}", f"--region={region}", "--format=json", quiet=True)
        revs = rollback_pick.newest_first(json.loads(listing or "[]"))
        try:
            target = rollback_pick.pick(revs, serving, revision)
        except rollback_pick.Nothing as e:
            raise Failed(f"nothing was changed: {e}")
        if target == "latest":
            self.gcloud("run", "services", "update-traffic", service, f"--region={region}", "--to-latest")
        elif target != serving:
            self.gcloud("run", "services", "update-traffic", service, f"--region={region}", f"--to-revisions={target}=100")
        code, _ = self.http("GET", f"{self.url(service)}/health", tries=6, timeout=20)
        if code != 200:
            raise Failed(f"traffic is on {target}, but /health answers {code}: the problem is not the revision")
        return target

    # ── ops and domains: what the `ops` and `domains` GitHub workflows did, from this box ───────────────────────
    def checkout_main(self) -> Path:
        mirror = Path(self.s["CI_LOCAL"]) / self.s["CI_NAME"] / "mirror.git"
        self.home.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="cd-main-", dir=str(self.home)))
        trunk = Path(self.s["CD_TRUNK_GIT"]) / f"{self.s['CI_NAME']}.git"
        if trunk.is_dir():                    # CI is held while the private Skippy works, and its mirror with it: take the newest from the house
            branch = self.s["CI_BRANCH"]
            try:
                self.sh(["git", "-c", "safe.directory=*", "fetch", "--quiet", "--force", str(trunk), f"+refs/heads/{branch}:refs/heads/{branch}"],
                        cwd=mirror, quiet=True)
            except Failed as e:
                self.log(f"couldn't refresh the mirror from the trunk, using what CI last saw: {e}")
        sha = self.sh(["git", "rev-parse", self.s["CI_BRANCH"]], cwd=mirror, quiet=True).strip()
        self.sh(["git", "clone", "--quiet", "--no-checkout", str(mirror), str(work / "src")], quiet=True)
        self.sh(["git", "checkout", "--quiet", "--detach", sha], cwd=work / "src", quiet=True)
        return work

    def ops_image(self, src: Path) -> str:
        """python:3.12-slim with the portal's requirements (scripts/remote.py uses the portal's own modules), named by their hash."""
        req = src / "portal" / "requirements.txt"
        tag = f"skippy-ops:{sha256_file(req)[:16]}"
        if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0:
            return tag
        ctx = Path(tempfile.mkdtemp(prefix="cd-ops-image-", dir=str(self.home)))
        try:
            shutil.copy(req, ctx / "requirements.txt")
            (ctx / "Dockerfile").write_text("FROM python:3.12-slim\nCOPY requirements.txt /tmp/requirements.txt\n"
                                            "RUN pip install --no-cache-dir -r /tmp/requirements.txt\n", encoding="utf-8")
            self.sh(["docker", "build", "-q", "-t", tag, str(ctx)], timeout=1800)
        finally:
            shutil.rmtree(ctx, ignore_errors=True)
        return tag

    def python_in_house(self, args: list[str], env: dict[str, str] | None = None) -> int:
        """`python <args>` in a container with the checkout of main at /src and this box's Google identity: stdout and stderr are
        the caller's own (an operator is watching). Arguments go as separate argv items: never through a shell."""
        self.cfg = json.loads((HERE / "deploy.json").read_text(encoding="utf-8"))
        oidc.token()
        work = self.checkout_main()
        try:
            src = work / "src"
            image = self.ops_image(src)
            envs = []
            for k, v in {"GOOGLE_APPLICATION_CREDENTIALS": str(oidc.CRED_FILE), "GOOGLE_CLOUD_PROJECT": self.cfg["project"],
                         **(env or {})}.items():
                envs += ["-e", f"{k}={v}"]
            cmd = ["docker", "run", "--rm", "--network", "host", "-v", f"{src}:/src:ro", "-v", f"{OIDC_DIR}:{OIDC_DIR}:ro",
                   "-w", "/src", "-e", "PYTHONDONTWRITEBYTECODE=1", *envs, image, "python", *args]
            return subprocess.run(cmd).returncode
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def ops(self, argv: list[str]) -> int:
        """scripts/remote.py <command> <args>: the games VM and the portal's ops doors (status, logs, start, stop, restart, tail,
        search, stats, restart-game, backups, restore-game, discord-setup, home, configure). `home` and `discord-setup` ask the
        portal, which accepts only a Google ID token for the deployer: minted here, for the portal's ops audience."""
        self.cfg = json.loads((HERE / "deploy.json").read_text(encoding="utf-8"))
        env = {}
        if argv and argv[0] in ("home", "discord-setup"):
            oidc.token()
            env["OPS_ID_TOKEN"] = oidc.id_token("skippy-portal-ops")
        return self.python_in_house(["scripts/remote.py", *argv], env)

    def domains(self) -> int:
        """tools/domains.py: the portal's own domains (Cloud DNS, Google ownership, Cloud Run mappings). Idempotent. It needs an
        access token with the cloud-platform and siteverification scopes, minted here from this box's identity."""
        return self.python_in_house(["-c", DOMAINS_RUN])

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
            if quiet.should_defer(self.home / "busy-since"):
                self.log("the private Skippy is working: holding this tick")
                return
            sha = self.green()
            if sha:
                self.self_update(sha)
            self.handle(self.report())
            sha = self.green()
            if sha and sha != self.state.get("delivered"):
                self.deploy(sha)
            self.publish_all()
            self.report()

    # ── publish: what skippy-core puts in buckets besides the portal ────────────────────────────────────────────
    PUBLISH_RETRY_S = 600

    def publish_all(self) -> None:
        """Each repo listed in publish.json whose tip (a tarball: whose published folder) changed is built and uploaded. A failure
        is recorded and tried again in ten minutes; it never stops the others or the deploys."""
        try:
            specs = json.loads((HERE / "publish.json").read_text(encoding="utf-8"))["publish"]
        except (OSError, ValueError, KeyError):
            return
        if not getattr(self, "cfg", None):
            self.cfg = json.loads((HERE / "deploy.json").read_text(encoding="utf-8"))
        done = self.state.setdefault("published", {})
        for name, spec in specs.items():
            key = None
            try:
                key = self.publish_key(spec)
                have = done.get(name, {})
                if have.get("key") == key and "error" not in have:
                    continue                                  # published already
                if have.get("failed_key") == key and time.time() - have.get("failed_at", 0) < self.PUBLISH_RETRY_S:
                    continue                                  # this very version failed a moment ago: not again yet
                self.publish_one(name, spec, key)
                done[name] = {"key": key, "at": time.time()}
                self.say(f"cd: published {name} ({key[:7]})")
            except Exception as e:                            # noqa: BLE001 — the trunk away, a build that fails: said, retried later
                prev = done.get(name, {})
                done[name] = {"key": prev.get("key", ""), "error": str(e)[:300], "failed_at": time.time(), "failed_key": key}
                if prev.get("error") != str(e)[:300]:
                    self.say(f"cd: publishing {name} failed: {e}")
            self.save()

    def publish_repo(self, spec: dict) -> Path:
        repo = Path(self.s["CD_TRUNK_GIT"]) / f"{spec['repo']}.git"
        if not repo.is_dir():
            raise Failed(f"{spec['repo']} isn't in the trunk ({repo}): is skippy-pc on?")
        return repo

    def publish_key(self, spec: dict) -> str:
        """What identifies a version: the repo's tip, or for a tarball the tree id of the folder it carries (so a commit elsewhere in
        the repo publishes nothing)."""
        repo = self.publish_repo(spec)
        tip = self.sh(["git", "-c", "safe.directory=*", "-C", str(repo), "rev-parse", spec["branch"]], quiet=True).strip()
        if spec["kind"] == "tarball":
            return self.sh(["git", "-c", "safe.directory=*", "-C", str(repo), "rev-parse", f"{tip}:{spec['path']}"], quiet=True).strip()
        return tip

    def publish_one(self, name: str, spec: dict, key: str) -> None:
        repo = self.publish_repo(spec)
        git = ["git", "-c", "safe.directory=*", "-C", str(repo)]
        tip = self.sh([*git, "rev-parse", spec["branch"]], quiet=True).strip()
        self.home.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"publish-{name}-", dir=str(self.home)))
        try:
            if spec["kind"] == "tarball":
                self.publish_tarball(spec, repo, tip, key, work)
            else:
                self.publish_site(spec, repo, tip, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def publish_tarball(self, spec: dict, repo: Path, tip: str, tree: str, work: Path) -> None:
        """`addon/<files>` and `SOURCE` ("<tree> <commit>") in a tar.gz, as <commit>.tar.gz (a pin) and <branch>.tar.gz (the tip)."""
        raw = work / "raw.tar"
        self.sh(["git", "-c", "safe.directory=*", "-C", str(repo), "archive", "--format=tar", "-o", str(raw), tip, spec["path"]], quiet=True)
        out = work / "out"
        out.mkdir()
        archive = out / "addon.tar.gz"
        prefix = spec["path"].rstrip("/") + "/"
        with tarfile.open(raw) as src, tarfile.open(archive, "w:gz") as dst:
            for member in src:
                if not member.name.startswith(prefix) or not (member.isfile() or member.isdir()):
                    continue
                member.name = "addon/" + member.name[len(prefix):]
                dst.addfile(member, src.extractfile(member) if member.isfile() else None)
            source = f"{tree} {tip}\n".encode()
            info = tarfile.TarInfo("SOURCE")
            info.size = len(source)
            dst.addfile(info, io.BytesIO(source))
        base = f"gs://{spec['bucket']}/{spec['prefix']}"
        for target in (f"{tip}.tar.gz", f"{spec['branch']}.tar.gz"):
            self.gcloud("storage", "cp", "/src/addon.tar.gz", f"{base}/{target}", cwd=out)

    def publish_site(self, spec: dict, repo: Path, tip: str, work: Path) -> None:
        """Test, build and check in a container (the steps are the repo's own: what its GitHub workflow ran), then sync dist/ to the
        bucket with the version file LAST, so a build in flight is never shown."""
        src = work / "src"
        self.sh(["git", "-c", "safe.directory=*", "clone", "--quiet", "--no-checkout", str(repo), str(src)], quiet=True)
        self.sh(["git", "checkout", "--quiet", "--detach", tip], cwd=src, quiet=True)
        script = " && ".join(spec["steps"])
        self.sh(["docker", "run", "--rm", "-v", f"{src}:/src", "-w", "/src", "-e", "CI=1", "-e", f"GITHUB_REF_NAME={spec['branch']}",
                 "-e", "npm_config_update_notifier=false", spec["image"], "bash", "-c", f"set -e; {script}"], timeout=1800, quiet=True)
        dist = src / spec["dist"]
        if not (dist / spec["last"]).is_file():
            raise Failed(f"the build of {spec['repo']} left no {spec['dist']}/{spec['last']}")
        dest = f"gs://{spec['bucket']}/{spec['prefix']}"
        self.gcloud("storage", "rsync", "/src", dest, "--recursive", "--delete-unmatched-destination-objects",
                    f"--exclude=^{spec['last'].replace('.', chr(92) + '.')}$", cwd=dist)
        self.gcloud("storage", "cp", f"/src/{spec['last']}", f"{dest}/{spec['last']}", "--cache-control=no-store", cwd=dist)

    SELF_FILES = ("cd.py", "oidc.py", "rollback_pick.py", "deploy.json", "publish.json")

    INSTALLED = Path("/opt/skippy-core/cd")

    def self_update(self, sha: str, here: Path | None = None) -> None:
        """The installed copy takes the version in the commit CI has just passed, then starts over as that version: a change to the
        deployer is deployed with the tests it passed, with no hand step (an installed copy left behind once ran an old build for
        an hour). Only the installed copy does this, and only once per start (no loops)."""
        here = here or Path(__file__).resolve().parent
        if here != self.INSTALLED or os.environ.get("CD_SELF_UPDATED"):
            return
        mirror = Path(self.s["CI_LOCAL"]) / self.s["CI_NAME"] / "mirror.git"
        fresh = {}
        for name in self.SELF_FILES:
            r = subprocess.run(["git", "show", f"{sha}:core/cd/{name}"], cwd=mirror, capture_output=True)
            if r.returncode != 0 or not r.stdout:
                return                                       # the commit lacks a file: touch nothing
            fresh[name] = r.stdout
        changed = False
        for name, data in fresh.items():
            old = (here / name).read_bytes() if (here / name).is_file() else b""
            if data != old:
                tmp = here / f".{name}.new"
                tmp.write_bytes(data)
                os.chmod(tmp, 0o755 if name.endswith(".py") else 0o644)
                tmp.replace(here / name)
                changed = True
        if changed:
            self.say("cd: the installed deployer was behind this commit; updated, starting over")
            os.environ["CD_SELF_UPDATED"] = "1"
            os.execv(sys.executable, [sys.executable, str(here / "cd.py"), "run"])


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
    if cmd == "ops":
        return cd.ops(argv[1:])
    if cmd == "domains":
        return cd.domains()
    if cmd == "bootstrap":                                # plan only, unless "apply" is typed
        return cd.bootstrap(apply=argv[1:2] == ["apply"])
    if cmd == "vendor":                                   # the Core deployer's Mentat build: <dest>/.vendor/sigma-ground
        cd.cfg = json.loads((HERE / "deploy.json").read_text(encoding="utf-8"))      # (the pins live there; deploy() loads it, a bare command must too)
        cd.vendor(Path(argv[1]), star_control=False)
        return 0
    if cmd == "status":
        print(json.dumps(cd.state, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
