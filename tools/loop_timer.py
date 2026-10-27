#!/usr/bin/env python3
"""Time ONE trip around the self-deploy loop: "prompt sent" to "new deployment visible".

    python3 tools/loop_timer.py                          # the newest commit CI has seen, as it happened (after the fact)
    python3 tools/loop_timer.py --since "12 minutes ago" # ... counting from when the prompt was sent
    python3 tools/loop_timer.py --watch --since now      # BEFORE pushing: waits for the push, CI, the deploy and the new version
    python3 tools/loop_timer.py --sha 8adcd7e --json     # one commit, as JSON

`make loop-time` and `make loop-watch` call it. Standard library only (urllib for the portal, `ssh` and `git` for the rest), so it
runs from Git Bash on Windows and on Linux. docs/cicd.md "Timing the loop" explains how to read the table.

The loop since 2026-10-25 (docs/no-github.md): a push to the house git (D:\\trunk\\git\\skippy-tel.git) -> skippy-core's CI (core/ci, a
timer every minute) tests it -> skippy-core's portal deployer (core/cd/cd.py, a timer every minute) deploys it to Cloud Run; beside it,
Core's deployer (core/deployer, every 2 minutes) updates Core's own services. Every stage comes from a record and is never guessed;
a stage with none prints `n/a (reason)`:
  * the PUSH is the house repo's reflog entry for the commit (`core.logAllRefUpdates`); without one, the ref file's time when the
    commit is still main's tip, else the commit's committer date (said in the note);
  * CI is its result file on skippy-core (results/<sha>.json: at, finished_at, each job's started_at / finished_at);
  * the deploy is cd.py's run in /var/lib/skippy-core/cd/state.json (created_at, each job's started_at / completed_at, and the portal
    job's serving_at: when all traffic moved to the new revision);
  * Core's services are the deployer's last good deploy (status.json last_good: started, at) when it is this commit;
  * "visible" is when THIS machine first saw `<portal>/version` report the pushed commit (polled once a second while watching), so it
    needs the timer to be running before the deploy finishes: afterwards the moment is unknowable;
  * the agent's own marks (bridge received, first edit, git push) only exist when `--bridge` can read them.
skippy-core is read over `ssh skippy-core` (one read-only Python snippet per look; nothing there is changed). All the arithmetic is in
UTC; the table shows local time (`--utc` for UTC).

Exit status: 0 the table printed and the new version is live (or, without --watch, nothing failed); 1 CI or the deploy failed, or the
version never showed up while watching; 2 couldn't run (no such commit, skippy-core unreachable, bad flag).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

PORTAL = "https://skippy-portal-1015050363686.us-west1.run.app"        # CLAUDE.md "Cloud: portal + games VM"
BRIDGE = "http://localhost:9337"                                           # the Claude bridge on skippy-pc
CORE_HOST = "skippy-core"                                                  # `ssh skippy-core` (the Captain's ssh config)
HOUSE = os.environ.get("LOOP_HOUSE_GIT") or ("D:/trunk/git/skippy-tel.git" if os.name == "nt" else "/mnt/pc/trunk/git/skippy-tel.git")
BRANCH = "main"
DEPLOY_JOB = "portal"                                                      # cd.py's job after which the new revision serves
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})     # what "first edit" means in the bridge's events
SKEW_S = 5.0            # clocks of this machine and skippy-core: a record this much BEFORE `--since` still counts as after it
LABELS = {"portal-image": "build portal image", "portal": "deploy portal", "mentat": "mentat deploy", "infra": "infra (terraform)",
          "suite": "suite", "flask-api": "flask-api", "services": "services", "shell-syntax": "shell syntax"}

# What `ssh skippy-core python3 -` runs: reads CI's newest results, Core's deployer status and cd.py's runs, and prints them as one JSON.
# Read only (every file is world-readable); a job's log tail is dropped on the spot, so the answer stays small.
REMOTE = r'''
import glob, json, os
B = "/var/lib/skippy-core"
def rd(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
ci = []
for f in sorted(glob.glob(B + "/ci/skippy-tel/results/*.json"), key=os.path.getmtime, reverse=True)[:40]:
    d = rd(f)
    if isinstance(d, dict):
        d.setdefault("sha", os.path.basename(f)[:-5])
        for j in d.get("jobs") or []:
            j.pop("tail", None)
        ci.append(d)
print(json.dumps({"ci": ci, "deployer": rd(B + "/deployer/status.json"), "cd_runs": (rd(B + "/cd/state.json") or {}).get("runs") or []}))
'''


class Problem(Exception):
    """Something the person running this can act on; the message says what. Exit status 2."""


# ── time ───────────────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: Any) -> datetime | None:
    """An ISO 8601 / epoch-seconds time as an aware UTC datetime (CI writes epoch seconds, cd.py and the deployer ISO). None for
    "missing": anything unreadable is treated as absent rather than guessed."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            moment = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        else:
            moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)       # machine-written times without a zone are UTC here
    except (ValueError, OverflowError, OSError):
        return None
    moment = moment.astimezone(timezone.utc)
    return moment if moment.year >= 2000 else None


_AGO = re.compile(r"(\d+(?:\.\d+)?)\s*(s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?)\s+ago", re.I)
_UNIT = {"s": 1, "m": 60, "h": 3600}


def parse_since(text: str, now: datetime) -> datetime:
    """`--since`: 'now', 'N minutes ago' (s/m/h and their long forms), or an ISO time (no zone = this machine's local time)."""
    text = text.strip()
    if text.lower() == "now":
        return now
    found = _AGO.fullmatch(text)
    if found:
        return now - timedelta(seconds=float(found.group(1)) * _UNIT[found.group(2)[0].lower()])
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise Problem(f"--since {text!r}: use an ISO time (2026-09-30T19:03:00Z) or 'N minutes ago' (also seconds, hours, 'now')") from None
    return moment.astimezone(timezone.utc)                         # a naive time is taken as local by astimezone()


def fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return ""
    whole = int(round(abs(seconds)))
    sign = "-" if seconds < 0 and whole else ""
    if whole < 60:
        return f"{sign}{whole}s"
    minutes, rest = divmod(whole, 60)
    if minutes < 60:
        return f"{sign}{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{hours}h{minutes:02d}m"


def fmt_clock(moment: datetime | None, utc: bool = False) -> str:
    if moment is None:
        return ""
    return (moment if utc else moment.astimezone()).strftime("%H:%M:%S")


def iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── the outside world, behind seams ──────────────────────────────────────────────

def read_core(host: str = CORE_HOST) -> dict:
    """One look at skippy-core's records (REMOTE above). An unreachable box or an unreadable answer is a Problem with ssh's own words."""
    try:
        done = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "python3", "-"], input=REMOTE,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except FileNotFoundError:
        raise Problem("ssh isn't installed or isn't on PATH") from None
    except subprocess.TimeoutExpired:
        raise Problem(f"ssh {host} timed out") from None
    if done.returncode != 0:
        raise Problem(f"ssh {host} failed: {(done.stderr or done.stdout).strip()[:300]}")
    try:
        data = json.loads(done.stdout)
    except ValueError:
        raise Problem(f"skippy-core didn't answer JSON: {done.stdout[:120]!r}") from None
    if not isinstance(data, dict):
        raise Problem("skippy-core's answer wasn't an object")
    return data


class HouseGit:
    """The house's bare repo (docs/no-github.md): main's tip, a commit's subject, and when a commit was pushed."""

    def __init__(self, path: str = HOUSE, branch: str = BRANCH):
        self.path, self.branch = path, branch

    def git(self, *args: str) -> str | None:
        try:
            done = subprocess.run(["git", "-c", "safe.directory=*", "--git-dir", self.path, *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    def tip(self) -> str | None:
        return self.git("rev-parse", f"refs/heads/{self.branch}")

    def resolve(self, prefix: str) -> str | None:
        return self.git("rev-parse", "--verify", "--quiet", f"{prefix}^{{commit}}")

    def subject(self, sha: str) -> str:
        return self.git("log", "-1", "--format=%s", sha) or ""

    def updates(self) -> list[tuple[str, str, datetime]]:
        """(old, new, when) for every update of main the reflog kept, oldest first (`logs/refs/heads/<branch>`: "old new who <epoch> <tz>")."""
        out = []
        try:
            lines = (Path(self.path) / "logs" / "refs" / "heads" / self.branch).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return out
        for line in lines:
            found = re.match(r"([0-9a-f]{40}) ([0-9a-f]{40}) .*?> (\d+) [+-]\d{4}", line)
            if found:
                out.append((found.group(1), found.group(2), datetime.fromtimestamp(int(found.group(3)), timezone.utc)))
        return out

    def pushes(self) -> list[tuple[str, datetime]]:
        return [(new, when) for _, new, when in self.updates()]

    def _in(self, sha: str, tip: str) -> bool:
        return not set(tip) <= {"0"} and (sha == tip or self.git("merge-base", "--is-ancestor", sha, tip) == "")

    def pushed(self, sha: str) -> tuple[datetime | None, str]:
        """When `sha` reached main, and how that is known. A commit pushed together with later ones is "pushed" with them: the update
        whose new tip holds it and whose old tip doesn't."""
        for old, new, when in self.updates():
            if self._in(sha, new) and not self._in(sha, old):
                return when, "reflog"
        if self.tip() == sha:
            try:
                ref = Path(self.path) / "refs" / "heads" / self.branch
                return datetime.fromtimestamp(ref.stat().st_mtime, timezone.utc), "the ref file's time (no reflog)"
            except OSError:
                pass
        stamp = self.git("log", "-1", "--format=%ct", sha)
        if stamp and stamp.isdigit():
            return (datetime.fromtimestamp(int(stamp), timezone.utc),
                    "the commit date: the push itself isn't recorded (git config core.logAllRefUpdates always on the house repo)")
        return None, ""


def http_json(url: str, headers: dict | None = None, timeout: float = 5.0) -> tuple[int, Any]:
    """GET `url`: (status, parsed JSON or None). Never raises: a network error is (0, None), so one dropped poll costs nothing."""
    request = urllib.request.Request(url, headers={"User-Agent": "skippy-loop-timer", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:        # noqa: S310 — the URLs are the Captain's own
            status, body = reply.status, reply.read(1_000_000)
    except urllib.error.HTTPError as err:
        return err.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None
    try:
        return status, json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return status, None


@dataclass
class Sources:
    """Everything that touches the outside world. Tests hand in fakes, so no test needs ssh, a real repo, a real clock or the network;
    `sleep` is the only way time passes here, so a fake clock can run a 4-minute loop instantly."""
    core: Callable[[], dict] = read_core
    house: Any = field(default_factory=HouseGit)
    http: Callable[..., tuple[int, Any]] = http_json
    clock: Callable[[], datetime] = utcnow
    sleep: Callable[[float], None] = time.sleep
    env: Any = field(default_factory=lambda: os.environ)


# ── which commit, and its records ────────────────────────────────────────────────

def ci_result(snap: dict, sha: str) -> dict | None:
    return next((r for r in snap.get("ci") or [] if isinstance(r, dict) and r.get("sha") == sha), None)


def cd_run(snap: dict, sha: str, after: datetime | None) -> dict | None:
    """cd.py's run for this commit: the FIRST one created after `after` (the one the push caused; a later one is a Recovery redeploy)."""
    runs = [r for r in snap.get("cd_runs") or [] if isinstance(r, dict) and r.get("head_sha") == sha and parse_ts(r.get("created_at"))]
    if after:
        runs = [r for r in runs if parse_ts(r["created_at"]) >= after - timedelta(seconds=SKEW_S)] or runs
    return min(runs, key=lambda r: parse_ts(r["created_at"]), default=None)


def known_shas(snap: dict) -> list[tuple[str, datetime | None]]:
    """Every commit skippy-core has a record of, with when CI began on it, newest first."""
    seen: dict[str, datetime | None] = {}
    for r in snap.get("ci") or []:
        if isinstance(r, dict) and r.get("sha"):
            seen[r["sha"]] = parse_ts(r.get("at"))
    for r in snap.get("cd_runs") or []:
        if isinstance(r, dict) and r.get("head_sha"):
            seen.setdefault(r["head_sha"], parse_ts(r.get("created_at")))
    far = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(seen.items(), key=lambda kv: kv[1] or far, reverse=True)


def pick_sha(snap: dict, house: Any, want: str | None, since: datetime | None) -> str | None:
    """The commit this loop is about. `--sha`: that commit (a prefix is enough). `--since`: the FIRST commit pushed after it (by the
    reflog, else by when CI took it up). Neither: the newest commit CI has seen."""
    shas = known_shas(snap)
    if want:
        hits = [s for s, _ in shas if s.lower().startswith(want.lower())]
        return hits[0] if hits else house.resolve(want)
    if since:
        floor = since - timedelta(seconds=SKEW_S)
        pushed = [(when, sha) for sha, when in house.pushes() if when >= floor]
        if pushed:
            return min(pushed)[1]
        later = [(when, sha) for sha, when in shas if when and when >= floor]
        return min(later)[1] if later else None
    return shas[0][0] if shas else None


# ── the bridge's marks (optional) ──────────────────────────────────────────────────

def bridge_marks(src: Sources, base: str, push: datetime | None, since: datetime | None,
                 task: str | None = None) -> tuple[dict[str, datetime], list[str]]:
    """What the agent's side knows: `bridge received` (the workshop run's creation), `first edit` and `git push` (the first
    tool events of those kinds). Needs the bridge's run summaries / events to carry times: until they do, the answer is
    `({}, [why])` and the table says so. The home token is read from SKIPPY_HOME_TOKEN and never printed."""
    token = (src.env.get("SKIPPY_HOME_TOKEN") or "").strip()
    if not token:
        return {}, ["bridge: n/a (SKIPPY_HOME_TOKEN isn't set in this shell, and the bridge's doors want it)"]
    head = {"Authorization": f"Bearer {token}"}
    base = base.rstrip("/")
    asked = src.clock()
    status, listing = src.http(f"{base}/workshop/runs", head)
    if status != 200 or not isinstance(listing, dict):
        return {}, [f"bridge: n/a ({base}/workshop/runs answered {status or 'nothing'}: is `make bridge` running, and is the token right?)"]
    candidates = []
    for run in listing.get("runs") or []:
        if not isinstance(run, dict) or not run.get("task"):
            continue
        made = parse_ts(run.get("created") or run.get("ts") or run.get("created_at"))
        if made is None and isinstance(run.get("age_s"), (int, float)):
            made = asked - timedelta(seconds=run["age_s"])         # the bridge's own "how old", measured against the reply
        candidates.append((made, str(run["task"])))
    if task:
        candidates = [c for c in candidates if c[1] == task]
    else:                                                      # the newest run that began before the push (and after the prompt)
        candidates = [c for c in candidates if c[0] and (push is None or c[0] <= push + timedelta(seconds=2))
                      and (since is None or c[0] >= since - timedelta(seconds=10))]
    if not candidates:
        return {}, ["bridge: n/a (no workshop run found between the prompt and the push)"]
    made, chosen = max(candidates, key=lambda c: c[0] or datetime.min.replace(tzinfo=timezone.utc))
    marks: dict[str, datetime] = {}
    notes: list[str] = []
    if made:
        marks["bridge received"] = made
    status, run = src.http(f"{base}/workshop/run/{chosen}?after=0", head)
    events = run.get("events") if status == 200 and isinstance(run, dict) else None
    if not isinstance(events, list):
        return marks, notes + [f"bridge: first edit / git push n/a (run {chosen}'s events were not readable)"]
    stamped = 0
    for event in events:
        when = parse_ts(event.get("ts")) if isinstance(event, dict) else None
        if when is None:
            continue
        stamped += 1
        if event.get("t") != "tool":
            continue
        name, detail = str(event.get("name") or ""), str(event.get("detail") or "")
        if name in EDIT_TOOLS:
            marks.setdefault("first edit", when)
        elif name == "Bash" and re.search(r"\bgit\s+push\b", detail):
            marks.setdefault("git push", when)
    if not stamped:
        notes.append("bridge: first edit / git push n/a (the bridge's events carry no ts yet)")
    else:
        notes += [f"bridge: {m} n/a (not in run {chosen}'s events)" for m in ("first edit", "git push") if m not in marks]
    return marks, notes


# ── the timeline: pure functions of what was seen ────────────────────────────────────

@dataclass
class Row:
    kind: str                          # stage | job | step | visible
    key: str
    label: str
    start: datetime | None = None
    end: datetime | None = None
    note: str = ""
    na: str = ""                       # no evidence: why. Printed as "n/a (reason)"; never filled with a guess
    depth: int = 0
    open: bool = False                 # still running: `end` is unknown, the duration shown is "so far"
    took: float | None = None          # a recorded duration with no start/end stamps (CI results written before they carried them)

    def seconds(self, now: datetime | None = None) -> float | None:
        if self.start and self.end:
            return (self.end - self.start).total_seconds()
        if self.open and self.start and now:
            return (now - self.start).total_seconds()
        return self.took


@dataclass
class Timeline:
    rows: list[Row]
    loop_start: datetime | None
    loop_start_is: str                 # "prompt sent" | "push (no --since)"
    total: Row
    critical_path: list[tuple[str, float]]
    serving_at: datetime | None        # all traffic on the new revision (cd.py's portal job)
    notes: list[str]
    meta: dict
    now: datetime
    failed: str = ""                   # CI or the deploy failed: what


@dataclass
class Records:
    """What is known about one commit's trip, gathered from the sources (all of it may be missing)."""
    sha: str
    title: str = ""
    push: datetime | None = None
    push_how: str = ""
    ci: dict | None = None
    cd: dict | None = None
    deployer: dict | None = None


def _ci_end(ci: dict) -> datetime | None:
    if ci.get("state") in ("running", None):
        return None
    start = parse_ts(ci.get("at"))
    return parse_ts(ci.get("finished_at")) or (start + timedelta(seconds=float(ci["seconds"])) if start and ci.get("seconds") is not None else None)


def _job(job: dict, key: str) -> Row:
    """A CI job (started_at / finished_at) or a cd.py job (started_at / completed_at) as a depth-1 row."""
    name = str(job.get("name") or "?")
    start, end = parse_ts(job.get("started_at")), parse_ts(job.get("finished_at") or job.get("completed_at"))
    verdict = job.get("conclusion") if "conclusion" in job else job.get("state")
    running = job.get("status") == "in_progress" or verdict in ("running", "")
    note = "" if verdict in ("success", "", None, "running") else str(verdict) + (f": {job['why']}" if job.get("why") else "")
    row = Row("step", f"{key}.{name}", LABELS.get(name, name), start, end, note, depth=1, open=running and end is None)
    if not start:
        if isinstance(job.get("seconds"), (int, float)):
            row.took = float(job["seconds"])
            row.note = (note + "; " if note else "") + "no start/end stamps in this record, only how long it took"
        elif running:
            row.na = "running, not stamped"
        else:
            row.na = f"no stamps in this record ({verdict or 'unknown'})"
    return row


def build_timeline(rec: Records, since: datetime | None, marks: dict[str, datetime], seen_at: datetime | None,
                   visible_why: str, now: datetime, portal: str = "") -> Timeline:
    push, ci, cd, dep = rec.push, rec.ci, rec.cd, rec.deployer
    rows: list[Row] = []
    notes: list[str] = []
    failed = ""

    # 1. prompt -> push, in as many pieces as the agent's marks allow
    chain: list[tuple[str, datetime]] = [("prompt", since)] if since else []
    dropped = []
    for name in ("bridge received", "first edit", "git push"):
        when = marks.get(name)
        if when is None:
            continue
        floor = chain[-1][1] if chain else None
        if (floor and when < floor) or (push and when > push + timedelta(seconds=2)):
            dropped.append(name)                                  # not between the prompt and the push: another run's, or a skewed clock
        else:
            chain.append((name, when))
    if dropped:
        notes.append(f"ignored bridge marks outside the prompt..push window: {', '.join(dropped)}")
    if push:
        chain.append(("push", push))
    if len(chain) < 2:                                            # only the push is known
        rows.append(Row("stage", "prompt_to_push", "prompt -> push",
                        na="no --since: pass --since 'N minutes ago' (or an ISO time) to count from the prompt"))
    else:
        for (a, ta), (b, tb) in zip(chain, chain[1:]):
            rows.append(Row("stage", f"{a}_to_{b}".replace(" ", "_"), f"{a} -> {b}", ta, tb))
    if push and rec.push_how != "reflog":
        notes.append(f"the push time is {rec.push_how}")
    if not push:
        notes.append("the push time is unknown (the commit isn't in the house repo at --house?)")

    # 2. CI on skippy-core: the wait for its timer, then the jobs side by side
    ci_start = parse_ts(ci.get("at")) if ci else None
    ci_end = _ci_end(ci) if ci else None
    rows.append(Row("stage", "ci_wait", "wait for CI (timer, 1 min)", push, ci_start,
                    na="" if ci_start else ("" if push else "no push time") or "CI hasn't taken this commit up yet"))
    if ci:
        state = ci.get("state") or "?"
        rows.append(Row("job", "ci", "CI (skippy-core)", ci_start, ci_end, note="" if state == "success" else state,
                        open=ci_end is None))
        rows += [_job(j, "ci") for j in ci.get("jobs") or [] if isinstance(j, dict)]
        if state == "failure":
            failed = "CI failed: " + ", ".join(j.get("name", "?") for j in ci.get("jobs") or [] if j.get("state") == "failure")
        elif state == "error":
            notes.append("CI hit the box's own trouble (an error, not the commit's fault): it tries again, at most three times")
    else:
        rows.append(Row("job", "ci", "CI (skippy-core)", na="no result for this commit (CI tests only main's newest commit)"))

    # 3. the portal deploy (cd.py): the wait for its timer, then its jobs, one after another
    serving_at = None
    created = parse_ts(cd.get("created_at")) if cd else None
    rows.append(Row("stage", "cd_wait", "wait for the deployer (timer)", ci_end, created,
                    na="" if (ci_end and created) else ("no deploy run for this commit yet" if not cd else "CI's end isn't known")))
    if cd:
        done = cd.get("status") == "completed"
        conclusion = cd.get("conclusion") or ""
        rows.append(Row("job", "cd", f"deploy run {cd.get('id')} (cd.py)", created, parse_ts(cd.get("updated_at")) if done else None,
                        note="" if conclusion in ("success", "") else conclusion, open=not done))
        after_serving = False
        for job in cd.get("jobs") or []:
            if not isinstance(job, dict) or job.get("name") == "test":     # "test" is CI's verdict, already above
                continue
            row = _job(job, "cd")
            if job.get("name") == DEPLOY_JOB and parse_ts(job.get("serving_at")):
                serving_at = parse_ts(job["serving_at"])
                into = f" ({fmt_dur((serving_at - row.start).total_seconds())} in)" if row.start else ""
                row.note = (row.note + "; " if row.note else "") + f"all traffic on the new revision at {fmt_clock(serving_at, True)} UTC{into}"
                after_serving = True
            elif after_serving and job.get("conclusion") == "success":
                row.note = "after the new revision was already serving"
            rows.append(row)
        if done and conclusion not in ("success", "") and not failed:
            failed = "the deploy failed: " + ", ".join(f"{j.get('name')} ({j.get('why') or 'failure'})" for j in cd.get("jobs") or []
                                                        if j.get("conclusion") == "failure")
        if done and conclusion == "success" and not serving_at:
            portal_job = next((j for j in cd.get("jobs") or [] if j.get("name") == DEPLOY_JOB), None)
            serving_at = parse_ts((portal_job or {}).get("completed_at")) or parse_ts(cd.get("updated_at"))
            notes.append("this run's record has no serving_at (cd.py before 2026-10-27): the end of "
                         + ("its portal job" if parse_ts((portal_job or {}).get("completed_at")) else "the whole run")
                         + " stands in for it, so serving is a little later than the truth")
    else:
        rows.append(Row("job", "cd", "deploy run (cd.py)", na="none yet" if not failed else "CI failed: nothing to deploy"))

    # 4. Core's own services (core/deployer), beside the portal deploy: only its LAST good deploy is kept
    good = (dep or {}).get("last_good") or {}
    if good.get("commit") == rec.sha:
        rows.append(Row("job", "core", "Core services (deployer)", parse_ts(good.get("started")), parse_ts(good.get("at")),
                        note="" if good.get("started") else "only the end is recorded (deployer before 2026-10-27)"))
    elif dep and dep.get("commit") == rec.sha:
        rows.append(Row("job", "core", "Core services (deployer)", na=f"{dep.get('state')}: {dep.get('detail') or ''}".strip(": ")))
    else:
        rows.append(Row("job", "core", "Core services (deployer)",
                        na="the deployer keeps only its last good deploy, and that is another commit" if dep else "no deployer status"))

    # 5. visible: the moment THIS machine first saw the pushed commit on /version
    if seen_at:
        lag = ""
        if serving_at:
            gap = (seen_at - serving_at).total_seconds()
            lag = (f"{fmt_dur(gap)} after all traffic moved" if gap >= 0
                   else f"{fmt_dur(-gap)} BEFORE cd.py's stamp (traffic switches before gcloud returns)")
        rows.append(Row("visible", "visible", "visible on the portal", serving_at, seen_at,
                        note="first seen by this machine" + (f"; {lag}" if lag else "")))
    else:
        rows.append(Row("visible", "visible", "visible on the portal", na=visible_why or "not seen yet"))

    # 6. the total, and the critical path (the stages are one after another now; Core's services run beside them)
    loop_start, loop_start_is = (since, "prompt sent") if since else (push, "push (no --since)")
    if loop_start and seen_at:
        total = Row("stage", "total", f"TOTAL {loop_start_is} -> visible", loop_start, seen_at)
    elif loop_start and serving_at:
        total = Row("stage", "total", f"TOTAL {loop_start_is} -> serving", loop_start, serving_at,
                    note="visible was not observed, so this stops where the new revision took all traffic")
    else:
        total = Row("stage", "total", f"TOTAL {loop_start_is} -> visible", na=failed or "the loop has not finished")
    path: list[tuple[str, float]] = []
    marks_at = [("push -> CI", push, ci_start), ("CI", ci_start, ci_end), ("CI -> deploy run", ci_end, created)]
    image = next((j for j in (cd or {}).get("jobs") or [] if j.get("name") == "portal-image"), None)
    image_end = parse_ts((image or {}).get("completed_at"))
    if image and parse_ts(image.get("started_at")):
        marks_at += [("run start", created, parse_ts(image["started_at"])), ("build portal image", parse_ts(image["started_at"]), image_end),
                     ("deploy portal (to serving)", image_end, serving_at)]
    else:
        marks_at.append(("deploy run to serving", created, serving_at))
    if seen_at:
        marks_at.append(("serving -> visible", serving_at, seen_at))
    for label, a, b in marks_at:
        if a and b:
            path.append((label, (b - a).total_seconds()))
    if dep and good.get("commit") == rec.sha:
        notes.append("Core's services deploy beside the portal, off its critical path")
    meta = {"sha": rec.sha, "title": rec.title or (cd or {}).get("title"), "cd_run": (cd or {}).get("id"),
            "ci_state": (ci or {}).get("state"), "deploy": (cd or {}).get("conclusion") or (cd or {}).get("status"),
            "push": iso(push), "push_from": rec.push_how or None, "portal": portal,
            "push_to_serving_s": round((serving_at - push).total_seconds(), 1) if push and serving_at else None}
    return Timeline(rows, loop_start, loop_start_is, total, path, serving_at, notes, meta, now, failed)


def render(tl: Timeline, utc: bool = False, extra: list[str] | None = None) -> str:
    """The table, ASCII only (Git Bash on Windows prints it as is)."""
    rows = [*tl.rows, tl.total]
    label_w = max(len(r.label) + 2 * r.depth for r in rows)
    zone = "UTC" if utc else (datetime.now().astimezone().strftime("UTC%z") or "local")
    meta = tl.meta
    out = [f"loop {str(meta.get('sha') or '')[:7]}  {str(meta.get('title') or '')[:70]}",
           f"CI {meta.get('ci_state') or 'n/a'}, deploy run {meta.get('cd_run') or 'n/a'} ({meta.get('deploy') or 'n/a'})",
           f"portal {meta.get('portal') or '?'}   times are {'UTC' if utc else 'local'} ({zone}); took = end - start", ""]
    out.append(f"{'stage':<{label_w}}  {'start':<8}  {'end':<8}  {'took':>7}  note")
    for r in rows:
        if r is tl.total:
            out.append("-" * (label_w + 40))
        took = fmt_dur(r.seconds(tl.now))
        if r.open and not r.end and took:
            took += "+"
        note = f"n/a ({r.na})" if r.na else r.note
        out.append(f"{('  ' * r.depth + r.label):<{label_w}}  {fmt_clock(r.start, utc):<8}  {fmt_clock(r.end, utc):<8}  {took:>7}  {note}".rstrip())
    if tl.critical_path:
        path = " -> ".join(f"{name} {fmt_dur(sec)}" for name, sec in tl.critical_path)
        whole = meta.get("push_to_serving_s")
        out += ["", f"critical path: {path}" + (f"   [push -> serving: {fmt_dur(whole)}]" if whole is not None else "")]
    for line in [*tl.notes, *(extra or [])]:
        out.append(f"* {line}")
    return "\n".join(out).encode("ascii", "replace").decode("ascii")      # a commit title may carry characters a Windows console can't print


def to_dict(tl: Timeline, extra: list[str] | None = None) -> dict:
    def one(r: Row) -> dict:
        sec = r.seconds(tl.now)
        return {"kind": r.kind, "key": r.key, "label": r.label, "depth": r.depth, "start": iso(r.start), "end": iso(r.end),
                "seconds": None if sec is None else round(sec, 1), "running": bool(r.open and not r.end),
                "note": r.note, "na": r.na or None}
    return {**tl.meta, "loop_start": iso(tl.loop_start), "loop_start_is": tl.loop_start_is,
            "stages": [one(r) for r in tl.rows], "total": one(tl.total),
            "serving_at": iso(tl.serving_at), "failed": tl.failed or None,
            "critical_path": [{"label": n, "seconds": round(s, 1)} for n, s in tl.critical_path],
            "notes": [*tl.notes, *(extra or [])]}


# ── following one loop ────────────────────────────────────────────────────────────

class Follow:
    """One loop, observed: finds the commit, re-reads skippy-core's records, and polls /version. `step()` does one tick; `watch()`
    ticks until the loop is over. Everything slow goes through `src`, so a test drives it with a fake world and a fake clock."""

    def __init__(self, src: Sources, portal: str, sha: str | None, since: datetime | None, watch: bool,
                 core_every: float = 10.0, house_every: float = 2.0, timeout: float = 1800.0, grace: float = 180.0, utc: bool = False):
        self.src, self.portal = src, portal.rstrip("/")
        self.want, self.since, self.watching, self.utc = sha, since, watch, utc   # `want` may be an abbreviation; `sha` is the full commit
        self.sha: str | None = None
        self.core_every, self.house_every, self.timeout, self.grace = core_every, house_every, timeout, grace
        self.snap: dict = {}
        self.push: datetime | None = None
        self.push_how = ""
        self.seen_at: datetime | None = None
        self.already_live_at: datetime | None = None   # the new commit was already serving at the first look: its start can't be known
        self.version: dict | None = None
        self.first_probe: tuple[datetime, dict] | None = None
        self.baseline: tuple | None = None
        self.ended = ""                    # why watching stopped ("" = still going)
        self.failed = False
        self._last_core: datetime | None = None
        self._last_house: datetime | None = None
        self.core_errors = 0
        self._announced: dict[str, str] = {}
        self.begun = src.clock()
        self._tip0 = src.house.tip() if watch and not sha else None      # main before the push we wait for

    # -- the push --
    def look_at_house(self) -> None:
        """While watching for a push: main moving off the tip it had when the watch began IS the push. When the house keeps no reflog,
        that moment, as seen from here, is the push time (a poll every couple of seconds)."""
        self._last_house = self.src.clock()
        tip = self.src.house.tip()
        if not tip or tip == self._tip0:
            return
        self.sha = tip
        when, how = self.src.house.pushed(tip)
        if how != "reflog":
            when, how = self._last_house, "when this machine saw main move (no reflog on the house repo)"
        self.push, self.push_how = when, how

    # -- skippy-core --
    def refresh(self) -> None:
        """Re-read skippy-core. A watch of several minutes rides out a dropped ssh or two; five in a row, or any failure without
        --watch, is a Problem."""
        self._last_core = self.src.clock()
        try:
            self.snap = self.src.core() or {}
            self.core_errors = 0
        except Problem:
            self.core_errors += 1
            if not self.watching or self.core_errors >= 5:
                raise
            return
        if self.sha is None and not (self.watching and not self.want):
            self.sha = pick_sha(self.snap, self.src.house, self.want, self.since)
        if self.sha and self.push is None:
            self.push, self.push_how = self.src.house.pushed(self.sha)

    def records(self) -> Records:
        sha = self.sha or ""
        return Records(sha, self.src.house.subject(sha) if sha else "", self.push, self.push_how, ci_result(self.snap, sha),
                       cd_run(self.snap, sha, self.push or self.since), self.snap.get("deployer"))

    def serving_at(self) -> datetime | None:
        run = cd_run(self.snap, self.sha or "", self.push or self.since)
        job = next((j for j in (run or {}).get("jobs") or [] if j.get("name") == DEPLOY_JOB), None)
        return parse_ts((job or {}).get("serving_at"))

    def _report(self, say: Callable[[str], None]) -> None:
        """While watching: one stderr line per stage whose state changed, so a long wait isn't silent."""
        rec = self.records()
        states = {"push": iso(rec.push) and "seen"}
        if rec.ci:
            states["CI"] = rec.ci.get("state")
            states.update({f"CI {j.get('name')}": j.get("state") for j in rec.ci.get("jobs") or []})
        if rec.cd:
            states.update({f"deploy {j.get('name')}": j.get("conclusion") or j.get("status") for j in rec.cd.get("jobs") or []
                           if j.get("name") != "test"})
        for name, state in states.items():
            if state and self._announced.get(name) != state:
                self._announced[name] = state
                say(f"[{fmt_clock(self.src.clock(), self.utc)}] {name}: {state}")

    # -- the portal --
    def probe(self) -> None:
        now = self.src.clock()
        status, body = self.src.http(f"{self.portal}/version")
        if status != 200 or not isinstance(body, dict):
            return
        self.version = body
        if self.seen_at or self.already_live_at or not self.sha:
            return
        ident = (body.get("revision"), body.get("started_at"))
        if self.first_probe is None:
            self.first_probe = (now, body)
            if body.get("commit") == self.sha:
                if cd_run(self.snap, self.sha, self.push or self.since):
                    self.already_live_at = now     # the deploy is under way or over: the new revision may have been serving for a while
                else:
                    self.baseline = ident          # before the deploy even began: an earlier revision of the same commit. Wait for a NEW one
                return
        if body.get("commit") == self.sha and ident != self.baseline:
            self.seen_at = now

    def why_not_visible(self) -> str:
        """The reason the `visible` row has no time (it is only asked when there is none)."""
        if self.already_live_at:
            return (f"already live when first checked at {fmt_clock(self.already_live_at, self.utc)}, so the moment it appeared can't be "
                    "known: start `make loop-watch` before the deploy ends")
        if self.version is None:
            return f"{self.portal}/version didn't answer (portal down, wrong --portal, or the route isn't deployed yet)"
        shown = str(self.version.get("commit") or "?")[:7]
        return (f"{self.portal}/version reports {shown}, not {str(self.sha or '')[:7]}: this deploy is not live yet, "
                "or a later one has replaced it")

    def done(self) -> str:
        """"" while the loop is still going, else why it is over (only --watch ends on its own; a one-shot just looks once)."""
        now = self.src.clock()
        if self.seen_at:
            if self.serving_at() or (now - self.seen_at).total_seconds() > 30:
                return "visible"
            return ""                                   # seen; let cd.py's own stamp catch up so the lag row is real
        if self.already_live_at:
            return "already live"
        rec = self.records() if self.sha else None
        if rec and rec.ci and rec.ci.get("state") == "failure":
            self.failed = True
            return "CI failed: the new version will not go live"
        if rec and rec.cd and rec.cd.get("status") == "completed":
            if rec.cd.get("conclusion") != "success":
                self.failed = True
                return f"the deploy run ended {rec.cd.get('conclusion')}: the new version did not go live"
            ended = parse_ts(rec.cd.get("updated_at")) or now
            if self.watching and (now - ended).total_seconds() > self.grace:
                self.failed = True
                return "the deploy finished but /version never showed the new commit (wrong --portal? a later commit already deployed?)"
        if self.watching and (now - self.begun).total_seconds() > self.timeout:
            self.failed = True
            return f"gave up after {fmt_dur(self.timeout)}"
        return ""

    def step(self, say: Callable[[str], None] = lambda _m: None) -> str:
        now = self.src.clock()
        if self.sha is None and self._tip0 is not None and (self._last_house is None or (now - self._last_house).total_seconds() >= self.house_every):
            self.look_at_house()
        if self.sha and (self._last_core is None or (now - self._last_core).total_seconds() >= self.core_every):
            self.refresh()
            if self.watching:
                self._report(say)
        elif self.sha is None and self._tip0 is None and self._last_core is None:
            self.refresh()                               # --sha, or no house to watch: find the commit from skippy-core's records
        if self.sha:
            self.probe()
        self.ended = self.done()
        return self.ended

    def watch(self, say: Callable[[str], None] = lambda _m: None) -> None:
        try:
            while not self.step(say):
                self.src.sleep(1.0)
        except KeyboardInterrupt:
            self.ended = "interrupted (Ctrl-C): this is what was seen so far"
            say(self.ended)


def run_once(follow: Follow) -> None:
    """Without --watch: one look at skippy-core and one at /version."""
    follow.refresh()
    if follow.sha:
        follow.probe()
        follow.ended = follow.done()


# ── the command line ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None, src: Sources | None = None, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    ap = argparse.ArgumentParser(prog="loop_timer.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="when the prompt was sent: an ISO time, 'N minutes ago' or 'now' (default: the push)")
    ap.add_argument("--sha", help="the commit (full or abbreviated) whose loop to time (default: the newest commit CI has seen)")
    ap.add_argument("--watch", action="store_true", help="wait for the loop to finish (Ctrl-C prints what was seen)")
    ap.add_argument("--portal", default=PORTAL, help="the portal whose /version is polled (default: production)")
    ap.add_argument("--core", default=CORE_HOST, help=f"the ssh host holding CI's and the deployers' records (default {CORE_HOST})")
    ap.add_argument("--house", default=HOUSE, help=f"the house's bare repo, for the push time (default {HOUSE}; env LOOP_HOUSE_GIT)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the table")
    ap.add_argument("--utc", action="store_true", help="show UTC instead of this machine's local time")
    ap.add_argument("--bridge", nargs="?", const=BRIDGE, metavar="URL",
                    help=f"also read the agent's marks from the Claude bridge (default {BRIDGE}; needs SKIPPY_HOME_TOKEN)")
    ap.add_argument("--bridge-task", help="the bridge run (task id) to read, instead of the newest before the push")
    ap.add_argument("--timeout", type=float, default=1800.0, help="--watch gives up after this many seconds (default 1800)")
    args = ap.parse_args(argv)
    src = src or Sources(core=lambda: read_core(args.core), house=HouseGit(args.house))

    def say(message: str) -> None:
        print(message, file=err, flush=True)

    try:
        now = src.clock()
        since = parse_since(args.since, now) if args.since else None
        follow = Follow(src, args.portal, args.sha, since, args.watch, timeout=args.timeout, utc=args.utc)
        if args.watch:
            say("watching " + (f"commit {args.sha}" if args.sha else "for the next push to main") + " (Ctrl-C prints what was seen)")
            follow.watch(say)
        else:
            run_once(follow)
        if not follow.sha:
            raise Problem("no commit found" + (f" for {args.sha}" if args.sha else "")
                          + (f" pushed since {iso(since)}" if since and not args.sha else "")
                          + ": has the push happened? (--watch waits for it)")
        rec = follow.records()
        marks, extra = ({}, [])
        if args.bridge:
            marks, extra = bridge_marks(src, args.bridge, rec.push, since, args.bridge_task)
        tl = build_timeline(rec, since, marks, follow.seen_at, "" if follow.seen_at else follow.why_not_visible(), src.clock(), follow.portal)
        extra = list(extra)
        if follow.version and follow.seen_at:
            v = follow.version
            extra.append(f"serving instance: revision {v.get('revision') or '?'}, image built {v.get('built_at') or '?'}, "
                         f"process started {v.get('started_at') or '?'} (UTC, from /version)")
        if follow.ended and follow.ended not in ("visible", "already live"):
            extra.append(follow.ended)
    except Problem as problem:
        print(f"loop_timer: {problem}", file=err)
        return 2
    if args.json:
        print(json.dumps(to_dict(tl, extra), indent=2), file=out)
    else:
        print(render(tl, args.utc, extra), file=out)
    if follow.failed or tl.failed:
        return 1
    if args.watch:
        return 0 if follow.seen_at or follow.ended == "already live" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
