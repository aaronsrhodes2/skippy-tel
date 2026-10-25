"""The admin page's recovery buttons: what the Captain can do from a phone when a deploy or the private Skippy goes wrong.

Why here, and why so narrow (the CI/CD audit, findings A1-A4, L06, U01): the Captain is often away from skippy-pc, and the
only other way to roll the portal back, cancel a hung deploy or stop a runaway private-Skippy run is the GitHub app. So the
Admin page gets a few fixed buttons. Each one is:
  * an admin-only POST (auth.admin_required: a friend gets 403, a signed-out visitor the sign-in redirect), never a chat
    tool and never anything a friend's page links to;
  * from the portal's own page only (Origin, else Referer, must be this portal), on top of the SameSite=Lax cookie;
  * confirmed (the page asks; the POST carries confirm=yes, and without it the server asks again instead of acting);
  * rate limited per action, and logged: one JSON line per attempt to stdout (Cloud Logging) and the last 20 kept in
    memory for the card.

GitHub is reached with GITHUB_ACTIONS_TOKEN, a fine-grained token for this one repo with Actions read/write and nothing
else (it can't read code or secrets). `GitHub` below can call exactly six endpoints (the sixth, read only: the private Skippy's ship card), checked again inside the one
method that sends anything, so a bug elsewhere still can't reach another URL. Errors come back as plain sentences: never
a raw response body, never the token.

The private Skippy's buttons need no token: they work on portal/workshop.py's records and the home job queue.

Pure where it can be (the clock, the transport and the environment are arguments), so it is tested without a network.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

REPO = "aaronsrhodes2/skippy-tel"
API = "https://api.github.com"
TIMEOUT_S = 15
STUCK_AFTER_S = 20 * 60          # a deploy normally takes 4-8 min; deploy.yml's longest job may now take 20 (audit A1)
RUNS_SHOWN = 5
RUNS_FETCHED = 20                # enough to say "main is N pushes ahead" for a normal day
RUNS_CACHE_S = 30                # the admin page may be reloaded often; GitHub's view moves slowly
LOG_KEPT = 20

_RUN_ID = re.compile(r"[1-9][0-9]{0,19}")
_SHA = re.compile(r"[0-9a-f]{40}")
_REVISION = re.compile(r"skippy-portal-[a-z0-9-]{1,49}")
_ACTIVE = ("queued", "in_progress", "waiting", "pending", "requested")


@dataclass(frozen=True)
class Action:
    label: str            # the button
    ask: str              # the confirm question
    limit: int            # at most `limit` times per `window_s`, whoever asks
    window_s: int
    github: bool          # needs the token


# The allow-list. A POST naming anything else is a 404: nothing typed by a person picks code to run.
ACTIONS: dict[str, Action] = {
    "rollback": Action("Roll back the portal", "Roll the portal back? Traffic moves to an earlier revision and the portal "
                       "restarts (about 30 s). The next push to main undoes it.", 1, 120, True),
    "undo-rollback": Action("Undo a rollback", "Send traffic to the newest revision again? The portal restarts (about 30 s).",
                            1, 120, True),
    "rerun-failed": Action("Re-run the failed jobs", "Re-run the failed jobs of the newest deploy? If they pass, that commit "
                           "goes live.", 2, 600, True),
    "cancel-stuck": Action("Cancel the stuck deploy", "Cancel the deploy that has been running for over 20 minutes? If it had "
                           "already moved traffic, its own rollback step puts the previous revision back.", 1, 300, True),
    "workshop-cancel": Action("Cancel the current run", "Cancel the private Skippy's current run? A message still waiting for "
                              "skippy-pc is withdrawn and never delivered.", 6, 60, False),
    "workshop-clear": Action("Clear a stuck chat record", "Clear the stuck chat record? The chat stops waiting on it; anything "
                             "still running on skippy-pc is told to stop.", 6, 60, False),
    "workshop-pause": Action("Pause the private Skippy", "Pause the private Skippy? New messages are refused and the current run "
                             "is cancelled until you turn it back on.", 10, 60, False),
    "workshop-resume": Action("Turn the private Skippy back on", "Turn the private Skippy back on?", 10, 60, False),
    # skippy-pc itself: a job for the home reporter, carried out by the bridge (services/claude-bridge/host_ops.py).
    "pc-restart-service": Action("Restart service", "Restart that service on skippy-pc? It's down for a few seconds "
                                 "(Ollama and the music studio: up to a minute).", 6, 300, False),
    "pc-restart-bridge": Action("Restart the bridge", "Restart the private Skippy's bridge on skippy-pc? Its supervisor "
                                "starts it again within seconds. Refused while a run is going.", 2, 300, False),
    "pc-reset-workshop": Action("Reset Skippy's copy", "Put the private Skippy's own copy of that project back to what's on "
                                "GitHub? Uncommitted work goes into a git stash and unpushed commits onto a rescue branch: "
                                "nothing is deleted.", 3, 300, False),
    "pc-funnel-on": Action("Funnel on", "Open the audio, video and voice services to the internet again (Tailscale Funnel)?",
                           4, 300, False),
    "pc-funnel-off": Action("Funnel off", "Take the audio, video and voice services off the internet (Tailscale Funnel off)? "
                            "They stop playing anywhere but at home until it's back on.", 4, 300, False),
    # The starter on the host (starter.py, tools/skippy_starter.py): works when Docker or the bridge is down. Needs the PC on and signed in.
    "starter-start": Action("Start SkippyTel", "Start skippy-pc's whole stack? Docker Desktop if it isn't running, then the containers, then the "
                            "bridge (Skippy Desktop). It can take a couple of minutes.", 3, 600, False),
    "starter-stop": Action("Stop SkippyTel", "Stop skippy-pc's stack? The bridge (refused while a run is going) and every container stop, so the "
                           "voice, music, video and chat services go away until you start it again. Docker Desktop and the starter stay up.", 2, 600, False),
    "starter-restart": Action("Restart SkippyTel", "Stop and start skippy-pc's whole stack? Everything is down for a couple of minutes.", 2, 600, False),
    "pc-funnel-status": Action("Funnel status", "Ask skippy-pc what Tailscale Funnel is serving?", 10, 60, False),
}

# The services a "Restart service" button may name; the bridge holds the same list and checks again.
PC_SERVICES = ("flask-api", "whisper", "music-studio", "music-gate", "video-gate", "voice-gate", "home-reporter")
PC_WAIT_S = 45                   # how long the button waits for skippy-pc's answer before saying "sent"


class GitHubError(Exception):
    """Something went wrong talking to GitHub. The message is a plain sentence, safe to show and to log."""


def valid_revision(text: str | None) -> str | None:
    """The rollback workflow's input as the page may send it: blank (step back one), `latest`, or a revision name.
    Anything else is None (refused before GitHub is asked)."""
    value = (text or "").strip()
    if value == "" or value == "latest" or _REVISION.fullmatch(value):
        return value
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would follow a redirect and carry the Authorization header to wherever it points. The allow-list is about
    URLs, so a redirect is an error here, never a hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(request, timeout=timeout) as resp:                  # noqa: S310 — the URL is from the allow-list
            return resp.status, resp.read(2_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, b""                                                   # the body is never shown: only the status
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise GitHubError(f"GitHub didn't answer within {int(timeout)} seconds. Try again in a minute.") from None
        raise GitHubError("Couldn't reach GitHub from the portal. Try again in a minute.") from None


_PLAIN = {
    401: "GitHub refused the portal's token (expired or revoked?). Make a new one and put it in the vault as "
         "PORTAL_ACTIONS_TOKEN; the next deploy hands it to the portal.",
    403: "GitHub said the token may not do that (it needs Actions: read and write on this repo), or its rate limit is used up.",
    404: "GitHub couldn't find that: the workflow or run is gone, or the token can't see this repo.",
    409: "GitHub says that run can't be changed now (it has probably finished already).",
    422: "GitHub refused the request as given (for a rollback: is that revision name right?).",
}


class GitHub:
    """The only GitHub calls the portal makes. Each public method maps to one allow-listed endpoint."""

    # (method, path regex): the complete list. _call refuses anything that isn't one of these.
    ALLOWED = (
        ("GET", re.compile(rf"/repos/{re.escape(REPO)}/actions/workflows/deploy\.yml/runs\?branch=main&per_page=\d{{1,2}}")),
        ("GET", re.compile(rf"/repos/{re.escape(REPO)}/actions/runs/[1-9][0-9]{{0,19}}/jobs\?per_page=50")),
        # The private Skippy's ship card: the deploy runs of ONE commit (read only; a 40-hex sha and nothing else).
        ("GET", re.compile(rf"/repos/{re.escape(REPO)}/actions/workflows/deploy\.yml/runs\?head_sha=[0-9a-f]{{40}}&per_page=5")),
        ("POST", re.compile(rf"/repos/{re.escape(REPO)}/actions/workflows/rollback\.yml/dispatches")),
        # the one Recovery button: redeploy main when the portal is behind it or its last deploy failed (doctor.py, recovery_web.py)
        ("POST", re.compile(rf"/repos/{re.escape(REPO)}/actions/workflows/deploy\.yml/dispatches")),
        ("POST", re.compile(rf"/repos/{re.escape(REPO)}/actions/runs/[1-9][0-9]{{0,19}}/rerun-failed-jobs")),
        ("POST", re.compile(rf"/repos/{re.escape(REPO)}/actions/runs/[1-9][0-9]{{0,19}}/cancel")),
    )

    def __init__(self, token: str, transport=None, timeout: float = TIMEOUT_S):
        self.__token = (token or "").strip()
        self.transport = transport or _urllib_transport
        self.timeout = timeout

    def __repr__(self) -> str:                      # a stray print or traceback never shows the token
        return f"GitHub(repo={REPO!r}, configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self.__token)

    def _call(self, method: str, path: str, payload: dict | None = None):
        if not self.configured:
            raise GitHubError("The deploy buttons aren't configured: the portal has no GITHUB_ACTIONS_TOKEN.")
        if not any(m == method and rx.fullmatch(path) for m, rx in self.ALLOWED):
            raise GitHubError("The portal refused to make that GitHub call: it isn't on its list.")
        headers = {"Authorization": f"Bearer {self.__token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "skippy-portal-recovery"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        status, raw = self.transport(method, API + path, headers, body, self.timeout)
        if status >= 500:
            raise GitHubError(f"GitHub had a problem (HTTP {status}). Try again in a minute.")
        if not 200 <= status < 300:                     # a redirect included: it is never followed (see _NoRedirect)
            raise GitHubError(_PLAIN.get(status, f"GitHub refused the request (HTTP {status})."))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise GitHubError("GitHub answered with something the portal couldn't read.") from None

    # ── the five calls ──────────────────────────────────────────────────────
    def deploy_runs(self, limit: int = RUNS_FETCHED) -> list[dict]:
        data = self._call("GET", f"/repos/{REPO}/actions/workflows/deploy.yml/runs?branch=main&per_page={max(1, min(limit, 50))}")
        runs = (data or {}).get("workflow_runs") if isinstance(data, dict) else None
        return [r for r in (_run(x) for x in runs or []) if r]

    def run_jobs(self, run_id: int | str) -> list[dict]:
        rid = _run_id(run_id)
        data = self._call("GET", f"/repos/{REPO}/actions/runs/{rid}/jobs?per_page=50")
        jobs = (data or {}).get("jobs") if isinstance(data, dict) else None
        return [{"name": str(j.get("name") or "")[:80], "status": str(j.get("status") or "")[:20],
                 "conclusion": str(j.get("conclusion") or "")[:20]} for j in jobs or [] if isinstance(j, dict)]

    def runs_for_sha(self, sha: str) -> list[dict]:
        """The deploy runs of one commit, newest first (the ship card). Refused before any request unless `sha` is a
        full 40-hex commit id."""
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise GitHubError("That isn't a commit id.")
        data = self._call("GET", f"/repos/{REPO}/actions/workflows/deploy.yml/runs?head_sha={sha}&per_page=5")
        runs = (data or {}).get("workflow_runs") if isinstance(data, dict) else None
        return [r for r in (_run(x) for x in runs or []) if r and r["head_sha"] == sha]

    def dispatch_rollback(self, revision: str = "") -> None:
        value = valid_revision(revision)
        if value is None:
            raise GitHubError("That isn't a revision name: leave it blank, type latest, or a name like skippy-portal-00042-abc.")
        self._call("POST", f"/repos/{REPO}/actions/workflows/rollback.yml/dispatches",
                   {"ref": "main", "inputs": {"revision": value}})

    def dispatch_deploy(self) -> None:
        """Deploy main again (the workflow's own tests run first, as on every push)."""
        self._call("POST", f"/repos/{REPO}/actions/workflows/deploy.yml/dispatches", {"ref": "main"})

    def rerun_failed(self, run_id: int | str) -> None:
        self._call("POST", f"/repos/{REPO}/actions/runs/{_run_id(run_id)}/rerun-failed-jobs")

    def cancel(self, run_id: int | str) -> None:
        self._call("POST", f"/repos/{REPO}/actions/runs/{_run_id(run_id)}/cancel")


def _run_id(value) -> str:
    text = str(value).strip()
    if not _RUN_ID.fullmatch(text):
        raise GitHubError("That isn't a run number.")
    return text


def _when(iso) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _run(raw) -> dict | None:
    """One deploy run, reduced to what the card shows and checked for shape: anything odd is dropped, so nothing GitHub
    (or something pretending to be it) sends can put a strange link or text on the page."""
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), int) or raw["id"] <= 0:
        return None
    sha = str(raw.get("head_sha") or "")
    url = str(raw.get("html_url") or "")
    created = _when(raw.get("created_at"))
    if not _SHA.fullmatch(sha) or not url.startswith(f"https://github.com/{REPO}/actions/runs/") or not created:
        return None
    updated = _when(raw.get("updated_at")) or created
    return {"id": raw["id"], "status": str(raw.get("status") or "")[:20], "conclusion": str(raw.get("conclusion") or "")[:20],
            "head_sha": sha, "short": sha[:7], "created_at": created, "updated_at": updated, "html_url": url,
            "title": " ".join(str(raw.get("display_title") or "").split())[:90], "event": str(raw.get("event") or "")[:20]}


def ago(moment: datetime, now: datetime) -> str:
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min ago"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} days ago"


def state_word(run: dict) -> str:
    """One word for a run: running, queued, passed, failed, cancelled..."""
    if run["status"] != "completed":
        return "running" if run["status"] == "in_progress" else (run["status"] or "unknown")
    return {"success": "passed", "failure": "failed"}.get(run["conclusion"], run["conclusion"] or "finished")


def pipeline(runs: list[dict], live_commit: str, now: datetime) -> dict:
    """What the Deploy pipeline and Live version cards say, from the newest-first deploy runs and the serving commit.

      ahead:  how many newer pushes main has than what's serving (None when the live commit isn't among the runs, e.g. a
              local build, or more than RUNS_FETCHED pushes behind)
      stuck:  the newest run, when it has been running for longer than STUCK_AFTER_S (the cancel button's only target)
      failed: the newest run, when it is the newest and it failed (the only run "re-run" may touch: re-running an OLDER
              failed run would deploy an old commit over a newer one)
    """
    runs = [{**r, "word": state_word(r), "age": ago(r["created_at"], now)} for r in runs]
    shown = runs[:RUNS_SHOWN]
    newest = runs[0] if runs else None
    live_at = next((i for i, r in enumerate(runs) if r["head_sha"] == live_commit), None) \
        if live_commit and _SHA.fullmatch(live_commit) else None
    # Pushes, not commits: each push to main is one deploy run named by its tip, and the token can't read the code to
    # count commits. Several runs of one commit (a re-run, a manual dispatch) count once.
    ahead = len({r["head_sha"] for r in runs[:live_at]}) if live_at is not None else None
    green = next((r for r in runs if r["conclusion"] == "success"), None)
    stuck = newest if newest and newest["status"] in _ACTIVE and (now - newest["created_at"]).total_seconds() > STUCK_AFTER_S else None
    failed = newest if newest and newest["status"] == "completed" and newest["conclusion"] == "failure" else None
    # A newer green deploy than what's serving: a rollback is holding traffic back (and the next push will release it, A3).
    held_back = bool(green and live_at is not None and runs.index(green) < live_at)
    return {"runs": shown, "ahead": ahead, "newest": newest, "green": green, "stuck": stuck, "failed": failed,
            "held_back": held_back}


class Log:
    """The last LOG_KEPT recovery attempts (in memory: a deploy forgets them, Cloud Logging keeps the JSON lines)."""

    def __init__(self, clock=time.time, out=None):
        self.clock = clock
        self.out = out                      # a print-like function; None = print to stdout, flushed
        self._items: deque = deque(maxlen=LOG_KEPT)
        self._lock = threading.Lock()

    def add(self, action: str, by: str, result: str, ok: bool) -> None:
        line = {"event": "recovery", "action": action, "by": by, "result": result[:300], "ok": ok}
        with self._lock:
            self._items.appendleft({**line, "at": self.clock()})
        text = json.dumps(line)
        if self.out:
            self.out(text)
        else:
            print(text, flush=True)

    def recent(self) -> list[dict]:
        with self._lock:
            return list(self._items)


class RunsCache:
    """The deploy run list for the cards, fetched at most every RUNS_CACHE_S. On an error the last good list is kept and
    the error is shown beside it, so a GitHub hiccup never blanks the card."""

    def __init__(self, github: GitHub, clock=time.monotonic):
        self.github, self.clock = github, clock
        self._at: float | None = None
        self._runs: list[dict] | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def get(self, fresh: bool = False) -> tuple[list[dict] | None, str | None]:
        with self._lock:
            if not fresh and self._at is not None and self.clock() - self._at < RUNS_CACHE_S:
                return self._runs, self._error
        try:
            runs, error = self.github.deploy_runs(), None
        except GitHubError as exc:
            runs, error = None, str(exc)
        with self._lock:
            self._at = self.clock()
            if runs is not None:
                self._runs = runs
            self._error = error
            return self._runs, self._error

    def forget(self) -> None:
        with self._lock:
            self._at = None


def same_origin(origin: str | None, referer: str | None, own_roots: list[str]) -> bool:
    """Did this POST come from one of the portal's own pages? `own_roots` are like `https://host/` (where the request was
    addressed, and the configured PORTAL_URL). Origin is what browsers send on a POST; Referer is the fallback for one
    that strips it. Neither present = refused: a POST that can't say where it came from is not a button press here."""
    roots = {r.rstrip("/") for r in own_roots if r}
    if origin:
        return origin.rstrip("/") in roots
    if referer:
        return any(referer == r or referer.startswith(r + "/") for r in roots)
    return False
