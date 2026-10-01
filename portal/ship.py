"""The private Skippy's ship card: proof that what the agent pushed is live, not its word for it.

When a run pushed a commit, the bridge says so with a `ship` event (workshop.py ship_event). The chat turns it into a card
that asks this module every 10 s how that commit is doing, until the answer is final:

  * SkippyTel: the deploy run for THAT commit (recovery.GitHub.runs_for_sha, read only), its jobs grouped as
    tests -> build -> deploy, and green only when the portal itself serves the commit (buildinfo: this process's
    GIT_SHA; the card is answered by the new portal once the deploy switched traffic) or a later deploy that contains it
    is live (main only moves forward: the agent never force-pushes). Red with the run's link when the deploy failed,
    and the Admin page's Roll back one tap away.
  * Any other project: the commit's link and "pushed", plus a link to its deploy workflow when it has one. The portal's
    GitHub token can read only skippy-tel's Actions, so it can't watch another repo's runs.

Pure apart from the GitHub client and the clock, which are arguments; every text it returns is plain (the page sets it as
text) and every link is built here from the checked repo and sha, or is GitHub's own run link checked by recovery._run.
"""
from __future__ import annotations

import threading
import time

import recovery

POLL_S = 10                       # the page asks this often (and stops when `final`)
CACHE_S = 8                       # one GitHub look per commit per this long, however many tabs ask
ROLLBACK = "/admin#recovery"      # the Admin page's Recovery card: Roll back is one tap there
STAGES = (("tests", lambda name: name.startswith("test")), ("build", lambda name: name == "portal-image"),
          ("deploy", lambda name: name == "portal"))


def commit_url(ship: dict) -> str:
    return f"https://github.com/{ship['repo']}/commit/{ship['sha']}"


def stage_states(jobs: list[dict]) -> list[dict]:
    """[{name, state}] for tests, build, deploy from a run's jobs: waiting / queued / running / done / failed / skipped."""
    out = []
    for name, match in STAGES:
        mine = [j for j in jobs if match(j["name"])]
        if not mine:
            state = "waiting"
        elif any(j["conclusion"] in ("failure", "cancelled", "timed_out", "startup_failure") for j in mine):
            state = "failed"
        elif all(j["status"] == "completed" for j in mine):
            state = "skipped" if all(j["conclusion"] == "skipped" for j in mine) else "done"
        elif any(j["status"] == "in_progress" for j in mine):
            state = "running"
        else:
            state = "queued"
        out.append({"name": name, "state": state})
    return out


class ShipWatch:
    def __init__(self, github: recovery.GitHub, runs_cache: recovery.RunsCache, clock=time.monotonic):
        self.github, self.runs_cache, self.clock = github, runs_cache, clock
        self._seen: dict[str, tuple[float, list, list, str | None]] = {}     # sha -> (when, runs, jobs, error)
        self._lock = threading.Lock()

    def _look(self, sha: str) -> tuple[list, list, str | None]:
        with self._lock:
            hit = self._seen.get(sha)
            if hit and self.clock() - hit[0] < CACHE_S:
                return hit[1], hit[2], hit[3]
        runs, jobs, error = [], [], None
        try:
            runs = self.github.runs_for_sha(sha)
            if runs:
                jobs = self.github.run_jobs(runs[0]["id"])
        except recovery.GitHubError as exc:
            error = str(exc)
        with self._lock:
            self._seen[sha] = (self.clock(), runs, jobs, error)
            if len(self._seen) > 50:
                self._seen.pop(next(iter(self._seen)))
        return runs, jobs, error

    def _live_later(self, sha: str, live: str) -> bool:
        """Is a LATER deploy that contains `sha` the one serving? The deploy runs on main, newest first: the live commit's
        run is newer than ours and succeeded. (Pushes to main only move forward, so a later push contains ours.)"""
        if live == "dev" or live == sha:
            return False
        runs, _ = self.runs_cache.get()
        runs = runs or []
        ours = next((i for i, r in enumerate(runs) if r["head_sha"] == sha), None)
        theirs = next((i for i, r in enumerate(runs) if r["head_sha"] == live and r["conclusion"] == "success"), None)
        return ours is not None and theirs is not None and theirs < ours

    def status(self, ship: dict, live: str) -> dict:
        """What the card shows now. `live` is the commit this portal serves (buildinfo.info()["commit"])."""
        short = ship["sha"][:7]
        card = {"final": False, "tone": "wait", "headline": "", "note": "", "stages": [],
                "links": {"commit": commit_url(ship)}, "short": short, "subject": ship["subject"]}
        if ship["project"] != "skippytel" or ship["repo"] != recovery.REPO:
            card.update(final=True, tone="ok", headline=f"Pushed {short} to {ship['repo']} ({ship['branch']}).")
            if ship["workflow"]:
                card["note"] = (f"It has a deploy workflow ({ship['workflow']}); the portal can't read that repo's runs, "
                                "so follow it on GitHub.")
                card["links"]["workflow"] = f"https://github.com/{ship['repo']}/actions/workflows/{ship['workflow']}"
            else:
                card["note"] = "Pushed (no deploy pipeline here)."
            return card
        if live == ship["sha"]:
            card.update(final=True, tone="ok", headline=f"Live: the portal serves {short}.",
                        stages=[{"name": n, "state": "done"} for n, _ in STAGES])
            return card
        if not self.github.configured:
            card.update(headline=f"Pushed {short}. Waiting for the portal to serve it.",
                        note="The deploy's progress needs the portal's GITHUB_ACTIONS_TOKEN; this card still turns green "
                             "when the new version answers.")
            return card
        runs, jobs, error = self._look(ship["sha"])
        if error:
            card.update(headline=f"Pushed {short}. Can't see its deploy right now.", note=error)
            return card
        if not runs:
            card.update(headline=f"Pushed {short}. Waiting for GitHub to start its deploy…",
                        stages=[{"name": n, "state": "waiting"} for n, _ in STAGES])
            return card
        run = runs[0]
        card["links"]["run"] = run["html_url"]
        card["stages"] = stage_states(jobs)
        if self._live_later(ship["sha"], live):
            card.update(final=True, tone="ok", headline=f"Live: {short} went out with a later deploy ({live[:7]}).")
            return card
        word = recovery.state_word(run)
        if word == "failed":
            failed = next((s["name"] for s in card["stages"] if s["state"] == "failed"), "")
            card.update(final=True, tone="bad", headline=f"The deploy of {short} failed" + (f" at {failed}." if failed else "."),
                        note="Not live. Its log is on GitHub; Roll back is on the Admin page if the portal is broken.")
            card["links"]["rollback"] = ROLLBACK
            return card
        if word == "cancelled":
            newer = (self.runs_cache.get()[0] or [])
            ours = next((i for i, r in enumerate(newer) if r["head_sha"] == ship["sha"]), None)
            if ours:                                  # a later push is in the queue: ours goes out with it
                card.update(headline=f"{short}'s deploy was cancelled by a later push; it goes live with that one.")
                return card
            card.update(final=True, tone="bad", headline=f"The deploy of {short} was cancelled.",
                        note="Not live. Push again or re-run it on GitHub.")
            card["links"]["rollback"] = ROLLBACK
            return card
        if word == "passed":
            card.update(headline=f"Deployed {short}. Waiting for the new version to answer…")
            return card
        stage = next((s["name"] for s in card["stages"] if s["state"] in ("running", "queued")), "")
        card.update(headline=f"Deploying {short}" + (f": {stage}…" if stage else "…"))
        return card
