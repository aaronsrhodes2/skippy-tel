"""The starter on skippy-pc: it does three things, in the right order, refuses everything else, never touches a running Skippy, and keeps polling while Docker starts."""
import sys
import tempfile
import urllib.error
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import skippy_starter as ss  # noqa: E402


class World:
    """A fake PC: Docker up or not, containers, a bridge that answers or not, a portal that hands out commands."""

    def __init__(self, docker=True, containers=("running",) * 3, bridge=False, busy=False, docker_wakes=True):
        self.docker, self.states, self.bridge_up, self.busy, self.docker_wakes = docker, list(containers), bridge, busy, docker_wakes
        self.cmds, self.spawned, self.posts, self.portal_down = [], [], [], False
        self.pending_commands = []
        self.t = [0.0]

    def run(self, cmd, timeout=120, cwd=None, env=None):
        self.cmds.append(cmd)
        if cmd[:2] == ["docker", "ps"] and "-q" in cmd and "-a" not in cmd:
            return (0, "") if self.docker else (1, "cannot connect")
        if cmd[:3] == ["docker", "ps", "-a"]:
            return 0, "\n".join(self.states)
        if cmd[:3] == ["docker", "compose", "up"]:
            self.states = ["running"] * 3
            return 0, ""
        if cmd[:3] == ["docker", "compose", "stop"]:
            self.states = ["exited"] * 3
            return 0, ""
        if cmd[0] == "powershell.exe" and "-File" in cmd:                          # the clean restart
            self.docker = self.docker_wakes
            return 0, ""
        if cmd[0] == "powershell.exe":                                              # kill the bridge
            self.bridge_up = False
            return 0, ""
        return 0, ""

    def spawn(self, cmd, env=None, log=""):
        self.spawned.append(cmd)
        if cmd[0] == ss.DOCKER_EXE and self.docker_wakes and not getattr(self, "needs_clean", False):
            self.docker = True
        elif "bridge_supervisor.sh" in " ".join(cmd):
            self.bridge_up = True

    def http(self, url, data=None, headers=None, timeout=15):
        if url.endswith("/health"):
            if not self.bridge_up:
                raise urllib.error.URLError("refused")
            return {"status": "ok", "workshop": {"ready": True, "busy": self.busy}}
        if url.endswith("/home/starter"):
            if self.portal_down:
                raise urllib.error.URLError("no internet")
            self.posts.append((data, headers))
            cmds, self.pending_commands = self.pending_commands, []
            return {"commands": cmds}
        raise AssertionError(url)

    def sleep(self, s):
        self.t[0] += s


NOW = [datetime(2026, 10, 12, 23, 30)]                       # Monday night


def make(state_file=None, **kw):
    w = World(**kw)
    state_file = state_file or Path(tempfile.mkdtemp()) / "starter.json"
    s = ss.Starter(run=w.run, http=w.http, spawn=w.spawn, sleep=w.sleep, clock=lambda: w.t[0], token="home-token", root=Path("/repo"), say=lambda *_: None,
                   now=lambda: NOW[0], state_file=state_file)
    s.state_file = state_file
    return s, w


def test_start_with_everything_down_brings_up_docker_then_the_containers_then_the_bridge_in_that_order():
    s, w = make(docker=False, containers=(), bridge=False)
    ok, text = s.do("start")
    assert ok and text == "Docker up; 3 of 3 containers; bridge answering."
    assert w.spawned[0][0] == ss.DOCKER_EXE and "bridge_supervisor.sh" in " ".join(w.spawned[1])
    order = [c[:3] for c in w.cmds if c[0] == "docker" and c[1] == "compose"]
    assert order == [["docker", "compose", "up"]] and s.working == ""


def test_start_when_most_is_already_up_only_does_what_is_missing():
    s, w = make(containers=("running", "exited", "running"), bridge=True)
    ok, text = s.do("start")
    assert ok and w.spawned == [] and w.states == ["running"] * 3 and "3 of 3" in text


def test_docker_that_will_not_start_gets_the_clean_restart_and_then_a_plain_answer():
    s, w = make(docker=False, docker_wakes=False)
    ok, text = s.do("start")
    assert not ok and "needs a look in person" in text
    assert any(c[0] == "powershell.exe" and "-File" in c for c in w.cmds)                     # the documented clean restart was tried
    s2, w2 = make(docker=False)
    w2.needs_clean = True
    w2.docker_wakes = True
    ok2, _ = s2.do("start")
    assert ok2                                                                                 # the clean restart brought it back


def test_stop_stops_the_bridge_and_the_containers_and_leaves_docker_alone():
    s, w = make(bridge=True)
    ok, text = s.do("stop")
    assert ok and "Docker Desktop and the starter are still up" in text and "starts itself again at Tue 7:00 AM" in text and w.states == ["exited"] * 3 and not w.bridge_up
    assert not any(c[:2] == ["docker", "kill"] or "quit" in " ".join(c) for c in w.cmds)


def test_stop_does_nothing_at_all_while_skippy_desktop_is_in_a_run():
    s, w = make(bridge=True, busy=True)
    ok, text = s.do("stop")
    assert not ok and "middle of a run" in text and w.bridge_up and w.states == ["running"] * 3
    ok, text = s.do("restart")
    assert not ok and w.bridge_up and w.states == ["running"] * 3                              # a restart is a stop first: same refusal


def test_restart_is_a_stop_and_a_start():
    s, w = make(bridge=True)
    ok, text = s.do("restart")
    assert ok and text.startswith("Restarted.") and w.bridge_up and w.states == ["running"] * 3


def test_an_operation_that_is_not_one_of_the_three_runs_nothing():
    s, w = make()
    for op in ("format c:", "", "start; rm -rf /", None, "STOP", "update"):
        assert s.do(op) == (False, "I do not do that.")
    assert w.cmds == [] and w.spawned == []


def test_a_crash_inside_an_operation_is_a_result_not_a_dead_starter():
    s, w = make()
    s.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    ok, text = s.do("start")
    assert not ok and "went wrong" in text and s.working == ""


def test_each_poll_tells_the_portal_how_things_are_with_the_home_token_and_nothing_else():
    s, w = make(bridge=True, busy=True)
    s.poll_once(threaded=False)
    data, headers = w.posts[0]
    assert headers == {"Authorization": "Bearer home-token"} and data["results"] == []
    assert data["status"] == {"docker": True, "containers_up": 3, "containers_total": 3, "bridge": True, "busy": True, "supervisor": True, "working": "", "resume_at": ""}


def test_a_request_from_the_portal_is_done_once_and_its_result_goes_back_on_the_next_poll():
    s, w = make(bridge=True)
    w.pending_commands = [{"id": "a1b2c3d4", "op": "stop"}]
    s.poll_once(threaded=False)
    w.pending_commands = [{"id": "a1b2c3d4", "op": "stop"}]                                    # redelivered: already handled, ignored
    s.poll_once(threaded=False)
    assert sum(1 for c in w.cmds if c[:3] == ["docker", "compose", "stop"]) == 1
    sent = w.posts[1][0]["results"]
    assert sent == [{"id": "a1b2c3d4", "ok": True, "summary": "Stopped: the bridge and every container. Docker Desktop and the starter are still up. It starts itself again at Tue 7:00 AM."}]
    s.poll_once(threaded=False)
    assert w.posts[2][0]["results"] == []                                                       # reported once


def test_garbage_commands_are_answered_and_nothing_runs():
    s, w = make()
    w.pending_commands = [{"id": "aaaa1111", "op": "rm -rf /"}, {"id": "bbbb2222"}, "nope", {"op": "start"}, {"id": 5, "op": "start"}]
    s.poll_once(threaded=False)
    assert w.cmds == [c for c in w.cmds if c[0] == "docker" and c[1] == "ps"]                   # only the status looks, no operation
    s.poll_once(threaded=False)
    assert {r["id"]: r["summary"] for r in w.posts[1][0]["results"]} == {"aaaa1111": "I do not do that.", "bbbb2222": "I do not do that."}


def test_a_second_request_while_busy_is_refused_politely_and_polling_goes_on():
    s, w = make()
    s.working = "starting Docker Desktop"
    w.pending_commands = [{"id": "cccc3333", "op": "start"}]
    s.poll_once(threaded=False)
    s.poll_once(threaded=False)
    assert w.posts[1][0]["results"] == [{"id": "cccc3333", "ok": False, "summary": "Busy: starting Docker Desktop."}]
    assert w.posts[1][0]["status"]["working"] == "starting Docker Desktop"


def test_no_internet_keeps_the_results_for_the_next_try_and_never_raises():
    s, w = make()
    s.results = [{"id": "dddd4444", "ok": True, "summary": "x"}]
    w.portal_down = True
    assert s.poll_once(threaded=False) == []
    assert s.results == [{"id": "dddd4444", "ok": True, "summary": "x"}]
    w.portal_down = False
    s.poll_once(threaded=False)
    assert w.posts[-1][0]["results"][0]["id"] == "dddd4444"


def test_the_token_comes_from_the_environment_or_the_vault_and_a_second_copy_does_not_start(tmp_path):
    vault = tmp_path / ".env"
    vault.write_text("OTHER=1\nSKIPPY_HOME_TOKEN='abc123'\n")
    assert ss.load_token({"SKIPPY_HOME_TOKEN": "fromenv"}, ()) == "fromenv"
    assert ss.load_token({}, (str(vault),)) == "abc123" and ss.load_token({}, (str(tmp_path / "none"),)) == ""
    first = ss.one_copy()
    try:
        assert first is not None and ss.one_copy() is None
    finally:
        first.close()


def test_a_stop_says_when_it_comes_back_by_itself_and_the_default_is_the_next_7_am():
    s, w = make(bridge=True)
    NOW[0] = datetime(2026, 10, 12, 23, 30)
    ok, text = s.do("stop")
    assert ok and s.resume_at == datetime(2026, 10, 13, 7, 0)
    NOW[0] = datetime(2026, 10, 13, 3, 0)                                                        # stopped in the small hours: that same morning
    s2, _ = make(bridge=True)
    s2.do("stop")
    assert s2.resume_at == datetime(2026, 10, 13, 7, 0)
    NOW[0] = datetime(2026, 10, 13, 6, 50)                                                       # ten minutes before 7: not "in ten minutes", tomorrow
    s3, _ = make(bridge=True)
    s3.do("stop")
    assert s3.resume_at == datetime(2026, 10, 14, 7, 0)
    NOW[0] = datetime(2026, 10, 12, 23, 30)
    s4, _ = make(bridge=True)
    s4.do("stop", "4h")
    assert s4.resume_at == datetime(2026, 10, 13, 3, 30)
    s5, _ = make(bridge=True)
    ok, text = s5.do("stop", "manual")
    assert ok and s5.resume_at is None and "stays down until you start it" in text
    assert s5.do("stop", "whenever")[0] is False                                                  # not one of the three ways


def test_at_the_time_it_starts_the_stack_by_itself_once_and_never_before():
    s, w = make(bridge=True)
    NOW[0] = datetime(2026, 10, 12, 23, 30)
    s.do("stop")
    assert not w.bridge_up and w.states == ["exited"] * 3
    NOW[0] = datetime(2026, 10, 13, 6, 59)
    assert s.check_resume() is False and w.states == ["exited"] * 3
    NOW[0] = datetime(2026, 10, 13, 7, 0)
    assert s.check_resume() is True and w.states == ["running"] * 3 and w.bridge_up and s.resume_at is None
    assert s.check_resume() is False                                                              # done once
    s.poll_once(threaded=False)
    assert "Back on by itself, as planned" in w.posts[-1][0]["results"][0]["summary"]


def test_the_plan_survives_a_reboot_and_a_start_by_hand_cancels_it():
    state = Path(tempfile.mkdtemp()) / "starter.json"
    s, w = make(state_file=state, bridge=True)
    NOW[0] = datetime(2026, 10, 12, 23, 30)
    s.do("stop")
    again, w2 = make(state_file=state, bridge=False, containers=("exited",) * 3)                  # the PC rebooted: a new starter reads the file
    assert again.resume_at == datetime(2026, 10, 13, 7, 0)
    ok, _ = again.do("start")
    assert ok and again.resume_at is None and again._load_resume() is None


def test_a_failed_automatic_start_is_tried_again_in_ten_minutes():
    s, w = make(docker=False, docker_wakes=False)
    NOW[0] = datetime(2026, 10, 13, 7, 0)
    s.resume_at = datetime(2026, 10, 13, 7, 0)
    assert s.check_resume() is False and s.resume_at == datetime(2026, 10, 13, 7, 10)


def test_the_portal_s_resume_choice_reaches_the_stop_and_a_junk_one_becomes_the_default():
    s, w = make(bridge=True)
    NOW[0] = datetime(2026, 10, 12, 23, 30)
    w.pending_commands = [{"id": "eeee5555", "op": "stop", "resume": "manual"}]
    s.poll_once(threaded=False)
    assert s.resume_at is None
    s2, w2 = make(bridge=True)
    w2.pending_commands = [{"id": "ffff6666", "op": "stop", "resume": "rm -rf"}]
    s2.poll_once(threaded=False)
    assert s2.resume_at == datetime(2026, 10, 13, 7, 0)
