"""The starter on skippy-pc: start, stop, restart, heal one thing and repair everything, in the right order; it refuses everything else, never touches a
running Skippy, stops processes by PID only, heals a hung Docker by itself, and keeps polling while Docker starts."""
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
        self.building, self.core_says = "", '{"state": "ok", "detail": "repaired"}' 
        self.pending_commands = []
        self.battery = ""                                                           # no UPS unless a test plugs one in
        self.t = [0.0]
        self.oa_running, self.oa_web, self.oa_web_on_start = False, False, True      # OpenAudible: the app, its web server

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
        if cmd[0] == "tasklist":
            return 0, '"OpenAudible.exe","4711","Console","1","300,000 K"' if self.oa_running else "INFO: No tasks are running which match the specified criteria."
        if cmd[0] == "powershell.exe" and "-File" in cmd:                          # the clean restart
            self.docker = self.docker_wakes
            return 0, ""
        if cmd[0] == "powershell.exe" and ss.UPS_PS in cmd:                         # skippy-pc's UPS (Win32_Battery)
            return 0, self.battery
        if cmd[0] == "powershell.exe":                                              # who is building? (read only)
            return 0, self.building
        if cmd[0] == "netstat":
            listening = "  TCP    0.0.0.0:9337           0.0.0.0:0              LISTENING       4242\n" if self.bridge_up else ""
            return 0, listening + "  TCP    0.0.0.0:3003           0.0.0.0:0              LISTENING       999"
        if cmd[0] == "taskkill":
            if cmd[2] == "4242":
                self.bridge_up = False
            return 0, ""
        if cmd[:4] == ["docker", "compose", "config", "--services"]:
            return 0, "flask-api\nvoice-gate\nollama"
        if cmd[0] == "ssh":
            return 0, "12:00 deploying\n" + self.core_says
        if cmd[0] == "git":
            return 0, "main" if "rev-parse" in cmd else ""
        return 0, ""

    def spawn(self, cmd, env=None, log="", minimized=False):
        self.spawned.append(cmd)
        if cmd[0] == ss.OPENAUDIBLE_EXE:
            self.oa_running, self.oa_web = True, self.oa_web_on_start
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
    s.openaudible_probe = lambda: w.oa_web
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
    assert data["status"] == {"docker": True, "containers_up": 3, "containers_total": 3, "bridge": True, "busy": True, "supervisor": True, "working": "", "resume_at": "", "containers": [], "openaudible": False, "openaudible_app": False}


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
    assert w.cmds == [c for c in w.cmds if (c[0] == "docker" and c[1] == "ps") or c[0] in ("nvidia-smi", "tasklist")]   # only the status looks, no operation
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


def test_the_token_comes_from_the_environment_or_the_vault_and_a_second_copy_does_not_start(tmp_path, monkeypatch):
    import socket
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))                        # a free port: the real starter may be running on this PC, holding its own
    monkeypatch.setattr(ss, "LOCK_PORT", probe.getsockname()[1])
    probe.close()
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


# ── heal one thing, repair everything, and Docker by itself ──
def test_heal_recreates_one_compose_service_the_safe_way_and_refuses_names_it_does_not_know():
    s, w = make()
    ok, text = s.do("heal", arg="voice-gate")
    assert ok and text == "voice-gate recreated."
    compose = [c[2:] for c in w.cmds if c[:2] == ["docker", "compose"] and c[2] in ("stop", "rm", "up")]
    assert compose == [["stop", "voice-gate"], ["rm", "-f", "voice-gate"], ["up", "-d", "--build", "voice-gate"]]
    for bad in ("nginx", "voice-gate; rm -rf /", "../x", ""):
        assert s.do("heal", arg=bad)[0] is False, bad


def test_heal_leaves_a_service_alone_while_someone_is_building():
    s, w = make()
    w.building = "docker  compose up -d --build tts-studio"
    ok, text = s.do("heal", arg="voice-gate")
    assert not ok and "building" in text and not any(c[:3] == ["docker", "compose", "rm"] for c in w.cmds)


def test_the_bridge_is_restarted_by_its_pid_never_mid_run_and_never_by_name():
    s, w = make(bridge=True)
    ok, text = s.do("heal", arg="bridge")
    assert ok and ["taskkill", "/PID", "4242", "/T", "/F"] in w.cmds and w.bridge_up
    assert not any("Stop-Process" in " ".join(c) or "-match" in " ".join(c) for c in w.cmds)
    s2, w2 = make(bridge=True, busy=True)
    ok, text = s2.do("heal", arg="bridge")
    assert not ok and "middle of a run" in text and not any(c[0] == "taskkill" for c in w2.cmds)


def test_skippy_core_is_repaired_over_ssh_with_its_one_fixed_command():
    s, w = make()
    ok, text = s.do("heal", arg="core")
    (ssh,) = [c for c in w.cmds if c[0] == "ssh"]
    assert ok and "repaired" in text and ssh[-1] == "repair" and ssh[-2] == "/opt/skippy-core/doctor/deployer.py" and "sudo" in ssh
    assert "BatchMode=yes" in ssh and ss.CORE_SSH in ssh
    w.core_says = '{"state": "repair-failed", "detail": "smoke: chat: no reply"}'
    assert s.do("heal", arg="core") == (False, "Skippy Core: smoke: chat: no reply")


def test_repair_puts_everything_back_in_order_and_never_discards_local_work():
    s, w = make(docker=False, bridge=True)
    ok, text = s.do("repair", arg="ollama,nonsense")
    assert ok, text
    order = [" ".join(c[:6]) for c in w.cmds if c[0] in ("git", "ssh", "taskkill") or c[:3] in (["docker", "compose", "up"], ["docker", "compose", "rm"])]
    git = [c for c in w.cmds if c[0] == "git"]
    assert any("merge" in c and "--ff-only" in c for c in git) and not any(x in c for c in git for x in ("reset", "checkout", "stash", "clean"))
    rebuild = order.index("docker compose up -d --build --remove-orphans")         # (Docker's clean restart brings the stack up first)
    assert order.index(next(o for o in order if o.startswith("git"))) < rebuild
    assert ["docker", "compose", "rm", "-f", "ollama"] in w.cmds and not any("nonsense" in c for c in w.cmds)
    assert order[-1].startswith("ssh") and "Docker restarted clean" in text and "main pulled." in text


def test_a_hung_docker_gets_the_clean_restart_by_itself_after_two_minutes_but_not_when_stopped_on_purpose():
    s, w = make(docker=False)
    assert s.watch_docker() is False                                     # first sight: it starts counting
    w.t[0] += ss.DOCKER_DOWN_HEAL_S - 1
    assert s.watch_docker() is False
    w.t[0] += 2
    assert s.watch_docker() is True and w.docker and s.results[-1]["summary"].startswith("By itself: Docker restarted clean")
    s2, w2 = make(docker=False)
    s2._save_resume(NOW[0])                                              # stopped on purpose, back at 7: hands off
    w2.t[0] += 10 * ss.DOCKER_DOWN_HEAL_S
    assert s2.watch_docker() is False and s2.watch_docker() is False and not w2.docker


def test_the_status_carries_skippy_pcs_gpu_for_the_patient_queue_and_nothing_when_there_is_no_nvidia_smi():
    s, w = make()
    real = w.run

    def run(cmd, timeout=120, cwd=None, env=None):
        if cmd[0] == "nvidia-smi":
            return 0, "16376, 12780, 86"
        return real(cmd, timeout, cwd, env)
    w.run = run
    s.run = run
    st = s.status()
    assert st["gpu_free_pct"] == 22 and st["gpu_util_pct"] == 86
    w.run = lambda cmd, timeout=120, cwd=None, env=None: (127, "not found") if cmd[0] == "nvidia-smi" else real(cmd, timeout, cwd, env)
    s.run = w.run
    assert "gpu_free_pct" not in s.status()


# ── skippy-pc's own UPS ──
MAINS = '{"BatteryStatus":2,"EstimatedChargeRemaining":100,"EstimatedRunTime":66}'
BATTERY = '{"BatteryStatus":1,"EstimatedChargeRemaining":97,"EstimatedRunTime":58}'


def test_the_ups_reading_rides_in_the_status_and_no_ups_means_nothing():
    s, w = make()
    assert s.ups() == {} and "ups_on_battery" not in s.status()
    w.battery = MAINS
    assert s.ups() == {"ups_on_battery": False, "ups_charge": 100, "ups_runtime_min": 66}
    assert s.status()["ups_charge"] == 100
    w.battery = '{"BatteryStatus":2,"EstimatedChargeRemaining":100,"EstimatedRunTime":71582788}'   # Windows' "unknown"
    assert s.ups()["ups_runtime_min"] == 0


def test_two_minutes_on_battery_stops_the_stack_and_shuts_windows_down():
    s, w = make(bridge=True)
    w.battery = BATTERY
    assert s.watch_power() is False                          # just went on battery: the clock starts
    w.t[0] += ss.UPS_SHUTDOWN_AFTER_S - 1
    assert s.watch_power() is False
    w.t[0] += 2
    assert s.watch_power() is True and s.power_down
    assert ["docker", "compose", "stop"] in [c[:3] for c in w.cmds] and w.cmds[-1][:2] == ["shutdown", "/s"]
    assert not w.bridge_up                                   # the bridge stopped by its PID first
    assert any("shutting down" in r["summary"] for p, _ in w.posts for r in p["results"])   # the portal heard first
    assert s.watch_power() is False                          # once


def test_the_power_coming_back_in_time_cancels_it():
    s, w = make()
    w.battery = BATTERY
    s.watch_power()
    w.t[0] += 60
    w.battery = MAINS
    assert s.watch_power() is False and s.on_battery_since is None
    w.battery = BATTERY
    w.t[0] += ss.UPS_SHUTDOWN_AFTER_S - 10
    assert s.watch_power() is False                          # a new outage starts its own two minutes
    assert not any(c[0] == "shutdown" for c in w.cmds)


def test_the_starter_ships_a_container_crash_to_the_portals_log_intake_and_keeps_it_if_the_portal_is_away(tmp_path):
    w = LogWorld()
    s = ss.Starter(run=w.run, http=w.http, token="tok", root=tmp_path, say=lambda m: None, clock=w.clock)
    s.watch_logs()                                                                 # round one: learns what is running
    assert w.logs == []
    w.clock_t[0] += ss.LOGS_EVERY_S + 1
    w.rows = [("skippytel-voice-gate", "exited", "Exited (137) 3 seconds ago")]
    w.portal_down = True
    s.watch_logs()                                                                 # it died, the portal is away: spooled, not lost
    assert w.logs == [] and (tmp_path / "state" / "logs" / "spool.jsonl").exists()
    w.clock_t[0] += ss.LOGS_EVERY_S + 1
    w.portal_down = False
    s.watch_logs()
    crash = w.logs[0]
    assert crash["kind"] == "crash" and crash["source"] == "docker/skippytel-voice-gate" and crash["fields"]["exit_code"] == 137
    assert w.hosts == {"skippy-pc"}


class LogWorld:
    def __init__(self):
        self.rows, self.logs, self.hosts, self.portal_down, self.clock_t = [("skippytel-voice-gate", "running", "Up 2 hours")], [], set(), False, [1000.0]

    def clock(self):
        return self.clock_t[0]

    def run(self, cmd, timeout=120, cwd=None, env=None):
        if cmd[:3] == ["docker", "ps", "-a"] and "{{json .}}" in cmd:
            import json as _j
            return 0, "\n".join(_j.dumps({"Names": n, "State": st, "Status": su, "Image": "img"}) for n, st, su in self.rows)
        if cmd[:3] == ["docker", "ps", "-a"]:
            return 0, "\n".join(st for _, st, _ in self.rows)
        if cmd[:2] == ["docker", "ps"]:
            return 0, "abc"
        if cmd[:2] == ["docker", "inspect"]:
            return 0, "\n".join(f"/{n} 0" for n, _, _ in self.rows)
        if cmd[:2] == ["docker", "logs"]:
            return 0, "whisper did not answer\nfatal: gone" if "--tail" in cmd else ""
        return 0, ""

    def http(self, url, data=None, headers=None, timeout=15):
        if self.portal_down:
            raise urllib.error.URLError("down")
        if url.endswith("/home/logs"):
            self.hosts.add(data["host"])
            self.logs.extend(data["records"])
        return {"ok": True}


def test_the_status_says_whether_openaudible_answers_and_whether_the_app_is_even_running():
    s, w = make()
    assert (s.status()["openaudible"], s.status()["openaudible_app"]) == (False, False)
    w.oa_running = True
    assert (s.status()["openaudible"], s.status()["openaudible_app"]) == (False, True)       # the app is there, the web server is off
    w.oa_web = True
    assert (s.status()["openaudible"], s.status()["openaudible_app"]) == (True, True)


def test_heal_openaudible_starts_the_app_only_when_it_is_not_running(tmp_path, monkeypatch):
    exe = tmp_path / "OpenAudible.exe"
    exe.write_text("x")
    monkeypatch.setattr(ss, "OPENAUDIBLE_EXE", str(exe))
    s, w = make()
    ok, text = s.do("heal", arg="openaudible")
    assert ok and "started" in text and w.spawned == [[str(exe)]] and w.oa_running
    assert not any(c[0] == "taskkill" for c in w.cmds)
    assert s.do("heal", arg="openaudible") == (True, "OpenAudible's web server is answering.")
    w.spawned.clear()
    s2, w2 = make()
    w2.oa_web_on_start = False                                      # started, but the web server preference is off
    ok, text = s2.do("heal", arg="openaudible")
    assert not ok and "Preferences > Web Server" in text


def test_heal_openaudible_never_touches_a_running_app_and_works_with_docker_down(tmp_path, monkeypatch):
    exe = tmp_path / "OpenAudible.exe"
    exe.write_text("x")
    monkeypatch.setattr(ss, "OPENAUDIBLE_EXE", str(exe))
    s, w = make(docker=False, containers=())
    w.oa_running = True                                             # it may be converting his library
    ok, text = s.do("heal", arg="openaudible")
    assert not ok and "left alone" in text and w.spawned == [] and not any(c[0] == "taskkill" for c in w.cmds)
    s2, w2 = make(docker=False, containers=())
    monkeypatch.setattr(ss, "OPENAUDIBLE_EXE", str(tmp_path / "nope.exe"))
    ok, text = s2.do("heal", arg="openaudible")
    assert not ok and "not installed" in text and w2.spawned == []
