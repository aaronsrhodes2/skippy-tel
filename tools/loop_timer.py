#!/usr/bin/env python3
"""Time ONE trip around the self-deploy loop: "prompt sent" to "new deployment visible".

    python3 tools/loop_timer.py                          # the newest deploy run, as it happened (after the fact)
    python3 tools/loop_timer.py --since "12 minutes ago" # ... counting from when the prompt was sent
    python3 tools/loop_timer.py --watch --since now      # BEFORE pushing: waits for the push, the run and the new version
    python3 tools/loop_timer.py --sha 8adcd7e --json     # one commit, as JSON

`make loop-time` and `make loop-watch` call it. Standard library only (urllib for the portal, the `gh` CLI for GitHub), so
it runs from Git Bash on Windows and on Linux. docs/cicd.md "Timing the loop" explains how to read the table.

Every stage comes from evidence and is never guessed; a stage with none prints `n/a (reason)`:
  * the PUSH is the deploy run's `createdAt` (GitHub creates the run the moment the push lands);
  * queue, every job and every step of the deploy job are GitHub's own start/end stamps (`gh run view --json jobs`);
  * "the new revision starts serving" is the END of the deploy job's `Deploy to Cloud Run` step;
  * "visible" is when THIS machine first saw `<portal>/version` report the pushed commit (polled once a second while
    watching), so it needs the timer to be running before the deploy finishes: afterwards the moment is unknowable;
  * the agent's own marks (bridge received, first edit, git push) only exist when `--bridge` can read them.
All the arithmetic is in UTC; the table shows local time (`--utc` for UTC).

Exit status: 0 the table printed and the new version is live (or, without --watch, the run succeeded); 1 the run failed
or was cancelled, or the version never showed up while watching; 2 couldn't run (no run, gh missing/not signed in, bad flag).
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
from typing import Any, Callable

PORTAL = "https://skippy-portal-1015050363686.us-west1.run.app"        # CLAUDE.md "Cloud: portal + games VM"
BRIDGE = "http://localhost:9337"                                           # the Claude bridge on skippy-pc
WORKFLOW, BRANCH = "deploy.yml", "main"
DEPLOY_JOB, DEPLOY_STEP = "portal", "Deploy to Cloud Run"                  # the job and step after which the new revision serves
RUN_FIELDS = "databaseId,headSha,createdAt,startedAt,updatedAt,event,status,conclusion,url,displayTitle"
VIEW_FIELDS = RUN_FIELDS + ",jobs"
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})     # what "first edit" means in the bridge's events
SKEW_S = 5.0            # this machine's clock vs GitHub's: a run created this much BEFORE `--since` still counts as after it
LABELS = {
    "gate": "gate (bootstrapped?)", "changes": "changes (what to build)", "test": "tests",
    "test-flask-api": "tests: flask-api", "portal-image": "build portal image", "mentat-test": "mentat tests",
    "mentat": "mentat deploy", "infra": "infra (terraform)", "portal": "deploy portal",
}
NOISE_STEP = re.compile(r"Set up job|Complete job|Post .*", re.I)         # bookkeeping steps: no information in them


class Problem(Exception):
    """Something the person running this can act on; the message says what. Exit status 2."""


# ── time ───────────────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: Any) -> datetime | None:
    """A GitHub / ISO 8601 / epoch-seconds time as an aware UTC datetime. None for "missing": GitHub writes a job that has not
    started as 0001-01-01T00:00:00Z, and anything unreadable is treated as absent rather than guessed."""
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

def run_gh(args: list[str]) -> str:
    """`gh <args>`: its stdout. A missing gh, a signed-out gh or an API error becomes a Problem with gh's own words."""
    try:
        # GH_REPO: the checkout's origin is the house's own git now (docs/no-github.md), so gh can't infer the repo from it;
        # GitHub is only the transition mirror whose workflows still deploy, until phase 3 retires this tool's GitHub side.
        env = {**os.environ, "GH_REPO": os.environ.get("GH_REPO", "aaronsrhodes2/skippy-tel")}
        done = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)
    except FileNotFoundError:
        raise Problem("the GitHub CLI (gh) isn't installed or isn't on PATH") from None
    except subprocess.TimeoutExpired:
        raise Problem(f"gh {' '.join(args[:3])} timed out") from None
    if done.returncode != 0:
        raise Problem(f"gh {' '.join(args[:3])} failed: {(done.stderr or done.stdout).strip()[:300]} (signed in? `gh auth status`)")
    return done.stdout


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
    """Everything that touches the outside world. Tests hand in fakes (plain functions), so no test needs a real gh,
    a real clock or the network; `sleep` is the only way time passes here, so a fake clock can run a 4-minute loop instantly."""
    gh: Callable[[list[str]], str] = run_gh
    http: Callable[..., tuple[int, Any]] = http_json
    clock: Callable[[], datetime] = utcnow
    sleep: Callable[[float], None] = time.sleep
    env: Any = field(default_factory=lambda: os.environ)


def gh_json(src: Sources, args: list[str]) -> Any:
    text = src.gh(args)
    try:
        return json.loads(text)
    except ValueError:
        raise Problem(f"gh {' '.join(args[:3])} didn't answer JSON: {text[:120]!r}") from None


# ── GitHub: which run, and how it went ────────────────────────────────────────────

def list_runs(src: Sources, repo: str, workflow: str = WORKFLOW, branch: str = BRANCH) -> list[dict]:
    runs = gh_json(src, ["run", "list", "--repo", repo, "--workflow", workflow, "--branch", branch, "--limit", "30",
                         "--json", RUN_FIELDS])
    return runs if isinstance(runs, list) else []


def pick_run(runs: list[dict], sha: str | None, since: datetime | None) -> dict | None:
    """The run this loop is about. `--sha`: the newest run of that commit. `--since`: the FIRST run created after it (the one the
    prompt caused; a run made before the prompt belongs to an earlier loop). Neither: the newest run. A push beats a manual
    dispatch of the same candidates, because only a push is "the push" the table's first row measures."""
    pool = [r for r in runs if parse_ts(r.get("createdAt"))]
    if sha:
        pool = [r for r in pool if str(r.get("headSha") or "").lower().startswith(sha.lower())]
    elif since:
        pool = [r for r in pool if parse_ts(r["createdAt"]) >= since - timedelta(seconds=SKEW_S)]
    pushes = [r for r in pool if r.get("event") == "push"]
    pool = pushes or pool
    if not pool:
        return None
    key = lambda r: parse_ts(r["createdAt"])          # noqa: E731
    return max(pool, key=key) if (sha or not since) else min(pool, key=key)


def view_run(src: Sources, repo: str, run_id: Any) -> dict:
    data = gh_json(src, ["run", "view", str(run_id), "--repo", repo, "--json", VIEW_FIELDS])
    if not isinstance(data, dict):
        raise Problem(f"gh run view {run_id}: unexpected answer")
    return data


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

    def seconds(self, now: datetime | None = None) -> float | None:
        if self.start and self.end:
            return (self.end - self.start).total_seconds()
        if self.open and self.start and now:
            return (now - self.start).total_seconds()
        return None


@dataclass
class Timeline:
    rows: list[Row]
    loop_start: datetime | None
    loop_start_is: str                 # "prompt sent" | "push (no --since)"
    total: Row
    critical_path: list[tuple[str, float]]
    serving_at: datetime | None        # the deploy step's end
    notes: list[str]
    meta: dict
    now: datetime


def _step_label(name: str) -> str:
    name = re.sub(r"^Run\s+", "", name)
    name = re.sub(r"^[\w.-]+/([\w.-]+@[\w.]+)", r"\1", name)       # google-github-actions/setup-gcloud@v2 -> setup-gcloud@v2
    return name if len(name) <= 30 else name[:27] + "..."


def _job_times(job: dict) -> tuple[datetime | None, datetime | None]:
    return parse_ts(job.get("startedAt")), parse_ts(job.get("completedAt"))


def critical_path(jobs: list[dict], serving_at: datetime | None) -> list[tuple[str, float]]:
    """The chain of jobs that decided when the deploy could start, found from the stamps alone (gh doesn't say what a job `needs`):
    a job starts when its LAST dependency ends, so its critical predecessor is the job that ended latest, at or before its start.
    The chain ends at the deploy job, cut at the moment the new revision serves."""
    timed = {j["name"]: _job_times(j) for j in jobs if j.get("conclusion") != "skipped"}
    deploy = timed.get(DEPLOY_JOB)
    if not deploy or not deploy[0]:
        return []
    chain = [(DEPLOY_JOB, deploy[0], serving_at or deploy[1])]
    cur_start = deploy[0]
    while True:
        before = [(end, name, start) for name, (start, end) in timed.items()
                  if name not in {c[0] for c in chain} and start and end and end <= cur_start + timedelta(seconds=1)]
        if not before:
            break
        end, name, start = max(before)
        chain.insert(0, (name, start, end))
        cur_start = start
    return [(LABELS.get(name, name) + (" (to serving)" if name == DEPLOY_JOB and serving_at else ""),
             (end - start).total_seconds()) for name, start, end in chain if end]


def build_timeline(run: dict, since: datetime | None, marks: dict[str, datetime], seen_at: datetime | None,
                   visible_why: str, now: datetime, portal: str = "") -> Timeline:
    push = parse_ts(run.get("createdAt"))
    event = str(run.get("event") or "push")
    jobs = [j for j in run.get("jobs") or [] if isinstance(j, dict) and j.get("name")]
    rows: list[Row] = []
    notes: list[str] = []

    # 1. prompt -> push, in as many pieces as the agent's marks allow
    pushed = "push" if event == "push" else f"run created ({event})"
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
        chain.append((pushed, push))
    if len(chain) < 2:                                            # only the push is known
        rows.append(Row("stage", "prompt_to_push", f"prompt -> {pushed}",
                        na="no --since: pass --since 'N minutes ago' (or an ISO time) to count from the prompt"))
    else:
        for (a, ta), (b, tb) in zip(chain, chain[1:]):
            rows.append(Row("stage", f"{a}_to_{b}".replace(" ", "_"), f"{a} -> {b}", ta, tb))

    # 2. queue: the run exists, then a runner picks up its first job
    starts = [s for s in (_job_times(j)[0] for j in jobs) if s]
    rows.append(Row("stage", "queue", "queue (runner pickup)", push, min(starts) if starts else None,
                    na="" if starts else "no job has started yet"))

    # 3. the jobs, in the order they started; the deploy job with its steps
    serving_at = None
    deploy_step_seen = False
    skipped = [j["name"] for j in jobs if j.get("conclusion") == "skipped"]
    for job in sorted((j for j in jobs if j.get("conclusion") != "skipped"),
                      key=lambda j: _job_times(j)[0] or datetime.max.replace(tzinfo=timezone.utc)):
        start, end = _job_times(job)
        verdict = job.get("conclusion") or ""
        label = LABELS.get(job["name"], job["name"])
        if not start:
            rows.append(Row("job", job["name"], label, na=f"not started ({job.get('status') or 'queued'})"))
            continue
        rows.append(Row("job", job["name"], label, start, end, note=("" if verdict in ("success", "") else verdict),
                        open=end is None))
        if job["name"] != DEPLOY_JOB:
            continue
        after_serving = False
        for step in job.get("steps") or []:
            if NOISE_STEP.fullmatch(step.get("name") or ""):
                continue
            s_start, s_end = parse_ts(step.get("startedAt")), parse_ts(step.get("completedAt"))
            if not s_start:
                continue
            note = ""
            if step.get("name") == DEPLOY_STEP:
                deploy_step_seen = True
                if step.get("conclusion") == "success" and s_end:
                    serving_at, note, after_serving = s_end, "new revision serving at the end of this step", True
                else:
                    note = step.get("conclusion") or "running"
            elif after_serving:
                note = "after the new revision was already serving"
            if step.get("conclusion") not in ("success", None, "") and step.get("name") != DEPLOY_STEP:
                note = step["conclusion"]
            rows.append(Row("step", f"{job['name']}.{step.get('number')}", _step_label(step.get("name") or "?"),
                            s_start, s_end, note, depth=1, open=s_end is None))
    if skipped:
        notes.append(f"not run (skipped): {', '.join(skipped)}")
    if not deploy_step_seen and any(j["name"] == DEPLOY_JOB for j in jobs):
        notes.append(f"the {DEPLOY_JOB} job has no {DEPLOY_STEP!r} step yet (or at all)")

    # 4. visible: the moment THIS machine first saw the pushed commit on /version
    if seen_at:
        lag = ""
        if serving_at:
            gap = (seen_at - serving_at).total_seconds()
            lag = (f"{fmt_dur(gap)} after the deploy step ended" if gap >= 0
                   else f"{fmt_dur(-gap)} BEFORE the deploy step's end stamp (traffic switches before the CLI returns)")
        rows.append(Row("visible", "visible", "visible on the portal", serving_at, seen_at,
                        note="first seen by this machine" + (f"; {lag}" if lag else "")))
    else:
        rows.append(Row("visible", "visible", "visible on the portal", na=visible_why or "not seen yet"))

    # 5. the total
    loop_start, loop_start_is = (since, "prompt sent") if since else (push, "push (no --since)")
    if loop_start and seen_at:
        total = Row("stage", "total", f"TOTAL {loop_start_is} -> visible", loop_start, seen_at)
    elif loop_start and serving_at:
        total = Row("stage", "total", f"TOTAL {loop_start_is} -> deploy step end", loop_start, serving_at,
                    note="visible was not observed, so this stops where the new revision started serving")
    else:
        total = Row("stage", "total", f"TOTAL {loop_start_is} -> visible", na="the loop has not finished (or the deploy never ran)")
    notes.append("rows overlap (jobs run in parallel): only the critical path adds up")
    meta = {"sha": run.get("headSha"), "run_id": run.get("databaseId"), "url": run.get("url"), "event": event,
            "status": run.get("status"), "conclusion": run.get("conclusion"), "title": run.get("displayTitle"),
            "push": iso(push), "portal": portal,
            "push_to_serving_s": round((serving_at - push).total_seconds(), 1) if push and serving_at else None}
    return Timeline(rows, loop_start, loop_start_is, total, critical_path(jobs, serving_at), serving_at, notes, meta, now)


def render(tl: Timeline, utc: bool = False, extra: list[str] | None = None) -> str:
    """The table, ASCII only (Git Bash on Windows prints it as is)."""
    rows = [*tl.rows, tl.total]
    label_w = max(len(r.label) + 2 * r.depth for r in rows)
    zone = "UTC" if utc else (datetime.now().astimezone().strftime("UTC%z") or "local")
    meta = tl.meta
    sha = str(meta.get("sha") or "")[:7]
    out = [f"loop {sha}  {str(meta.get('title') or '')[:70]}",
           f"run {meta.get('run_id')} ({meta.get('event')}, {meta.get('conclusion') or meta.get('status')})  {meta.get('url') or ''}",
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
        out += ["", f"critical path (from the stamps): {path}"
                + (f"   [push -> serving: {fmt_dur(whole)}, the rest is queueing between jobs]" if whole is not None else "")]
    for line in [*tl.notes, *(extra or [])]:
        out.append(f"* {line}")
    return "\n".join(out).encode("ascii", "replace").decode("ascii")      # a commit title may carry characters a Windows console can't print


def to_dict(tl: Timeline, extra: list[str] | None = None) -> dict:
    def one(r: Row) -> dict:
        sec = r.seconds(tl.now)
        return {"kind": r.kind, "key": r.key, "label": r.label, "depth": r.depth, "start": iso(r.start), "end": iso(r.end),
                "seconds": None if sec is None else round(sec, 1), "running": bool(r.open and not r.end),
                "note": r.note, "na": r.na or None}
    total = one(tl.total)
    return {**tl.meta, "loop_start": iso(tl.loop_start), "loop_start_is": tl.loop_start_is,
            "stages": [one(r) for r in tl.rows], "total": total,
            "serving_at": iso(tl.serving_at),
            "critical_path": [{"label": n, "seconds": round(s, 1)} for n, s in tl.critical_path],
            "notes": [*tl.notes, *(extra or [])]}


# ── following one loop ────────────────────────────────────────────────────────────

class Follow:
    """One loop, observed: finds the run, refreshes it, and polls /version. `step()` does one tick; `watch()` ticks until the
    loop is over. Everything slow goes through `src`, so a test drives it with a fake world and a fake clock."""

    def __init__(self, src: Sources, repo: str, portal: str, sha: str | None, since: datetime | None,
                 watch: bool, gh_every: float = 5.0, timeout: float = 1800.0, grace: float = 120.0, utc: bool = False,
                 workflow: str = WORKFLOW, branch: str = BRANCH):
        self.src, self.repo, self.portal = src, repo, portal.rstrip("/")
        self.workflow, self.branch = workflow, branch
        self.want, self.since, self.watching, self.utc = sha, since, watch, utc   # `want` may be an abbreviation; `sha` (below) is the full commit
        self.sha: str | None = None
        self.gh_every, self.timeout, self.grace = gh_every, timeout, grace
        self.run: dict | None = None
        self.seen_at: datetime | None = None
        self.already_live_at: datetime | None = None   # the new commit was already serving at the first look: its start can't be known
        self.version: dict | None = None
        self.first_probe: tuple[datetime, dict] | None = None
        self.baseline: tuple | None = None
        self.ended = ""                    # why watching stopped ("" = still going)
        self.failed = False
        self._last_gh: datetime | None = None
        self.gh_errors = 0
        self._run_id: Any = None
        self._announced: dict[str, str] = {}
        self.begun = src.clock()

    # -- GitHub --
    def refresh(self) -> None:
        """Re-read GitHub. A watch of several minutes rides out a dropped call or two (the portal's moment of truth is its own poll,
        not GitHub's); five in a row, or any failure without --watch, is a Problem."""
        try:
            self._refresh()
            self.gh_errors = 0
        except Problem:
            self.gh_errors += 1
            if not self.watching or self.gh_errors >= 5:
                raise

    def _refresh(self) -> None:
        """Find the run (once), then re-read it. Asking GitHub every few seconds only: with no run yet this is the wait for the push."""
        self._last_gh = self.src.clock()
        if self._run_id is None:
            found = pick_run(list_runs(self.src, self.repo, self.workflow, self.branch), self.want, self.since)
            if not found:
                return
            self._run_id, self.sha = found["databaseId"], str(found.get("headSha") or "")
        self.run = view_run(self.src, self.repo, self._run_id)

    def _deploy_step(self) -> dict | None:
        for job in (self.run or {}).get("jobs") or []:
            if job.get("name") == DEPLOY_JOB:
                for step in job.get("steps") or []:
                    if step.get("name") == DEPLOY_STEP:
                        return step
        return None

    def serving_at(self) -> datetime | None:
        step = self._deploy_step()
        return parse_ts(step.get("completedAt")) if step and step.get("conclusion") == "success" else None

    def _report(self, say: Callable[[str], None]) -> None:
        """While watching: one stderr line per job whose state changed, so a long wait isn't silent."""
        for job in (self.run or {}).get("jobs") or []:
            state = job.get("conclusion") or job.get("status") or ""
            if state and self._announced.get(job["name"]) != state:
                self._announced[job["name"]] = state
                say(f"[{fmt_clock(self.src.clock(), self.utc)}] {job['name']}: {state}")

    # -- the portal --
    def probe(self) -> None:
        now = self.src.clock()
        status, body = self.src.http(f"{self.portal}/version")
        if status != 200 or not isinstance(body, dict):
            return
        self.version = body
        if self.seen_at or self.already_live_at or not self.run:
            return
        ident = (body.get("revision"), body.get("started_at"))
        if self.first_probe is None:
            self.first_probe = (now, body)
            if body.get("commit") == self.sha:
                step = self._deploy_step()
                if step and parse_ts(step.get("startedAt")):
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
        run = self.run
        if self.seen_at:
            if self.serving_at() or (now - self.seen_at).total_seconds() > 30:
                return "visible"
            return ""                                   # seen; let GitHub's own stamps catch up so the lag row is real
        if self.already_live_at:
            return "already live"
        if run and run.get("status") == "completed":
            if run.get("conclusion") != "success":
                self.failed = True
                return f"the run ended {run.get('conclusion')}: the new version did not go live"
            ended = parse_ts(run.get("updatedAt")) or now
            if self.watching and (now - ended).total_seconds() > self.grace:
                self.failed = True
                return ("the run finished but /version never showed the new commit (deploy skipped by the gate? "
                        "wrong --portal? the version route not deployed yet?)")
        if self.watching and (now - self.begun).total_seconds() > self.timeout:
            self.failed = True
            return f"gave up after {fmt_dur(self.timeout)}"
        return ""

    def step(self, say: Callable[[str], None] = lambda _m: None) -> str:
        if self._last_gh is None or (self.src.clock() - self._last_gh).total_seconds() >= self.gh_every:
            self.refresh()
            if self.watching:
                self._report(say)
        if self.run is not None:
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
    """Without --watch: one look at GitHub and one at /version."""
    follow.refresh()
    if follow.run is not None:
        follow.probe()
        follow.ended = follow.done() or ("running" if follow.run.get("status") != "completed" else "")


# ── the command line ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None, src: Sources | None = None, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    src = src or Sources()
    ap = argparse.ArgumentParser(prog="loop_timer.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="when the prompt was sent: an ISO time, 'N minutes ago' or 'now' (default: the run's push)")
    ap.add_argument("--sha", help="the commit (full or abbreviated) whose deploy to time (default: the newest deploy run)")
    ap.add_argument("--watch", action="store_true", help="wait for the loop to finish (Ctrl-C prints what was seen)")
    ap.add_argument("--portal", default=PORTAL, help="the portal whose /version is polled (default: production)")
    ap.add_argument("--repo", help="owner/name (default: this checkout's, from `gh repo view`)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the table")
    ap.add_argument("--utc", action="store_true", help="show UTC instead of this machine's local time")
    ap.add_argument("--bridge", nargs="?", const=BRIDGE, metavar="URL",
                    help=f"also read the agent's marks from the Claude bridge (default {BRIDGE}; needs SKIPPY_HOME_TOKEN)")
    ap.add_argument("--bridge-task", help="the bridge run (task id) to read, instead of the newest before the push")
    ap.add_argument("--timeout", type=float, default=1800.0, help="--watch gives up after this many seconds (default 1800)")
    ap.add_argument("--workflow", default=WORKFLOW)
    ap.add_argument("--branch", default=BRANCH)
    args = ap.parse_args(argv)

    def say(message: str) -> None:
        print(message, file=err, flush=True)

    try:
        now = src.clock()
        since = parse_since(args.since, now) if args.since else None
        repo = args.repo or gh_json(src, ["repo", "view", "--json", "nameWithOwner"]).get("nameWithOwner")
        if not repo:
            raise Problem("couldn't tell which repository this is: pass --repo owner/name")
        follow = Follow(src, repo, args.portal, args.sha, since, args.watch, timeout=args.timeout, utc=args.utc,
                        workflow=args.workflow, branch=args.branch)
        if args.watch:
            say(f"watching {repo} {args.workflow}" + (f" for commit {args.sha}" if args.sha else "") + " (Ctrl-C prints what was seen)")
            follow.watch(say)
        else:
            run_once(follow)
        if follow.run is None:
            raise Problem("no deploy run found" + (f" for commit {args.sha}" if args.sha else "")
                          + (f" since {iso(since)}" if since and not args.sha else "")
                          + ": has the push happened? (--watch waits for it)")
        marks, extra = ({}, [])
        if args.bridge:
            marks, extra = bridge_marks(src, args.bridge, parse_ts(follow.run.get("createdAt")), since, args.bridge_task)
        tl = build_timeline(follow.run, since, marks, follow.seen_at, "" if follow.seen_at else follow.why_not_visible(),
                            src.clock(), follow.portal)
        extra = list(extra)
        if follow.version and follow.seen_at:
            v = follow.version
            extra.append(f"serving instance: revision {v.get('revision') or '?'}, image built {v.get('built_at') or '?'}, "
                         f"process started {v.get('started_at') or '?'} (UTC, from /version)")
        if follow.ended and follow.ended not in ("visible", "running"):
            extra.append(follow.ended)
    except Problem as problem:
        print(f"loop_timer: {problem}", file=err)
        return 2
    if args.json:
        print(json.dumps(to_dict(tl, extra), indent=2), file=out)
    else:
        print(render(tl, args.utc, extra), file=out)
    if follow.failed:
        return 1
    if args.watch:
        return 0 if follow.seen_at or follow.ended == "already live" else 1
    return 0 if follow.run.get("conclusion") in ("success", None, "") else 1


if __name__ == "__main__":
    sys.exit(main())
