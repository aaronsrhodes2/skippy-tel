"""Watchdog fixes around the way the VM goes to sleep.

  1. stop_everything() ran four commands and threw every exit status and every line of output away, so a `docker
     compose stop` that failed or hit its 400 s cap left nothing in the journal before the power went off.
  2. Every watchdog sleep posted Discord's "went to sleep" TWICE: once from the watchdog (with the real reason) and
     once from shutdown.sh, the GCE shutdown script that runs on that very poweroff (with the bare reason "stopped",
     which then overwrote the informative one on the pinned post). Now the watchdog leaves a note when Discord has
     been told and shutdown.sh stays quiet when it finds one; every other way the VM stops has no note and still gets
     its one "stopped" post.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra" / "gcp-games"
SHUTDOWN = INFRA / "shutdown.sh"

spec = importlib.util.spec_from_file_location("idle_watchdog_fix_stop", INFRA / "idle_watchdog.py")
iw = importlib.util.module_from_spec(spec)
sys.modules["idle_watchdog_fix_stop"] = iw          # dataclasses need the module registered
spec.loader.exec_module(iw)


class Stops:
    """Stands in for run(): records every command, answers each program with what the test says."""

    def __init__(self, **results):
        self.calls, self.results = [], results

    def __call__(self, argv, timeout=60, merge=False):
        self.calls.append((list(argv), timeout))
        key = "compose" if argv[:2] == ["docker", "compose"] else argv[0]
        return self.results.get(key, (0, ""))

    def commands(self, prog):
        return [argv for argv, _ in self.calls if argv[0] == prog]

    def journal(self):
        return [argv[-1] for argv, _ in self.calls if argv[0] == "logger"]

    def order(self):
        return [("compose" if argv[:2] == ["docker", "compose"] else argv[0]) for argv, _ in self.calls
                if argv[0] != "logger"]


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(iw, "STATE", tmp_path / "state")
    return tmp_path / "state"


# ── 1. every step is logged ──────────────────────────────────────────────────

def test_every_graceful_stop_step_is_logged_with_its_exit_status_and_its_output(state):
    system = Stops(compose=(124, "docker: timed out after 400s"),
                   **{iw.EVERQUEST: (1, "stopping\nsaving\nerror: world save failed: disk full")},
                   **{iw.DISCORD: (0, "")})
    iw.stop_everything("nobody played for 30 min", system)
    said = system.journal()
    compose = next(line for line in said if "docker compose stop" in line)
    assert "exit 124" in compose and "timed out after 400s" in compose
    everquest = next(line for line in said if "skippy-everquest stop" in line)
    assert "exit 1" in everquest and "world save failed: disk full" in everquest
    assert any("skippy-discord offline" in line and "exit 0" in line for line in said)
    assert system.order() == ["compose", iw.EVERQUEST, iw.DISCORD, "systemctl"]


def test_a_stop_that_worked_says_so_too(state):
    system = Stops(compose=(0, "Container valheim  Stopping\nContainer valheim  Stopped"))
    iw.stop_everything("8 h limit reached", system)
    line = next(line for line in system.journal() if "docker compose stop" in line)
    assert "exit 0" in line and "Stopped" in line


def test_only_the_tail_of_a_long_output_is_logged_on_one_line(state):
    noisy = "\n".join(f"line {n} " + "x" * 400 for n in range(60))
    system = Stops(compose=(1, noisy))
    iw.stop_everything("why", system)
    line = next(line for line in system.journal() if "docker compose stop" in line)
    assert "\n" not in line and len(line) < 900
    assert "line 59" in line and "line 0 " not in line


def test_no_failure_of_any_step_stops_the_power_off(state):
    system = Stops(compose=(124, "timed out"), **{iw.EVERQUEST: (127, "not found"), iw.DISCORD: (1, "discord valheim: down"),
                                                  "logger": (127, "logger: not found")})
    iw.stop_everything("nobody played for 30 min", system)
    assert system.order() == ["compose", iw.EVERQUEST, iw.DISCORD, "systemctl"]
    assert system.commands("systemctl") == [["systemctl", "poweroff"]]


def test_the_power_off_is_logged_when_it_comes_back(state):
    system = Stops(systemctl=(1, "Failed to connect to bus"))
    iw.stop_everything("why", system)
    assert any("systemctl poweroff" in line and "exit 1" in line and "Failed to connect to bus" in line
               for line in system.journal())


# ── 2. Discord hears about a sleep once ──────────────────────────────────────

def test_the_watchdog_leaves_a_note_that_discord_was_told_and_why(state):
    iw.stop_everything("nobody played for 30 min", Stops())
    assert (state / "offline-posted").read_text(encoding="utf-8").strip() == "nobody played for 30 min"


def test_a_post_that_failed_leaves_no_note_so_the_shutdown_script_still_tries(state):
    iw.stop_everything("nobody played for 30 min", Stops(**{iw.DISCORD: (1, "discord valheim: HTTP Error 500")}))
    assert not (state / "offline-posted").exists()


def test_the_heads_up_before_the_backstop_is_not_a_sleep_post(state):
    system = Stops()
    cfg = iw.Config(idle_limit_min=30, max_hours=8)
    iw.announce_backstop(cfg, system)
    assert system.commands(iw.DISCORD)[0][1] == "notice" and "powers off in 15 minutes" in system.commands(iw.DISCORD)[0][2]
    assert not (state / "offline-posted").exists()


def test_an_unwritable_state_directory_does_not_stop_the_power_off(tmp_path, monkeypatch):
    blocker = tmp_path / "state"
    blocker.write_text("a file where the directory should be")
    monkeypatch.setattr(iw, "STATE", blocker)
    system = Stops()
    iw.stop_everything("why", system)
    assert system.order()[-1] == "systemctl"


def _bash_runs_scripts() -> bool:
    bash = shutil.which("bash")
    if not bash:
        return False
    try:
        out = subprocess.run([bash, "-c", "command -v find head >/dev/null && echo ok"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "ok"


needs_bash = pytest.mark.skipif(not _bash_runs_scripts(), reason="needs bash, find and head")

PRELUDE = r"""
docker()  { echo "docker $*" >> "$FAKE_LOG"; }
logger()  { echo "logger $*" >> "$FAKE_LOG"; }
curl()    { case "$*" in */attributes/games) echo "$FAKE_GAMES" ;; *) return 22 ;; esac; }
"""


def run_shutdown(tmp_path, *, games="valheim", note=None, note_age_s=0):
    """shutdown.sh as GCE runs it when the VM stops, with docker, curl, logger and the two skippy-* tools faked and
    /run/skippy-games moved into tmp_path. Returns the arguments each skippy-discord call got."""
    tools, run_dir = tmp_path / "bin", tmp_path / "run"
    tools.mkdir(exist_ok=True)
    run_dir.mkdir(exist_ok=True)
    for name in ("skippy-discord", "skippy-everquest"):
        tool = tools / name
        tool.write_text(f'#!/bin/bash\necho "{name} $*" >> "$FAKE_LOG"\n', encoding="utf-8")
        tool.chmod(0o755)
    if note is not None:
        (run_dir / "offline-posted").write_text(note, encoding="utf-8")
        past = time.time() - note_age_s
        os.utime(run_dir / "offline-posted", (past, past))
    text = SHUTDOWN.read_text(encoding="utf-8")
    assert "/usr/local/bin/skippy-discord" in text
    text = text.replace("/usr/local/bin/", tools.as_posix() + "/").replace("/run/skippy-games", run_dir.as_posix())
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    env = {**os.environ, "FAKE_LOG": log.as_posix(), "FAKE_GAMES": games}
    done = subprocess.run([shutil.which("bash"), "-c", PRELUDE + text], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    lines = log.read_text(encoding="utf-8").splitlines()
    return [ln.removeprefix("skippy-discord ") for ln in lines if ln.startswith("skippy-discord ")], lines


@needs_bash
def test_a_stop_nobody_announced_still_gets_its_one_stopped_post(tmp_path):
    posts, lines = run_shutdown(tmp_path)                           # the portal's Stop button, `games.sh down`, gcloud
    assert posts == ["offline stopped"]
    assert any(ln.startswith("docker compose") and " stop " in ln for ln in lines)      # and the worlds are still saved
    assert "skippy-everquest stop" in lines


@needs_bash
def test_a_watchdog_sleep_is_not_announced_a_second_time(tmp_path):
    posts, lines = run_shutdown(tmp_path, note="nobody played for 30 min")
    assert posts == []
    assert any(ln.startswith("docker compose") for ln in lines) and "skippy-everquest stop" in lines   # still saves


@needs_bash
def test_a_note_from_long_ago_is_not_a_reason_to_stay_quiet(tmp_path):
    posts, _ = run_shutdown(tmp_path, note="nobody played for 30 min", note_age_s=3 * 3600)
    assert posts == ["offline stopped"]


@needs_bash
def test_a_session_without_games_posts_nothing(tmp_path):
    assert run_shutdown(tmp_path, games="")[0] == []


@needs_bash
def test_a_watchdog_sleep_end_to_end_posts_exactly_once_with_the_real_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(iw, "STATE", tmp_path / "run")             # the watchdog and the shutdown script share it
    system = Stops()
    iw.stop_everything("nobody played for 30 min", system)
    watchdog_posts = [argv[1:] for argv in system.commands(iw.DISCORD)]
    shutdown_posts, _ = run_shutdown(tmp_path, games="valheim factorio")
    assert watchdog_posts == [["offline", "nobody played for 30 min"]]
    assert shutdown_posts == []                                    # nothing overwrites the reason with "stopped"


@needs_bash
def test_a_watchdog_whose_post_failed_leaves_the_shutdown_script_to_say_it(tmp_path, monkeypatch):
    monkeypatch.setattr(iw, "STATE", tmp_path / "run")
    iw.stop_everything("nobody played for 30 min", Stops(**{iw.DISCORD: (1, "discord valheim: HTTP Error 500")}))
    assert run_shutdown(tmp_path)[0] == ["offline stopped"]


def test_shutdown_sh_and_the_watchdog_use_the_same_note():
    text = SHUTDOWN.read_text(encoding="utf-8")
    assert f"/run/skippy-games/{iw.OFFLINE_NOTE}" in text
    assert iw.OFFLINE_NOTE == "offline-posted"
