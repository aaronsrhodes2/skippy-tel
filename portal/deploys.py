"""The house's own deploys (docs/no-github.md, phase 3): skippy-core builds and deploys the portal (core/cd/cd.py) and reports each
deploy here; this object answers the portal's ship cards and Recovery exactly as recovery.GitHub did, from those reports instead
of GitHub's API. Nothing here reaches out: skippy-core POSTs /home/deploys (the home token) about once a minute with its recent
runs, and takes back what Recovery asked of it (deploy main again, roll back, re-run, cancel).

A run, as skippy-core sends it and as the cards read it (the shape recovery._run gave GitHub's runs):
    {"id": int, "head_sha": 40 hex, "status": "queued" | "in_progress" | "completed", "conclusion": "success" | "failure" | "cancelled" | "",
     "created_at": ISO, "updated_at": ISO, "title": str, "jobs": [{"name", "status", "conclusion"}]}
Kept in memory (the newest MAX_RUNS); skippy-core sends its whole recent list every time, so a restarted portal has it back within
a minute.
"""
from __future__ import annotations

import itertools
import re
import threading
import time
from datetime import datetime

import recovery

MAX_RUNS = 30
MAX_REQUESTS = 10
STALE_AFTER_S = 300           # no report from skippy-core this long: the cards say so instead of pretending to know
_SHA = re.compile(r"[0-9a-f]{40}")
_OPS = ("deploy", "rollback", "rerun", "cancel")


def _when(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def clean_run(raw) -> dict | None:
    """One run from skippy-core, checked for shape (it comes from whatever holds the home token): anything odd is dropped."""
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), int) or raw["id"] <= 0:
        return None
    sha = str(raw.get("head_sha") or "")
    created = _when(raw.get("created_at"))
    if not _SHA.fullmatch(sha) or not created:
        return None
    jobs = [{"name": str(j.get("name") or "")[:80], "status": str(j.get("status") or "")[:20],
             "conclusion": str(j.get("conclusion") or "")[:20]} for j in (raw.get("jobs") or [])[:20] if isinstance(j, dict)]
    return {"id": raw["id"], "status": str(raw.get("status") or "")[:20], "conclusion": str(raw.get("conclusion") or "")[:20],
            "head_sha": sha, "short": sha[:7], "created_at": created, "updated_at": _when(raw.get("updated_at")) or created,
            "html_url": "", "title": " ".join(str(raw.get("title") or "").split())[:90], "event": "push", "jobs": jobs}


class HouseDeploys:
    """recovery.GitHub's methods, answered from skippy-core's reports."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._runs: list[dict] = []
        self._at: float | None = None
        self._requests: list[dict] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"HouseDeploys(runs={len(self._runs)})"

    # ── skippy-core's side (POST /home/deploys) ─────────────────────────────────
    def report(self, payload: dict) -> dict:
        """skippy-core's runs in; the requests it hasn't done yet out. `done`: the request ids it has carried out."""
        runs = [r for r in (clean_run(x) for x in (payload.get("runs") or [])[:MAX_RUNS]) if r]
        runs.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        done = {d for d in payload.get("done") or [] if isinstance(d, int)}
        with self._lock:
            self._runs, self._at = runs, self.clock()
            self._requests = [q for q in self._requests if q["id"] not in done]
            return {"requests": [dict(q) for q in self._requests]}

    def _ask(self, op: str, arg: str = "") -> None:
        with self._lock:
            if any(q["op"] == op and q["arg"] == arg for q in self._requests):
                return                                    # asked already: once is enough
            if len(self._requests) >= MAX_REQUESTS:
                raise recovery.GitHubError("skippy-core has too many requests waiting already; it isn't picking them up.")
            self._requests.append({"id": next(self._ids), "op": op, "arg": arg, "at": self.clock()})

    # ── what the cards call (recovery.GitHub's names) ───────────────────────────
    @property
    def configured(self) -> bool:
        return self._at is not None and self.clock() - self._at < STALE_AFTER_S

    def _check(self) -> None:
        if self._at is None:
            raise recovery.GitHubError("skippy-core hasn't reported its deploys yet.")
        if not self.configured:
            raise recovery.GitHubError(f"skippy-core hasn't reported its deploys for {int((self.clock() - self._at) / 60)} min.")

    def deploy_runs(self, limit: int = MAX_RUNS) -> list[dict]:
        self._check()
        with self._lock:
            return [dict(r) for r in self._runs[:limit]]

    def run_jobs(self, run_id) -> list[dict]:
        self._check()
        with self._lock:
            run = next((r for r in self._runs if str(r["id"]) == str(run_id)), None)
            return [dict(j) for j in run["jobs"]] if run else []

    def runs_for_sha(self, sha: str) -> list[dict]:
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise recovery.GitHubError("That isn't a commit id.")
        self._check()
        with self._lock:
            return [dict(r) for r in self._runs if r["head_sha"] == sha][:5]

    def dispatch_deploy(self) -> None:
        self._ask("deploy")

    def dispatch_rollback(self, revision: str = "") -> None:
        value = recovery.valid_revision(revision)
        if value is None:
            raise recovery.GitHubError("That isn't a revision name: leave it blank, type latest, or a name like skippy-portal-00042-abc.")
        self._ask("rollback", value)

    def rerun_failed(self, run_id) -> None:
        self._ask("rerun", str(int(run_id)))

    def cancel(self, run_id) -> None:
        self._ask("cancel", str(int(run_id)))
