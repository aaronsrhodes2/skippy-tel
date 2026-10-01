"""tools/loop_timer.py: the table, the arithmetic, and the watching, against a fake world.

GitHub is a function (the `gh` seam returns what `gh run list|view --json` prints, computed from a fake clock), /version is another
function, and `sleep` only moves the fake clock: a four-minute deploy plays out in milliseconds, and every moment the timer reports
can be compared with the moment the scenario put it at. The real urllib path is exercised against a real HTTP server on an ephemeral port.
"""
from __future__ import annotations

import http.server
import io
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import loop_timer as lt

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
PUSH = datetime(2026, 9, 30, 19, 0, 0, tzinfo=UTC)         # the moment the push lands (the run's createdAt)
SHA = "8adcd7ee35c3bf0096bb2f95babb6db113eb5ef6"
OLD_SHA = "0123456789abcdef0123456789abcdef01234567"
ZERO = "0001-01-01T00:00:00Z"                              # how GitHub says "has not happened"
PORTAL = "https://portal.test"


def at(seconds: float) -> datetime:
    return PUSH + timedelta(seconds=seconds)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Job:
    name: str
    start: float                       # seconds after the push
    end: float
    steps: tuple = ()                  # (number, name, start, end)
    ok: str = "success"
    skipped: bool = False
    fail_step: str = ""                # the step that fails (the job then ends there)


def standard_jobs() -> list[Job]:
    """deploy.yml as measured on 2026-09-30 (docs/cicd.md): the deploy job starts when `test` ends; Deploy to Cloud Run ends at +190."""
    return [
        Job("gate", 2, 5),
        Job("mentat-test", 2, 40),
        Job("test", 2, 104),
        Job("infra", 106, 126),
        Job("mentat", 106, 236),
        Job("portal", 106, 197, steps=(
            (1, "Set up job", 106, 107), (2, "Run actions/checkout@v4", 107, 108),
            (3, "Run google-github-actions/auth@v2", 108, 108),
            (4, "Run google-github-actions/setup-gcloud@v2", 108, 131), (5, "Build and push", 131, 165),
            (6, "Deploy to Cloud Run", 165, 190), (7, "Smoke test", 190, 197),
            (8, "Post Run actions/checkout@v4", 197, 197), (9, "Complete job", 197, 197))),
    ]


@dataclass
class World:
    """One deploy loop on a fake clock. `now` only moves through sleep(); everything GitHub and the portal say is computed from it."""
    now: datetime
    jobs: list[Job] = field(default_factory=standard_jobs)
    visible_at: float | None = 191                      # seconds after the push when /version starts answering the new commit
    event: str = "push"
    run_id: int = 4242
    earlier: list[dict] = field(default_factory=list)   # older runs, newest first
    version_before: dict | None = None                  # what /version says before the flip (default: the old commit)
    version_down: bool = False
    bridge: dict = field(default_factory=dict)          # path -> (status, body)
    gh_calls: list = field(default_factory=list)
    http_calls: list = field(default_factory=list)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)

    def sources(self, env=None) -> lt.Sources:
        return lt.Sources(gh=self.gh, http=self.http, clock=self.clock, sleep=self.sleep, env=env if env is not None else {})

    # -- GitHub --
    def state(self, start, end, ok="success", skipped=False):
        if skipped:
            return {"status": "completed", "conclusion": "skipped", "startedAt": ZERO, "completedAt": ZERO}
        if self.now < at(start):
            return {"status": "queued", "conclusion": "", "startedAt": ZERO, "completedAt": ZERO}
        if self.now < at(end):
            return {"status": "in_progress", "conclusion": "", "startedAt": stamp(at(start)), "completedAt": ZERO}
        return {"status": "completed", "conclusion": ok, "startedAt": stamp(at(start)), "completedAt": stamp(at(end))}

    def run_summary(self):
        return {"databaseId": self.run_id, "headSha": SHA, "createdAt": stamp(PUSH), "startedAt": stamp(PUSH), "event": self.event,
                "status": "completed", "conclusion": "success", "url": f"https://github.test/runs/{self.run_id}",
                "displayTitle": "Portal: a change", "updatedAt": stamp(at(240))}

    def view(self):
        jobs = []
        for n, job in enumerate(self.jobs, 1):
            row = {"name": job.name, "databaseId": n, "url": f"https://github.test/jobs/{n}",
                   **self.state(job.start, job.end, job.ok, job.skipped), "steps": []}
            for number, name, s, e in job.steps:
                failed = job.fail_step == name
                row["steps"].append({"number": number, "name": name, **self.state(s, e, "failure" if failed else "success")})
            jobs.append(row)
        live = [j for j in self.jobs if not j.skipped]
        done = self.now >= at(max(j.end for j in live))
        verdict = "failure" if any(j.ok != "success" for j in live) else "success"
        return {**self.run_summary(), "jobs": jobs, "status": "completed" if done else "in_progress",
                "conclusion": verdict if done else "", "updatedAt": stamp(min(self.now, at(max(j.end for j in live))))}

    def gh(self, args):
        self.gh_calls.append(list(args))
        if args[:2] == ["repo", "view"]:
            return json.dumps({"nameWithOwner": "owner/repo"})
        if args[:2] == ["run", "list"]:
            runs = list(self.earlier)
            if self.now >= PUSH:
                runs.insert(0, self.run_summary())
            return json.dumps(runs)
        if args[:2] == ["run", "view"]:
            assert args[2] == str(self.run_id)
            return json.dumps(self.view())
        raise AssertionError(f"unexpected gh call {args}")

    # -- the portal and the bridge --
    def http(self, url, headers=None, timeout=5.0):
        self.http_calls.append(url)
        if url == f"{PORTAL}/version":
            if self.version_down:
                return 0, None
            if self.visible_at is not None and self.now >= at(self.visible_at):
                return 200, {"commit": SHA, "short": SHA[:7], "built_at": stamp(at(100)),
                             "revision": "skippy-portal-00043-new", "started_at": stamp(at(186))}
            return 200, self.version_before or {"commit": OLD_SHA, "short": OLD_SHA[:7], "built_at": "", "revision": "skippy-portal-00042-old",
                                                "started_at": stamp(at(-3600))}
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        return self.bridge.get(path, (404, None))


def run_main(world, *argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    code = lt.main(["--portal", PORTAL, "--utc", *argv], world.sources(env), out, err)
    return code, out.getvalue(), err.getvalue()


def stages(text: str) -> dict:
    return json.loads(text)


def by_key(doc) -> dict:
    return {s["key"]: s for s in doc["stages"]}


# ── pure helpers ────────────────────────────────────────────────────────────────────

def test_github_times_parse_to_utc_and_never_started_is_none():
    assert lt.parse_ts("2026-09-30T19:03:12Z") == datetime(2026, 9, 30, 19, 3, 12, tzinfo=UTC)
    assert lt.parse_ts("2026-09-30T12:03:12-07:00") == datetime(2026, 9, 30, 19, 3, 12, tzinfo=UTC)
    assert lt.parse_ts(1_790_000_000) == datetime.fromtimestamp(1_790_000_000, UTC)
    for nothing in (ZERO, "", None, "garbage", 0):
        assert lt.parse_ts(nothing) is None


def test_since_takes_iso_relative_and_now():
    now = datetime(2026, 9, 30, 19, 30, tzinfo=UTC)
    assert lt.parse_since("12 minutes ago", now) == now - timedelta(minutes=12)
    assert lt.parse_since("90s ago", now) == now - timedelta(seconds=90)
    assert lt.parse_since("2 hours ago", now) == now - timedelta(hours=2)
    assert lt.parse_since("1.5 min ago", now) == now - timedelta(seconds=90)
    assert lt.parse_since("now", now) == now
    assert lt.parse_since("2026-09-30T19:03:12Z", now) == datetime(2026, 9, 30, 19, 3, 12, tzinfo=UTC)
    with pytest.raises(lt.Problem, match="--since"):
        lt.parse_since("when the moon is full", now)


def test_durations_read_at_a_glance():
    assert [lt.fmt_dur(s) for s in (0, 3, 59.6, 102, 3600 + 120)] == ["0s", "3s", "1m00s", "1m42s", "1h02m"]
    assert lt.fmt_dur(-4) == "-4s" and lt.fmt_dur(None) == ""


# ── one finished loop, read after the fact ──────────────────────────────────────────

def test_a_finished_loop_is_a_table_of_stamps_with_the_critical_path_and_the_total():
    world = World(now=at(300))
    code, out, err = run_main(world, "--since", "2026-09-30T18:57:00Z")
    assert code == 0 and err == ""
    rows = {line.split("  ")[0].strip(): line for line in out.splitlines()}
    assert re.search(r"18:57:00\s+19:00:00\s+3m00s", rows["prompt -> push"])                    # 180 s from the prompt to the push
    assert "queue (runner pickup)" in rows and re.search(r"19:00:00\s+19:00:02\s+2s", rows["queue (runner pickup)"])
    assert re.search(r"19:00:02\s+19:00:05\s+3s", rows["gate (bootstrapped?)"])
    assert re.search(r"19:00:02\s+19:01:44\s+1m42s", rows["tests"])
    assert re.search(r"19:01:46\s+19:03:17\s+1m31s", rows["deploy portal"])
    deploy = [l for l in out.splitlines() if "Deploy to Cloud Run" in l][0]
    assert re.search(r"19:02:45\s+19:03:10\s+25s\s+new revision serving at the end of this step", deploy)
    assert "after the new revision was already serving" in [l for l in out.splitlines() if "Smoke test" in l][0]
    assert "Set up job" not in out and "Complete job" not in out and "Post Run" not in out          # bookkeeping steps are noise
    total = [l for l in out.splitlines() if l.startswith("TOTAL")][0]
    assert "prompt sent -> deploy step end" in total and re.search(r"18:57:00\s+19:03:10\s+6m10s", total)
    assert "critical path (from the stamps): tests 1m42s -> deploy portal (to serving) 1m24s" in out
    assert "[push -> serving: 3m10s" in out                              # the headline number: push to the new revision serving
    assert "rows overlap" in out


def test_visible_is_not_guessed_when_nobody_was_watching():
    """The new commit is already live when the timer first looks, so WHEN it appeared is unknowable: n/a with the reason, not a number."""
    world = World(now=at(300))
    code, out, _ = run_main(world, "--since", "2026-09-30T18:57:00Z")
    visible = [l for l in out.splitlines() if l.startswith("visible on the portal")][0]
    assert "n/a (already live when first checked at 19:05:00" in visible and "loop-watch" in visible
    assert not re.search(r"\d\d:\d\d:\d\d", visible.split("n/a")[0])


def test_without_since_the_prompt_stage_is_n_a_and_the_total_counts_from_the_push():
    world = World(now=at(300))
    code, out, _ = run_main(world)
    assert re.search(r"prompt -> push\s+n/a \(no --since", out)
    assert "TOTAL push (no --since) -> deploy step end" in out and "3m10s" in out


def test_json_carries_the_same_stages_in_utc_iso():
    world = World(now=at(300))
    code, out, _ = run_main(world, "--json", "--since", "2026-09-30T18:57:00Z")
    doc = json.loads(out)
    stage = by_key(doc)
    assert doc["sha"] == SHA and doc["run_id"] == 4242 and doc["serving_at"] == "2026-09-30T19:03:10Z"
    assert stage["prompt_to_push"]["seconds"] == 180 and stage["queue"]["seconds"] == 2
    assert stage["tests" if "tests" in stage else "test"]["seconds"] == 102 and stage["portal"]["seconds"] == 91
    assert stage["portal.6"]["start"] == "2026-09-30T19:02:45Z" and stage["portal.6"]["end"] == "2026-09-30T19:03:10Z"
    assert doc["total"]["seconds"] == 370 and doc["total"]["start"] == "2026-09-30T18:57:00Z"
    assert [c["label"] for c in doc["critical_path"]] == ["tests", "deploy portal (to serving)"]
    assert stage["visible"]["na"] and stage["visible"]["seconds"] is None


def test_the_table_is_plain_ascii_even_when_a_commit_title_is_not():
    world = World(now=at(300))
    run = world.run_summary()
    world.run_summary = lambda: {**run, "displayTitle": "Game pages \u2026 the \u2713 mark"}       # what a Windows console would choke on
    code, out, _ = run_main(world)
    out.encode("ascii")
    assert "Game pages" in out


def test_jobs_that_were_skipped_are_one_note_not_rows():
    world = World(now=at(300))
    world.jobs = [j for j in standard_jobs() if j.name not in ("mentat", "mentat-test")] + [
        Job("mentat-test", 0, 0, skipped=True), Job("mentat", 0, 0, skipped=True)]
    code, out, _ = run_main(world)
    assert "not run (skipped): mentat-test, mentat" in out
    assert "mentat tests" not in out and "mentat deploy" not in out


# ── watching ───────────────────────────────────────────────────────────────────────

def test_watching_from_before_the_push_sees_the_version_the_second_it_flips():
    world = World(now=at(-30))
    code, out, err = run_main(world, "--watch", "--since", "now")
    assert code == 0
    doc_rows = out.splitlines()
    visible = [l for l in doc_rows if l.startswith("visible on the portal")][0]
    assert re.search(r"19:03:10\s+19:03:11\s+1s\s+first seen by this machine; 1s after the deploy step ended", visible)
    total = [l for l in doc_rows if l.startswith("TOTAL")][0]
    assert "prompt sent -> visible" in total and re.search(r"18:59:30\s+19:03:11\s+3m41s", total)
    assert "serving instance: revision skippy-portal-00043-new" in out and "image built 2026-09-30T19:01:40Z" in out
    assert "watching owner/repo" in err and "gate: success" in err and "portal: success" not in err.replace("in_progress", "")   # progress goes to stderr
    runs_listed = [c for c in world.gh_calls if c[:2] == ["run", "list"]]
    assert 0 < len(runs_listed) < 60                                  # GitHub is asked every few seconds, not every second
    assert sum(u.endswith("/version") for u in world.http_calls) > 150  # the portal is asked every second once the run exists


def test_a_redeploy_of_the_same_commit_waits_for_the_new_revision_not_the_old_one():
    old = {"commit": SHA, "short": SHA[:7], "built_at": "", "revision": "skippy-portal-00042-old", "started_at": stamp(at(-3600))}
    world = World(now=at(-5), version_before=old, visible_at=195)
    code, out, _ = run_main(world, "--watch", "--since", "now")
    visible = [l for l in out.splitlines() if l.startswith("visible on the portal")][0]
    assert code == 0 and re.search(r"19:03:10\s+19:03:15\s+5s", visible)       # the flip, not the first look


def test_a_failed_run_ends_the_watch_with_the_reason_and_status_1():
    world = World(now=at(-5), visible_at=None)
    world.jobs = [j for j in standard_jobs() if j.name != "portal"] + [
        Job("portal", 106, 170, ok="failure", fail_step="Build and push", steps=(
            (1, "Set up job", 106, 107), (5, "Build and push", 131, 170)))]
    code, out, err = run_main(world, "--watch", "--since", "now")
    assert code == 1
    assert "the run ended failure: the new version did not go live" in out
    assert re.search(r"visible on the portal\s+n/a", out) and "TOTAL prompt sent -> visible" in out and "n/a (the loop has not finished" in out
    assert re.search(r"Build and push.*failure", out)


def test_a_run_that_finishes_without_the_version_ever_changing_is_reported_not_waited_on_forever():
    world = World(now=at(-5), visible_at=None)
    code, out, _ = run_main(world, "--watch", "--since", "now")
    assert code == 1 and "never showed the new commit" in out
    assert "reports 0123456, not 8adcd7e" in out                       # says what /version DID show


def test_ctrl_c_prints_what_was_seen_so_far():
    world = World(now=at(-5))
    ticks = {"n": 0}

    def sleepy(seconds):
        ticks["n"] += 1
        if ticks["n"] > 120:
            raise KeyboardInterrupt
        world.sleep(seconds)

    src = world.sources()
    src.sleep = sleepy
    out, err = io.StringIO(), io.StringIO()
    code = lt.main(["--portal", PORTAL, "--utc", "--watch", "--since", "now"], src, out, err)
    assert code == 1 and "interrupted (Ctrl-C)" in out.getvalue() + err.getvalue()
    assert "tests" in out.getvalue()                                  # the stages known so far are there
    assert re.search(r"visible on the portal\s+n/a", out.getvalue())


def test_a_dropped_github_call_does_not_end_a_watch_but_five_in_a_row_do():
    world = World(now=at(-5))
    real, state = world.gh, {"n": 0}

    def flaky(args):
        if args[:2] == ["run", "view"]:
            state["n"] += 1
            if state["n"] in (2, 3):                                  # two bad answers in the middle of the run
                raise lt.Problem("gh run view failed: HTTP 502")
        return real(args)

    world.gh = flaky
    code, out, err = run_main(world, "--watch", "--since", "now")
    assert code == 0 and re.search(r"visible on the portal\s+19:03:10\s+19:03:11", out)       # the moment is the portal's own, not GitHub's

    world = World(now=at(-5))
    real = world.gh
    world.gh = lambda args: (_ for _ in ()).throw(lt.Problem("gh run view failed: HTTP 502")) if args[:2] == ["run", "view"] else real(args)
    code, out, err = run_main(world, "--watch", "--since", "now")
    assert code == 2 and "HTTP 502" in err


def test_watching_with_no_push_gives_up_at_the_timeout():
    world = World(now=at(-5))
    world.run_summary = lambda: (_ for _ in ()).throw(AssertionError("no run exists"))
    world.gh = lambda args: json.dumps({"nameWithOwner": "o/r"} if args[:2] == ["repo", "view"] else [])
    code, out, err = run_main(world, "--watch", "--since", "now", "--timeout", "60")
    assert code == 2 and "no deploy run found" in err


# ── which run ────────────────────────────────────────────────────────────────────────

def runs(*specs):
    return [{"databaseId": i, "headSha": sha, "createdAt": stamp(created), "startedAt": stamp(created), "event": event,
             "status": "completed", "conclusion": "success", "url": "u", "displayTitle": "t"} for i, sha, created, event in specs]


def test_since_picks_the_first_run_after_the_prompt_and_never_an_earlier_loops():
    pool = runs((1, "a" * 40, at(-3000), "push"), (3, "c" * 40, at(500), "push"), (2, "b" * 40, at(40), "push"))
    assert lt.pick_run(pool, None, at(0))["databaseId"] == 2            # the earliest run created after the prompt
    assert lt.pick_run(pool, None, at(46))["databaseId"] == 3           # run 2 (at +40) is the previous loop's by now
    assert lt.pick_run(pool, None, at(520)) is None                     # the push hasn't happened yet
    assert lt.pick_run(pool, None, at(44))["databaseId"] == 2           # a few seconds of clock skew (this machine vs GitHub) are forgiven


def test_sha_picks_that_commit_by_prefix_and_the_default_is_the_newest():
    pool = runs((1, "abc" + "0" * 37, at(-100), "push"), (2, "abd" + "0" * 37, at(0), "push"), (3, "abc" + "0" * 37, at(50), "workflow_dispatch"))
    assert lt.pick_run(pool, "abd", None)["databaseId"] == 2
    assert lt.pick_run(pool, "ABC", None)["databaseId"] == 1            # a push beats a manual re-run of the same commit
    assert lt.pick_run(pool, "zzz", None) is None
    assert lt.pick_run(pool, None, None)["databaseId"] == 2             # newest push
    assert lt.pick_run([], None, None) is None


def test_the_workflow_and_branch_flags_reach_github():
    world = World(now=at(300))
    run_main(world, "--workflow", "other.yml", "--branch", "feature")
    listing = [c for c in world.gh_calls if c[:2] == ["run", "list"]][0]
    assert listing[listing.index("--workflow") + 1] == "other.yml" and listing[listing.index("--branch") + 1] == "feature"
    assert listing[listing.index("--repo") + 1] == "owner/repo"          # from `gh repo view`, since --repo wasn't given


def test_a_manual_dispatch_is_labelled_as_what_it_is():
    world = World(now=at(300), event="workflow_dispatch")
    code, out, _ = run_main(world, "--since", "2026-09-30T18:57:00Z")
    assert "prompt -> run created (workflow_dispatch)" in out


def test_no_run_is_a_clear_message_and_status_2():
    world = World(now=at(-5))
    code, out, err = run_main(world, "--sha", "8adcd7e")
    assert code == 2 and "no deploy run found for commit 8adcd7e" in err and out == ""


def test_gh_trouble_is_a_message_not_a_traceback():
    def broken(args):
        raise lt.Problem("gh run list failed: not logged in (signed in? `gh auth status`)")
    src = lt.Sources(gh=broken, clock=lambda: PUSH, sleep=lambda s: None, env={})
    out, err = io.StringIO(), io.StringIO()
    assert lt.main(["--repo", "o/r"], src, out, err) == 2 and "gh run list failed" in err.getvalue()


# ── the critical path, from the stamps ───────────────────────────────────────────────

def job(name, start, end, conclusion="success"):
    return {"name": name, "startedAt": stamp(at(start)), "completedAt": stamp(at(end)), "conclusion": conclusion}


def test_the_critical_path_follows_whichever_job_the_deploy_waited_for():
    split = [job("gate", 0, 4), job("test", 2, 104), job("test-flask-api", 2, 14), job("portal-image", 4, 60), job("portal", 105, 200)]
    assert [n for n, _ in lt.critical_path(split, at(190))] == ["tests", "deploy portal (to serving)"]
    slow_build = [job("gate", 0, 4), job("test", 2, 60), job("test-flask-api", 2, 14), job("portal-image", 4, 120), job("portal", 121, 200)]
    assert [n for n, _ in lt.critical_path(slow_build, at(190))] == ["gate (bootstrapped?)", "build portal image", "deploy portal (to serving)"]
    chained = [job("gate", 0, 4), job("test", 5, 100), job("portal", 101, 200)]                  # a job that waited for another
    assert [n for n, _ in lt.critical_path(chained, at(190))] == ["gate (bootstrapped?)", "tests", "deploy portal (to serving)"]


def test_the_critical_path_ignores_skipped_jobs_and_a_missing_deploy_job():
    assert lt.critical_path([job("test", 0, 90)], None) == []
    skipped = [job("mentat", 0, 0, "skipped"), job("test", 2, 90), job("portal", 91, 150)]
    assert [n for n, _ in lt.critical_path(skipped, at(140))] == ["tests", "deploy portal (to serving)"]
    seconds = dict(lt.critical_path(skipped, at(140)))
    assert seconds["tests"] == 88 and seconds["deploy portal (to serving)"] == 49          # cut where the revision serves, not at the job's end


# ── the agent's marks, through the bridge (optional) ─────────────────────────────────

def bridge_world(events, listing=None):
    world = World(now=at(300))
    world.bridge = {"/workshop/runs": (200, {"runs": listing if listing is not None else [{"task": "t1", "age_s": 470.0}]}),     # created at -170 s
                    "/workshop/run/t1?after=0": (200, {"events": events, "next": len(events), "done": True})}
    return world


def test_bridge_marks_split_prompt_to_push_when_the_events_carry_ts():
    events = [{"t": "text", "text": "ok", "ts": at(-170).timestamp()},
              {"t": "tool", "name": "Read", "detail": "a.py", "ts": at(-160).timestamp()},
              {"t": "tool", "name": "Edit", "detail": "portal/app.py", "ts": stamp(at(-120))},
              {"t": "tool", "name": "Edit", "detail": "portal/b.py", "ts": stamp(at(-100))},
              {"t": "tool", "name": "Bash", "detail": "git push origin main", "ts": stamp(at(-2))}]
    world = bridge_world(events)
    code, out, _ = run_main(world, "--bridge", "--since", "2026-09-30T18:57:00Z", env={"SKIPPY_HOME_TOKEN": "t0ken"})
    labels = [l.split("  ")[0].strip() for l in out.splitlines()]
    assert labels[labels.index("prompt -> bridge received"):labels.index("queue (runner pickup)")] == [
        "prompt -> bridge received", "bridge received -> first edit", "first edit -> git push", "git push -> push"]
    assert "prompt -> push" not in labels
    first = [l for l in out.splitlines() if l.startswith("bridge received -> first edit")][0]
    assert re.search(r"18:57:10\s+18:58:00\s+50s", first)      # created = the reply's time (19:05:00) minus the bridge's own age_s (470 s)


def test_bridge_without_a_token_or_without_ts_says_so_and_keeps_the_single_row():
    world = bridge_world([{"t": "tool", "name": "Edit", "detail": "x"}])
    code, out, _ = run_main(world, "--bridge", "--since", "2026-09-30T18:57:00Z", env={})
    assert "bridge: n/a (SKIPPY_HOME_TOKEN isn't set" in out and re.search(r"^prompt -> push\s+18:57:00", out, re.M)
    code, out, _ = run_main(world, "--bridge", "--since", "2026-09-30T18:57:00Z", env={"SKIPPY_HOME_TOKEN": "t0ken"})
    assert "bridge: first edit / git push n/a (the bridge's events carry no ts yet)" in out
    assert "bridge received -> " not in out or "prompt -> bridge received" in out


def test_the_token_is_sent_to_the_bridge_and_never_printed():
    world = bridge_world([])
    seen = []
    real = world.http
    world.http = lambda url, headers=None, timeout=5.0: (seen.append(headers), real(url, headers, timeout))[1]
    code, out, err = run_main(world, "--bridge", env={"SKIPPY_HOME_TOKEN": "sentinel-token-value"})
    assert {"Authorization": "Bearer sentinel-token-value"} in [h for h in seen if h]
    assert "sentinel-token-value" not in out + err


def test_bridge_marks_outside_the_prompt_to_push_window_are_dropped():
    events = [{"t": "tool", "name": "Bash", "detail": "git push", "ts": stamp(at(60))}]          # after the run was created: not this loop's
    world = bridge_world(events)
    code, out, _ = run_main(world, "--bridge", "--since", "2026-09-30T18:57:00Z", env={"SKIPPY_HOME_TOKEN": "t"})
    assert "ignored bridge marks outside the prompt..push window: git push" in out
    assert re.search(r"^prompt -> bridge received", out, re.M)                                   # the good mark stays


def test_an_unreachable_bridge_is_a_note_not_a_failure():
    world = World(now=at(300))                                         # no bridge routes: 404
    code, out, _ = run_main(world, "--bridge", env={"SKIPPY_HOME_TOKEN": "t"})
    assert code == 0 and "bridge: n/a (http://localhost:9337/workshop/runs answered 404" in out


# ── the real urllib path, against a real server ───────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):  # noqa: N802
        route = self.routes.get(self.path, (404, b"{}"))
        status, body = route() if callable(route) else route          # a callable answers from the fake clock at request time
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Handler.routes = {}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", Handler.routes
    httpd.shutdown()
    httpd.server_close()


def test_http_json_reads_json_and_never_raises(server):
    url, routes = server
    routes["/version"] = (200, json.dumps({"commit": SHA}).encode())
    routes["/html"] = (200, b"<html>not json</html>")
    assert lt.http_json(f"{url}/version") == (200, {"commit": SHA})
    assert lt.http_json(f"{url}/html") == (200, None)
    assert lt.http_json(f"{url}/missing") == (404, None)
    assert lt.http_json("http://127.0.0.1:9/version", timeout=1) == (0, None)        # nothing listens: no exception, a dropped poll


def test_end_to_end_against_a_real_http_server(server):
    url, routes = server
    world = World(now=at(-3))
    routes["/version"] = lambda: (200, json.dumps(world.http(f"{PORTAL}/version")[1]).encode())       # flips when the FAKE clock says so
    src = world.sources()
    src.http = lt.http_json                                           # the real urllib client, over a real socket; the fake GitHub and clock
    out, err = io.StringIO(), io.StringIO()
    code = lt.main(["--portal", url, "--utc", "--watch", "--since", "now", "--timeout", "600"], src, out, err)
    assert code == 0, out.getvalue() + err.getvalue()
    assert re.search(r"visible on the portal\s+19:03:10\s+19:03:11\s+1s", out.getvalue())


# ── wiring ──────────────────────────────────────────────────────────────────────────

def test_the_make_targets_call_real_scripts_with_flags_they_accept():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("loop-time", "loop-watch", "test-affected"):
        assert re.search(rf"^{target}:", makefile, re.M), target
    recipes = re.findall(r"^\tpython (tools/\w+\.py)([^\n]*)", makefile, re.M)
    scripts = {s for s, _ in recipes}
    assert {"tools/loop_timer.py", "tools/affected_tests.py"} <= scripts and all((ROOT / s).is_file() for s in scripts)
    flags = set(re.findall(r"(--[a-z-]+)", " ".join(a for s, a in recipes if "loop_timer" in s)))
    assert {"--since", "--watch"} <= flags
    source = (ROOT / "tools" / "loop_timer.py").read_text(encoding="utf-8")
    for flag in flags:
        assert f'"{flag}"' in source, f"{flag} is in the Makefile but not an option of loop_timer.py"


def test_nothing_secret_is_in_the_script():
    source = (ROOT / "tools" / "loop_timer.py").read_text(encoding="utf-8")
    for pattern in (r"Bearer\s+[A-Za-z0-9._-]{16,}", r"sk-ant-[A-Za-z0-9_-]{10,}", r"ghp_[A-Za-z0-9]{20,}", r"SKIPPY_HOME_TOKEN\s*=\s*[^\s\"'$]"):
        assert not re.search(pattern, source), pattern
    assert lt.PORTAL.startswith("https://skippy-portal-") and lt.PORTAL in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
