"""host_ops.py: the Admin page's skippy-pc buttons, with a fake command runner (no docker, git or tailscale needed)."""
from __future__ import annotations

import pytest

import host_ops
import projects as projects_mod


class FakeWorkshop:
    def __init__(self, busy=False, clone="C:/nowhere"):
        self._busy, self.clone, self.held, self.holds = busy, clone, None, []

    def busy(self):
        return self._busy

    def hold(self, why):
        if self._busy or self.held:
            return False
        self.held = why
        self.holds.append(why)
        return True

    def release(self):
        self.held = None

    def project(self, pid=None):
        pid = pid or "skippytel"
        if pid not in ("skippytel", "music-collection"):
            raise KeyError(pid)
        return projects_mod.Project(pid, pid.title(), f"aaronsrhodes2/{pid}", "main", "C:/primary", self.clone)


class Runner:
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, cmd, cwd=None):
        self.calls.append((cmd, cwd))
        key = " ".join(cmd[1:])
        for prefix, answer in self.answers.items():
            if key.startswith(prefix):
                return answer
        return 0, ""


def ops(workshop=None, runner=None, supervised=True):
    later = []
    o = host_ops.HostOps(workshop or FakeWorkshop(), runner or Runner(), later=lambda s, fn: later.append(fn),
                         is_supervised=lambda: supervised, clock=lambda: 1_790_000_000)
    return o, later


def test_a_listed_service_is_restarted_with_compose_in_the_skippytel_folder():
    r = Runner({"compose ps": (0, "skippytel-video-gate-1 Up 3 seconds")})
    o, _ = ops(runner=r)
    res = o.do("restart-service", "video-gate")
    assert res.ok and "video-gate restarted" in res.summary and "Up 3 seconds" in res.output
    assert r.calls[0] == ([host_ops.DOCKER, "compose", "restart", "video-gate"], host_ops.COMPOSE_DIR)


@pytest.mark.parametrize("name", ["", "postgres", "video-gate; rm -rf /", "--all"])
def test_anything_not_on_the_list_is_refused_before_a_command_runs(name):
    r = Runner()
    o, _ = ops(runner=r)
    with pytest.raises(host_ops.Refused):
        o.do("restart-service", name)
    assert r.calls == []


def test_a_failed_restart_says_so_with_its_output():
    o, _ = ops(runner=Runner({"compose restart": (1, "no such service")}))
    res = o.do("restart-service", "flask-api")
    assert not res.ok and "exit 1" in res.summary and "no such service" in res.output


def test_the_reporter_is_restarted_after_the_reply_so_its_own_answer_gets_out():
    r = Runner()
    o, later = ops(runner=r)
    res = o.do("restart-service", "home-reporter")
    assert res.ok and r.calls == [] and len(later) == 1
    later[0]()
    assert r.calls[0][0][-2:] == ["restart", "home-reporter"]


def test_the_bridge_restarts_only_under_its_supervisor_and_not_mid_run():
    with pytest.raises(host_ops.Refused, match="supervisor"):
        ops(supervised=False)[0].do("restart-bridge")
    with pytest.raises(host_ops.Refused, match="middle of a run"):
        ops(workshop=FakeWorkshop(busy=True))[0].do("restart-bridge")
    o, later = ops()
    assert o.do("restart-bridge").ok and len(later) == 1          # the exit is scheduled, not done inline


def test_reset_refuses_mid_run_and_an_unknown_project(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(host_ops.Refused, match="middle of a run"):
        ops(workshop=FakeWorkshop(busy=True, clone=str(tmp_path)))[0].do("reset-workshop", "skippytel")
    with pytest.raises(host_ops.Refused, match="no project"):
        ops()[0].do("reset-workshop", "nope")


def test_reset_with_no_clone_does_nothing():
    r = Runner()
    res = ops(runner=r)[0].do("reset-workshop", "")
    assert res.ok and "no clone yet" in res.summary and r.calls == []


def test_reset_keeps_uncommitted_work_and_unpushed_commits_then_goes_back_to_origin(tmp_path):
    (tmp_path / ".git").mkdir()
    r = Runner({"status --porcelain": (0, " M portal/app.py"), "rev-list": (0, "2")})
    res = ops(workshop=FakeWorkshop(clone=str(tmp_path)), runner=r)[0].do("reset-workshop", "skippytel")
    git = [" ".join(c[0][1:]) for c in r.calls]
    assert git[0] == "fetch --prune origin"
    stash = next(i for i, g in enumerate(git) if g.startswith("stash push --include-untracked"))
    rescue = next(i for i, g in enumerate(git) if g.startswith("branch rescue/"))
    checkout = git.index("checkout -B main origin/main")
    assert stash < checkout and rescue < checkout and git[-1] == "reset --hard origin/main"
    assert all(c[1] == str(tmp_path) for c in r.calls)
    assert res.ok and "git stash list" in res.summary and "2 unpushed commit(s)" in res.summary


def test_reset_stops_untouched_when_the_fetch_fails(tmp_path):
    (tmp_path / ".git").mkdir()
    r = Runner({"fetch": (128, "could not resolve host")})
    res = ops(workshop=FakeWorkshop(clone=str(tmp_path)), runner=r)[0].do("reset-workshop", "skippytel")
    assert not res.ok and "untouched" in res.summary and len(r.calls) == 1


def test_funnel_on_and_off_touch_both_doors_and_report_the_status():
    r = Runner({"funnel status": (0, "https://skippy-pc (Funnel on)")})
    res = ops(runner=r)[0].do("funnel", "on")
    cmds = [c[0][1:] for c in r.calls]
    assert ["funnel", "--https=443", "--bg", "3005"] in cmds and ["funnel", "--https=8443", "--bg", "3006"] in cmds
    assert ["funnel", "--https=10000", "--bg", "3007"] in cmds
    assert res.ok and "Funnel on" in res.output
    r2 = Runner()
    ops(runner=r2)[0].do("funnel", "off")
    assert ["funnel", "--https=443", "off"] in [c[0][1:] for c in r2.calls]
    with pytest.raises(host_ops.Refused):
        ops()[0].do("funnel", "sideways")


def test_one_operation_at_a_time_and_unknown_ones_refused():
    o, _ = ops()
    with pytest.raises(host_ops.Refused, match="Unknown"):
        o.do("format-c")
    o._lock.acquire()
    with pytest.raises(host_ops.Refused, match="still running"):
        o.do("funnel", "status")


def test_supervised_reads_a_live_pid(tmp_path):
    import os
    f = tmp_path / "bridge-supervisor.pid"
    assert not host_ops.supervised(f)
    f.write_text(str(os.getpid()))
    assert host_ops.supervised(f)
    f.write_text("nonsense")
    assert not host_ops.supervised(f)


def test_a_reset_holds_new_runs_off_while_it_works_and_lets_go_after(tmp_path):
    (tmp_path / ".git").mkdir()
    shop = FakeWorkshop(clone=str(tmp_path))
    seen = []
    r = Runner()
    r_call = r.__call__
    o, _ = ops(workshop=shop, runner=lambda cmd, cwd=None: (seen.append(shop.held), r_call(cmd, cwd))[1])
    o.do("reset-workshop", "skippytel")
    assert seen and all(h and "being reset" in h for h in seen)          # every git step ran under the hold
    assert shop.held is None


def test_a_bridge_restart_keeps_runs_off_until_it_exits():
    shop = FakeWorkshop()
    o, later = ops(workshop=shop)
    o.do("restart-bridge")
    assert shop.held and "restarting" in shop.held and len(later) == 1   # never released: the process ends
