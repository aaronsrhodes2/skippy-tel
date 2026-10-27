"""tools/loop_timer.py: the table, the arithmetic, and the watching, against a fake world.

skippy-core is a function (the `core` seam returns what the ssh snapshot prints: CI's results, Core's deployer status and cd.py's runs,
computed from a fake clock), the house repo is a small object, /version is another function, and `sleep` only moves the fake clock:
a five-minute loop plays out in milliseconds, and every moment the timer reports can be compared with the moment the scenario put it
at. The real pieces are exercised too: HouseGit against a real bare repo, the ssh snippet against a folder laid out like skippy-core's,
and the urllib path against a real HTTP server on an ephemeral port.
"""
from __future__ import annotations

import http.server
import io
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import loop_timer as lt

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
PUSH = datetime(2026, 9, 30, 19, 0, 0, tzinfo=UTC)         # the moment the push lands (the house repo's reflog)
SHA = "8adcd7ee35c3bf0096bb2f95babb6db113eb5ef6"
OLD_SHA = "0123456789abcdef0123456789abcdef01234567"
PORTAL = "https://portal.test"


def at(seconds: float) -> datetime:
    return PUSH + timedelta(seconds=seconds)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


CI_JOBS = (("suite", 45, 150), ("flask-api", 45, 90), ("services", 45, 70), ("shell-syntax", 45, 46))   # seconds after the push
CD_JOBS = (("portal-image", 172, 230), ("portal", 230, 275))


@dataclass
class World:
    """One loop on a fake clock. `now` only moves through sleep(); everything skippy-core, the house and the portal say is computed from it.
    Defaults: CI takes the commit up at +40 and is done at +150; cd.py's run is created at +170, the new revision has all traffic at +262,
    the run ends at +275; Core's deployer runs +160..+300; /version flips at +263."""
    now: datetime
    ci_at: float | None = 40
    ci_state: str = "success"
    failing_job: str = ""
    cd_at: float | None = 170
    cd_fail: str = ""                                   # the cd job that fails
    serving: float = 262
    visible_at: float | None = 263
    core_deploy: tuple | None = (160, 300)
    stamps: bool = True                                 # records written by today's ci.py / cd.py (False: before 2026-10-27)
    reflog: bool = True
    earlier: list[dict] = field(default_factory=list)   # older CI results, as the snapshot shows them
    version_before: dict | None = None
    version_down: bool = False
    core_down: int = 0                                  # this many ssh looks fail, then it answers
    bridge: dict = field(default_factory=dict)          # path -> (status, body)
    core_calls: int = 0
    http_calls: list = field(default_factory=list)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)

    def sources(self, env=None) -> lt.Sources:
        return lt.Sources(core=self.core, house=House(self), http=self.http, clock=self.clock, sleep=self.sleep,
                          env=env if env is not None else {})

    def t(self, seconds):
        return self.now >= at(seconds)

    # -- skippy-core --
    def ci(self):
        if self.ci_at is None or not self.t(self.ci_at):
            return None
        jobs, finished = [], max(e for _, _, e in CI_JOBS)
        for name, s, e in CI_JOBS:
            if not self.t(e):
                continue                                # ci.py writes the jobs only when every one has ended
            row = {"name": name, "state": "failure" if name == self.failing_job else "success", "why": "", "seconds": e - s}
            if self.stamps:
                row.update(started_at=at(s).timestamp(), finished_at=at(e).timestamp())
            jobs.append(row)
        if not self.t(finished):
            return {"sha": SHA, "state": "running", "at": at(self.ci_at).timestamp(), "jobs": []}
        out = {"sha": SHA, "state": self.ci_state, "at": at(self.ci_at).timestamp(), "seconds": finished - self.ci_at, "jobs": jobs}
        if self.stamps:
            out["finished_at"] = at(finished).timestamp()
        return out

    def cd(self):
        if self.cd_at is None or not self.t(self.cd_at):
            return None
        jobs, ok = [{"name": "test", "status": "completed", "conclusion": "success"}], True
        for name, s, e in CD_JOBS:
            if not ok or not self.t(s):
                break
            row = {"name": name, "status": "in_progress", "conclusion": ""}
            if self.stamps:
                row["started_at"] = stamp(at(s))
                if name == "portal" and self.t(self.serving) and self.cd_fail != "portal":
                    row["serving_at"] = stamp(at(self.serving))
            if self.t(e):
                failed = self.cd_fail == name
                row.update(status="completed", conclusion="failure" if failed else "success")
                if failed:
                    row["why"] = "smoke test: /health answers"
                    ok = False
                if self.stamps:
                    row["completed_at"] = stamp(at(e))
            jobs.append(row)
        end = max(e for _, _, e in CD_JOBS) if ok else next(e for n, _, e in CD_JOBS if n == self.cd_fail)
        done = self.t(end)
        return {"id": 35, "head_sha": SHA, "status": "completed" if done else "in_progress",
                "conclusion": ("success" if ok else "failure") if done else "", "created_at": stamp(at(self.cd_at)),
                "updated_at": stamp(min(self.now, at(end))), "title": "Portal: a change", "jobs": jobs}

    def deployer(self):
        if self.core_deploy and self.t(self.core_deploy[1]):
            good = {"commit": SHA, "full": "", "at": stamp(at(self.core_deploy[1]))}
            if self.stamps:
                good["started"] = stamp(at(self.core_deploy[0]))
            return {"state": "ok", "at": stamp(self.now), "commit": SHA, "last_good": good}
        return {"state": "waiting-ci", "at": stamp(self.now), "commit": SHA, "detail": "not tested yet by this box's CI",
                "last_good": {"commit": OLD_SHA, "full": "", "at": stamp(at(-900))}}

    def core(self):
        self.core_calls += 1
        if self.core_down:
            self.core_down -= 1
            raise lt.Problem("ssh skippy-core failed: Connection timed out")
        ci, cd = self.ci(), self.cd()
        return {"ci": ([ci] if ci else []) + self.earlier, "deployer": self.deployer(), "cd_runs": [cd] if cd else []}

    # -- the portal and the bridge --
    def http(self, url, headers=None, timeout=5.0):
        self.http_calls.append(url)
        if url == f"{PORTAL}/version":
            if self.version_down:
                return 0, None
            if self.visible_at is not None and self.t(self.visible_at):
                return 200, {"commit": SHA, "short": SHA[:7], "built_at": stamp(at(200)),
                             "revision": "skippy-portal-00043-new", "started_at": stamp(at(250))}
            return 200, self.version_before or {"commit": OLD_SHA, "short": OLD_SHA[:7], "built_at": "", "revision": "skippy-portal-00042-old",
                                                "started_at": stamp(at(-3600))}
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        return self.bridge.get(path, (404, None))


class House:
    """The house's bare repo, as the world says it is: main is OLD_SHA until the push, SHA after it."""

    def __init__(self, world: World):
        self.w = world

    def tip(self):
        return SHA if self.w.t(0) else OLD_SHA

    def resolve(self, prefix):
        return next((s for s in (SHA, OLD_SHA) if s.startswith(prefix)), None)

    def subject(self, sha):
        return "Portal: a change — with a dash" if sha == SHA else "older"

    def pushes(self):
        return [(OLD_SHA, at(-900)), *([(SHA, PUSH)] if self.w.t(0) else [])] if self.w.reflog else []

    def pushed(self, sha):
        for s, when in self.pushes():
            if s == sha:
                return when, "reflog"
        return (at(-30), "the commit date: the push itself isn't recorded") if sha == SHA else (None, "")


def run_main(world, *argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    code = lt.main(["--portal", PORTAL, "--utc", *argv], world.sources(env), out, err)
    return code, out.getvalue(), err.getvalue()


def rows_of(out: str) -> dict:
    return {line.strip().split("  ")[0]: line for line in out.splitlines() if "  " in line.strip()}


def by_key(doc) -> dict:
    return {s["key"]: s for s in doc["stages"]}


# ── pure helpers ────────────────────────────────────────────────────────────────────

def test_iso_and_epoch_times_parse_to_utc_and_nothing_is_none():
    assert lt.parse_ts("2026-09-30T19:03:12Z") == datetime(2026, 9, 30, 19, 3, 12, tzinfo=UTC)
    assert lt.parse_ts("2026-09-30T12:03:12-07:00") == datetime(2026, 9, 30, 19, 3, 12, tzinfo=UTC)
    assert lt.parse_ts(1_790_000_000) == datetime.fromtimestamp(1_790_000_000, UTC)
    assert lt.parse_ts(1_793_088_970.7391725) == datetime.fromtimestamp(1_793_088_970.7391725, UTC)    # CI's `at`, as ci.py writes it
    for nothing in ("0001-01-01T00:00:00Z", "", None, "garbage", 0):
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
    world = World(now=at(400))
    code, out, err = run_main(world, "--since", "2026-09-30T18:57:00Z")
    assert code == 0 and err == "", out + err
    rows = rows_of(out)
    assert re.search(r"18:57:00\s+19:00:00\s+3m00s", rows["prompt -> push"])                    # 180 s from the prompt to the push
    assert re.search(r"19:00:00\s+19:00:40\s+40s", rows["wait for CI (timer, 1 min)"])
    assert re.search(r"19:00:40\s+19:02:30\s+1m50s", rows["CI (skippy-core)"])
    assert re.search(r"19:00:45\s+19:01:30\s+45s", rows["flask-api"])                          # each CI job its own stamps
    assert re.search(r"19:02:30\s+19:02:50\s+20s", rows["wait for the deployer (timer)"])
    assert re.search(r"19:02:50\s+19:04:35\s+1m45s", rows["deploy run 35 (cd.py)"])
    assert re.search(r"19:03:50\s+19:04:35\s+45s\s+all traffic on the new revision at 19:04:22 UTC \(32s in\)", rows["deploy portal"])
    assert re.search(r"19:02:40\s+19:05:00\s+2m20s", rows["Core services (deployer)"])
    assert "already live when first checked" in rows["visible on the portal"]                  # nobody watched: the moment is unknowable
    assert re.search(r"18:57:00\s+19:04:22\s+7m22s\s+visible was not observed", rows["TOTAL prompt sent -> serving"])
    assert "critical path: push -> CI 40s -> CI 1m50s -> CI -> deploy run 20s -> run start 2s -> build portal image 58s -> " \
           "deploy portal (to serving) 32s   [push -> serving: 4m22s]" in out
    assert "Core's services deploy beside the portal, off its critical path" in out
    assert "the push time is" not in out                                                        # from the reflog: nothing to say


def test_json_carries_the_same_stages_in_utc_iso():
    world = World(now=at(400))
    code, out, _ = run_main(world, "--json")
    doc = json.loads(out)
    stages = by_key(doc)
    assert doc["sha"] == SHA and doc["push"] == "2026-09-30T19:00:00Z" and doc["push_from"] == "reflog" and doc["cd_run"] == 35
    assert stages["ci"]["start"] == "2026-09-30T19:00:40Z" and stages["ci"]["seconds"] == 110
    assert stages["cd.portal"]["depth"] == 1 and doc["serving_at"] == "2026-09-30T19:04:22Z"
    assert stages["prompt_to_push"]["na"].startswith("no --since") and doc["loop_start_is"] == "push (no --since)"
    assert doc["total"]["seconds"] == 262 and doc["failed"] is None
    assert [p["label"] for p in doc["critical_path"]][:2] == ["push -> CI", "CI"]


def test_the_table_is_plain_ascii_even_when_a_commit_title_is_not():
    code, out, _ = run_main(World(now=at(400)))
    out.encode("ascii")
    assert "loop 8adcd7e  Portal: a change ? with a dash" in out


def test_records_from_before_the_stamps_show_durations_and_say_what_stands_in():
    world = World(now=at(400), stamps=False)
    code, out, _ = run_main(world)
    rows = rows_of(out)
    assert re.search(r"^\s+suite\s+1m45s\s+no start/end stamps in this record, only how long it took", rows["suite"])
    assert "n/a (no stamps in this record (success))" in rows["deploy portal"]
    assert re.search(r"19:02:40|19:05:00", rows["Core services (deployer)"]) and "only the end is recorded" in rows["Core services (deployer)"]
    assert "the end of the whole run stands in for it" in out
    assert re.search(r"TOTAL push \(no --since\) -> serving\s+19:00:00\s+19:04:35\s+4m35s", out)


def test_without_a_reflog_the_push_is_the_commit_date_and_says_so():
    code, out, _ = run_main(World(now=at(400), reflog=False))
    assert "* the push time is the commit date: the push itself isn't recorded" in out
    assert re.search(r"18:59:30\s+19:00:40\s+1m10s", rows_of(out)["wait for CI (timer, 1 min)"])


def test_core_services_say_what_the_deployer_is_doing_or_that_it_moved_on():
    code, out, _ = run_main(World(now=at(200)))                       # before Core's deploy ended: the status names this commit
    assert "n/a (waiting-ci: not tested yet by this box's CI)" in rows_of(out)["Core services (deployer)"]
    world = World(now=at(400), core_deploy=None)
    world.deployer = lambda: {"state": "ok", "commit": OLD_SHA, "last_good": {"commit": OLD_SHA, "at": stamp(at(900))}}
    code, out, _ = run_main(world)
    assert "the deployer keeps only its last good deploy, and that is another commit" in out


# ── which commit ───────────────────────────────────────────────────────────────────

def test_since_picks_the_first_push_after_the_prompt_and_never_an_earlier_loops():
    world = World(now=at(400))
    code, out, _ = run_main(world, "--since", "2026-09-30T18:50:00Z", "--json")
    assert json.loads(out)["sha"] == SHA                              # OLD_SHA was pushed at -900 s, before the prompt
    world = World(now=at(400), reflog=False, earlier=[{"sha": OLD_SHA, "state": "success", "at": at(-860).timestamp(), "jobs": []}])
    code, out, _ = run_main(world, "--since", "2026-09-30T18:50:00Z", "--json")
    assert json.loads(out)["sha"] == SHA                              # no reflog: the first commit CI took up after the prompt


def test_sha_picks_that_commit_by_prefix_and_the_default_is_the_newest():
    earlier = [{"sha": OLD_SHA, "state": "success", "at": at(-860).timestamp(), "jobs": []}]
    code, out, _ = run_main(World(now=at(400), earlier=earlier), "--sha", "0123456", "--json")
    doc = json.loads(out)
    assert doc["sha"] == OLD_SHA and doc["cd_run"] is None
    code, out, _ = run_main(World(now=at(400), earlier=earlier), "--json")
    assert json.loads(out)["sha"] == SHA


def test_no_commit_is_a_clear_message_and_status_2():
    code, out, err = run_main(World(now=at(-60), ci_at=None), "--since", "now")
    assert code == 2 and "no commit found pushed since" in err and "--watch waits for it" in err


def test_ssh_trouble_is_a_message_not_a_traceback():
    world = World(now=at(400), core_down=1)
    code, out, err = run_main(world)
    assert code == 2 and err.startswith("loop_timer: ssh skippy-core failed")


def test_a_failed_ci_or_deploy_is_status_1_with_the_reason():
    code, out, _ = run_main(World(now=at(400), ci_state="failure", failing_job="suite", cd_at=None))
    assert code == 1 and "n/a (CI failed: suite)" in out and "failure" in rows_of(out)["suite"]
    code, out, _ = run_main(World(now=at(400), cd_fail="portal", visible_at=None))
    assert code == 1 and "failure: smoke test: /health answers" in rows_of(out)["deploy portal"]


# ── watching a live loop ─────────────────────────────────────────────────────────

def test_watching_from_before_the_push_sees_the_version_the_second_it_flips():
    world = World(now=at(-30))
    code, out, err = run_main(world, "--watch", "--since", "now")
    assert code == 0, out + err
    rows = rows_of(out)
    assert re.search(r"18:59:30\s+19:00:00\s+30s", rows["prompt -> push"])
    assert re.search(r"19:04:22\s+19:04:23\s+1s\s+first seen by this machine; 1s after all traffic moved", rows["visible on the portal"])
    assert re.search(r"TOTAL prompt sent -> visible\s+18:59:30\s+19:04:23\s+4m53s", out)
    assert "serving -> visible 1s" in out and "serving instance: revision skippy-portal-00043-new" in out
    assert "CI: success" in err and "deploy portal: in_progress" in err      # visible before the job's own smoke test ended
    assert world.core_calls < 60                                          # skippy-core is asked every 10 s, not every second


def test_watching_without_a_reflog_takes_the_push_as_the_moment_main_moved():
    world = World(now=at(-31), reflog=False)
    code, out, _ = run_main(world, "--watch")
    assert code == 0 and "the push time is when this machine saw main move (no reflog on the house repo)" in out
    assert re.search(r"19:00:01\s+19:00:40\s+39s", rows_of(out)["wait for CI (timer, 1 min)"])       # polled every 2 s from -31


def test_a_redeploy_of_the_same_commit_waits_for_the_new_revision_not_the_old_one():
    before = {"commit": SHA, "short": SHA[:7], "revision": "skippy-portal-00042-same", "started_at": stamp(at(-3600))}
    world = World(now=at(-10), version_before=before)
    code, out, _ = run_main(world, "--watch", "--since", "now")
    assert code == 0 and re.search(r"visible on the portal\s+19:04:22\s+19:04:23", out)


def test_a_failed_run_ends_the_watch_with_the_reason_and_status_1():
    world = World(now=at(-10), cd_fail="portal-image", visible_at=None)
    code, out, err = run_main(world, "--watch", "--since", "now")
    assert code == 1 and "the deploy run ended failure: the new version did not go live" in out
    assert world.now < at(260)                                             # it stopped at the failure, not at the timeout


def test_red_ci_ends_the_watch_at_once():
    world = World(now=at(-10), ci_state="failure", failing_job="flask-api", cd_at=None, visible_at=None)
    code, out, _ = run_main(world, "--watch")
    assert code == 1 and "CI failed: the new version will not go live" in out and world.now < at(170)


def test_a_deploy_that_finishes_without_the_version_ever_changing_is_reported_not_waited_on_forever():
    world = World(now=at(-10), visible_at=None)
    code, out, _ = run_main(world, "--watch", "--since", "now")
    assert code == 1 and "the deploy finished but /version never showed the new commit" in out and world.now < at(500)


def test_ctrl_c_prints_what_was_seen_so_far():
    world = World(now=at(-10))
    real = world.sleep

    def sleep(seconds):
        if world.now >= at(100):
            raise KeyboardInterrupt
        real(seconds)
    world.sleep = sleep
    code, out, err = run_main(world, "--watch", "--since", "now")
    assert code == 1 and "interrupted (Ctrl-C)" in err and "interrupted (Ctrl-C)" in out
    assert "running" in rows_of(out)["CI (skippy-core)"] and "+" in rows_of(out)["CI (skippy-core)"]


def test_a_dropped_ssh_does_not_end_a_watch_but_five_in_a_row_do():
    world = World(now=at(-10), core_down=3)
    code, out, _ = run_main(world, "--watch", "--since", "now")
    assert code == 0
    world = World(now=at(-10), core_down=99)
    code, out, err = run_main(world, "--watch", "--since", "now")
    assert code == 2 and "ssh skippy-core failed" in err


def test_watching_with_no_push_gives_up_at_the_timeout():
    world = World(now=at(-600))
    code, out, err = run_main(world, "--watch", "--since", "now", "--timeout", "120")
    assert code == 2 and "no commit found" in err and world.now <= at(-470)


# ── the agent's marks, through the bridge (optional) ─────────────────────────────────

def bridge_world(events, listing=None):
    world = World(now=at(400))
    world.bridge = {"/workshop/runs": (200, {"runs": listing if listing is not None else [{"task": "t1", "age_s": 570.0}]}),     # created at -170 s
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
    assert labels[labels.index("prompt -> bridge received"):labels.index("wait for CI (timer, 1 min)")] == [
        "prompt -> bridge received", "bridge received -> first edit", "first edit -> git push", "git push -> push"]
    assert "prompt -> push" not in labels
    first = [l for l in out.splitlines() if l.startswith("bridge received -> first edit")][0]
    assert re.search(r"18:57:10\s+18:58:00\s+50s", first)      # created = the reply's time (19:06:40) minus the bridge's own age_s (570 s)


def test_bridge_without_a_token_or_without_ts_says_so_and_keeps_the_single_row():
    world = bridge_world([{"t": "tool", "name": "Edit", "detail": "x"}])
    code, out, _ = run_main(world, "--bridge", "--since", "2026-09-30T18:57:00Z", env={})
    assert "bridge: n/a (SKIPPY_HOME_TOKEN isn't set" in out and re.search(r"^prompt -> push\s+18:57:00", out, re.M)
    code, out, _ = run_main(world, "--bridge", "--since", "2026-09-30T18:57:00Z", env={"SKIPPY_HOME_TOKEN": "t0ken"})
    assert "bridge: first edit / git push n/a (the bridge's events carry no ts yet)" in out


def test_the_token_is_sent_to_the_bridge_and_never_printed():
    world = bridge_world([])
    seen = []
    real = world.http
    world.http = lambda url, headers=None, timeout=5.0: (seen.append(headers), real(url, headers, timeout))[1]
    code, out, err = run_main(world, "--bridge", env={"SKIPPY_HOME_TOKEN": "sentinel-token-value"})
    assert {"Authorization": "Bearer sentinel-token-value"} in [h for h in seen if h]
    assert "sentinel-token-value" not in out + err


def test_bridge_marks_outside_the_prompt_to_push_window_are_dropped():
    events = [{"t": "tool", "name": "Bash", "detail": "git push", "ts": stamp(at(60))}]          # after the push: not this loop's
    world = bridge_world(events)
    code, out, _ = run_main(world, "--bridge", "--since", "2026-09-30T18:57:00Z", env={"SKIPPY_HOME_TOKEN": "t"})
    assert "ignored bridge marks outside the prompt..push window: git push" in out
    assert re.search(r"^prompt -> bridge received", out, re.M)                                   # the good mark stays


def test_an_unreachable_bridge_is_a_note_not_a_failure():
    code, out, _ = run_main(World(now=at(400)), "--bridge", env={"SKIPPY_HOME_TOKEN": "t"})
    assert code == 0 and "bridge: n/a (http://localhost:9337/workshop/runs answered 404" in out


# ── the real pieces: the house repo, the ssh snippet, urllib ──────────────────────────

def git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout.strip()


@pytest.fixture
def house(tmp_path):
    bare, work = tmp_path / "house.git", tmp_path / "work"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(bare))
    git(tmp_path, "clone", "--quiet", str(bare), str(work))
    for k, v in (("user.name", "T"), ("user.email", "t@example.com")):
        git(work, "config", k, v)

    def commit(text, when, push=True):
        (work / "f.txt").write_text(text)
        git(work, "add", "f.txt")
        git(work, "commit", "--quiet", "-m", text, env={"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        if push:
            git(work, "push", "--quiet", "origin", "main")
        return git(work, "rev-parse", "HEAD")
    return bare, commit


def test_house_git_reads_the_push_from_the_reflog_and_falls_back_honestly(house):
    bare, commit = house
    first = commit("one", "2026-09-30T18:00:00Z")                      # pushed before the reflog was switched on
    when, how = lt.HouseGit(str(bare)).pushed(first)
    assert how.startswith("the ref file's time")                       # still main's tip: the ref file was written by that push
    git(bare, "config", "core.logAllRefUpdates", "always")
    a = commit("two", "2026-09-30T18:10:00Z", push=False)
    b = commit("three", "2026-09-30T18:11:00Z")                        # a and b reach main in ONE push
    h = lt.HouseGit(str(bare))
    assert h.tip() == b and h.resolve(b[:7]) == b and h.subject(b) == "three"
    (when_b, how_b), (when_a, how_a) = h.pushed(b), h.pushed(a)
    assert how_b == how_a == "reflog" and when_a == when_b and abs((datetime.now(UTC) - when_b).total_seconds()) < 120
    assert [s for s, _ in h.pushes()] == [b]
    when, how = h.pushed(first)                                        # older than the reflog and no longer the tip: the commit date
    assert when == datetime(2026, 9, 30, 18, 0, tzinfo=UTC) and how.startswith("the commit date")
    assert lt.HouseGit(str(bare.parent / "missing.git")).tip() is None


def test_the_ssh_snippet_reads_skippy_cores_records_and_drops_the_log_tails(tmp_path):
    base = tmp_path / "core"
    (base / "ci" / "skippy-tel" / "results").mkdir(parents=True)
    (base / "deployer").mkdir()
    (base / "cd").mkdir()
    (base / "ci" / "skippy-tel" / "results" / f"{SHA}.json").write_text(json.dumps(
        {"sha": SHA, "state": "success", "at": 1.0, "jobs": [{"name": "suite", "state": "success", "tail": "x" * 4000}]}))
    (base / "ci" / "skippy-tel" / "results" / f"{SHA}.suite.log").write_text("not json")
    (base / "deployer" / "status.json").write_text(json.dumps({"state": "ok"}))
    (base / "cd" / "state.json").write_text(json.dumps({"runs": [{"id": 1, "head_sha": SHA}], "deployed": {}}))
    snippet = lt.REMOTE.replace('"/var/lib/skippy-core"', repr(base.as_posix()))
    assert snippet != lt.REMOTE
    done = subprocess.run([sys.executable, "-I", "-"], input=snippet, capture_output=True, text=True, check=True)
    snap = json.loads(done.stdout)
    assert snap["ci"] == [{"sha": SHA, "state": "success", "at": 1.0, "jobs": [{"name": "suite", "state": "success"}]}]
    assert snap["deployer"] == {"state": "ok"} and snap["cd_runs"] == [{"id": 1, "head_sha": SHA}]


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
    src.http = lt.http_json                                           # the real urllib client, over a real socket; the fake core and clock
    out, err = io.StringIO(), io.StringIO()
    code = lt.main(["--portal", url, "--utc", "--watch", "--since", "now", "--timeout", "600"], src, out, err)
    assert code == 0, out.getvalue() + err.getvalue()
    assert re.search(r"visible on the portal\s+19:04:22\s+19:04:23\s+1s", out.getvalue())


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


def test_github_is_out_of_the_loop():
    source = (ROOT / "tools" / "loop_timer.py").read_text(encoding="utf-8")
    assert '"gh"' not in source and "gh run" not in source and "GH_REPO" not in source


def test_nothing_secret_is_in_the_script():
    source = (ROOT / "tools" / "loop_timer.py").read_text(encoding="utf-8")
    for pattern in (r"Bearer\s+[A-Za-z0-9._-]{16,}", r"sk-ant-[A-Za-z0-9_-]{10,}", r"ghp_[A-Za-z0-9]{20,}", r"SKIPPY_HOME_TOKEN\s*=\s*[^\s\"'$]"):
        assert not re.search(pattern, source), pattern
    assert lt.PORTAL.startswith("https://skippy-portal-") and lt.PORTAL in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
