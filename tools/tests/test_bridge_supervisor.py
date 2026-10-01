"""tools/bridge_supervisor.sh keeps the solid-gold bridge running on skippy-pc.

Why it exists (incident 2026-09-30): the bridge was `make bridge` in a terminal, the one link of the private Skippy's chain
that nothing restarted. When the chat froze, "is the bridge up?" could only be answered at the PC, and a crash, a closed
window or a reboot meant no remote hands until somebody sat there.

The loop and its backoff are tested twice: the logic itself with a fake clock and fake child (the script is sourced, so
each function can be driven alone), and the wiring with a real subprocess against a stub that stands in for bridge.py.
Nothing here starts the real bridge, and every state directory is a temp folder.
"""
from __future__ import annotations

import http.server
import os
import re
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from bashrun import BASH, bash_env, free_port, posix, run_bash

SCRIPT = Path(__file__).resolve().parents[1] / "bridge_supervisor.sh"
pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash (Git for Windows' on Windows)")

STUB = """#!/usr/bin/env bash
# Stands in for bridge.py. What run N does comes from $STUB_PLAN: "fail", "sleepN" or "hang".
echo "start $$" >> "$STUB_DIR/starts.txt"
n=$(wc -l < "$STUB_DIR/starts.txt")
read -r -a plan <<< "$STUB_PLAN"
mode=${plan[$((n - 1))]:-fail}
case "$mode" in
  fail) exit 3 ;;
  sleep*) sleep "${mode#sleep}"; exit 0 ;;
  hang) exec sleep 300 ;;
esac
"""

FAKE_BRIDGE = """import http.server, os, sys
open(os.path.join(os.environ["STUB_DIR"], "starts.txt"), "a").write("start\\n")
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


def wait_for(cond, timeout=20.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


def port_open(port: int) -> bool:
    with socket.socket() as s:                    # a fresh socket each time: a connected one answers EISCONN forever
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b'{"status": "ok", "service": "claude-bridge"}')

    def log_message(self, *args):
        pass


def serve_health(port: int) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Rig:
    """One supervisor in a temp folder, running a stub (or a fake bridge) instead of bridge.py."""

    def __init__(self, tmp_path: Path, plan: str = "fail", **settings):
        if " " in posix(tmp_path):
            pytest.skip("the supervisor splits BRIDGE_SUP_CMD on spaces")
        self.dir, self.state, self.port = tmp_path, tmp_path / "state", free_port()
        self.stub = tmp_path / "stub.sh"
        self.stub.write_text(STUB, encoding="utf-8", newline="\n")
        self.env = {
            "BRIDGE_SUP_STATE_DIR": posix(self.state),
            "BRIDGE_SUP_CMD": f"bash {posix(self.stub)}",
            "STUB_DIR": posix(tmp_path), "STUB_PLAN": plan,
            "CLAUDE_BRIDGE_PORT": str(self.port),
            "BRIDGE_SUP_HEALTH_FAILS": "0", "BRIDGE_SUP_MIN_DELAY": "1", "BRIDGE_SUP_MAX_DELAY": "2",
        }
        self.env.update({f"BRIDGE_SUP_{k.upper()}": str(v) for k, v in settings.items()})
        self.procs: list[subprocess.Popen] = []

    def run(self, *args: str, timeout: float = 60):
        return run_bash([posix(SCRIPT), *args], env=self.env, timeout=timeout)

    def start(self) -> subprocess.Popen:
        proc = subprocess.Popen([BASH, posix(SCRIPT)], env=bash_env(self.env), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        self.procs.append(proc)
        return proc

    @property
    def starts(self) -> int:
        f = self.dir / "starts.txt"
        return len(f.read_text().splitlines()) if f.exists() else 0

    @property
    def log(self) -> str:
        f = self.state / "bridge-supervisor.log"
        return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""

    def close(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()


@pytest.fixture
def rig(tmp_path):
    made: list[Rig] = []

    def make(plan="fail", **settings) -> Rig:
        r = Rig(tmp_path, plan, **settings)
        made.append(r)
        return r

    yield make
    for r in made:
        r.close()


# ── the logic, with the script sourced ───────────────────────────────────────

def snippet(tmp_path: Path, body: str, **env) -> subprocess.CompletedProcess:
    """Source the script, point its state at a temp folder, run `body`."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    prelude = f'source "{posix(SCRIPT)}"; init_config; probe() {{ return 1; }}\n'
    return run_bash(["-c", prelude + textwrap.dedent(body)],
                    env={"BRIDGE_SUP_STATE_DIR": posix(state), "CLAUDE_BRIDGE_PORT": str(free_port()),
                         "BRIDGE_SUP_ECHO": "1", **env})                  # ECHO: the log lines come back on stdout too


def test_the_ladder_is_3_6_12_24_48_then_60_forever(tmp_path):
    done = snippet(tmp_path, """
        d=$MIN_DELAY
        for i in 1 2 3 4 5 6 7 8; do set -- $(backoff_step "$d" 1); printf '%s ' "$1"; d=$2; done
    """)
    assert done.stdout.split() == ["3", "6", "12", "24", "48", "60", "60", "60"], done.stderr


def test_five_minutes_up_starts_the_ladder_over_and_299_seconds_does_not(tmp_path):
    done = snippet(tmp_path, 'backoff_step 48 300; backoff_step 48 299; backoff_step 60 9999')
    assert done.stdout.split("\n")[:3] == ["3 6", "48 60", "3 6"], done.stderr


def test_nonsense_settings_fall_back_to_the_defaults_and_a_max_below_the_min_is_raised(tmp_path):
    done = snippet(tmp_path, 'echo "$MIN_DELAY $MAX_DELAY $RESET_AFTER $PORT"',
                   BRIDGE_SUP_MIN_DELAY="abc", BRIDGE_SUP_MAX_DELAY="-5", BRIDGE_SUP_RESET_AFTER="", CLAUDE_BRIDGE_PORT="x")
    assert done.stdout.split() == ["3", "60", "300", "9337"], done.stderr
    done = snippet(tmp_path, 'echo "$MIN_DELAY $MAX_DELAY"', BRIDGE_SUP_MIN_DELAY="5", BRIDGE_SUP_MAX_DELAY="2")
    assert done.stdout.split() == ["5", "5"], done.stderr


def test_the_loop_waits_by_the_ladder_and_a_long_run_resets_it(tmp_path):
    """A fake clock and a fake child: eight crashes (the 8th after 400 s up) -> waits 3 6 12 24 48 60 60, then 3 again."""
    done = snippet(tmp_path, """
        MAX_RUNS=9
        UPS=(1 1 1 1 1 1 1 400 1); i=0; clock=0
        now() { echo "$clock"; }
        run_child() { clock=$((clock + UPS[i])); i=$((i + 1)); return 7; }
        nap() { echo "nap $1"; }
        supervise_loop
    """)
    assert [ln for ln in done.stdout.splitlines() if ln.startswith("nap")] == [
        "nap 3", "nap 6", "nap 12", "nap 24", "nap 48", "nap 60", "nap 60", "nap 3"], done.stdout + done.stderr
    assert "exited (code 7) after 400s" in done.stdout


def test_stop_ends_the_loop_without_another_start_or_wait(tmp_path):
    done = snippet(tmp_path, """
        run_child() { echo ran; STOP=1; return 143; }
        nap() { echo napped; }
        supervise_loop
    """)
    assert done.stdout.split() == ["ran"], done.stdout + done.stderr


def test_a_bridge_that_answers_is_watched_and_replaced_only_after_it_stays_silent(tmp_path):
    """probe answers, answers, then 3 silences in a row -> take over. One lone silence (the 3rd probe) does not count."""
    done = snippet(tmp_path, """
        WATCH_FAILS=3; n=0; SEQ=(0 0 1 0 1 1 1)       # 0 = answers
        probe() { n=$((n + 1)); [ "${SEQ[n-1]:-1}" = 0 ]; }
        nap() { echo nap; }
        hold_while_bridge_answers; echo "released after $n probes"
    """)
    assert "not starting a second copy" in done.stdout
    assert done.stdout.strip().splitlines()[-1] == "released after 7 probes", done.stdout + done.stderr


def test_with_nothing_answering_the_bridge_is_started_at_once(tmp_path):
    done = snippet(tmp_path, 'nap() { echo napped; }; hold_while_bridge_answers; echo started')
    assert done.stdout.split() == ["started"], done.stdout + done.stderr


def test_log_is_copy_truncated_at_the_limit(tmp_path):
    done = snippet(tmp_path, """
        LOG_MAX=100
        head -c 50 /dev/zero | tr '\\0' a >> "$LOG"; rotate_log; echo "small: $(wc -c < "$LOG") $(test -f "$LOG.1" && echo rotated)"
        head -c 200 /dev/zero | tr '\\0' b >> "$LOG"; rotate_log; echo "big: $(wc -c < "$LOG") $(wc -c < "$LOG.1")"
    """)
    lines = [ln.replace(" ", "") for ln in done.stdout.splitlines()]
    assert lines[0] == "small:50", done.stdout + done.stderr
    assert lines[1] == "big:0250", done.stdout + done.stderr


# ── the wiring, with a real subprocess and a stub ────────────────────────────

def test_every_exit_is_followed_by_a_restart_after_a_growing_wait(rig):
    r = rig("fail fail fail", max_runs=3)
    t0 = time.time()
    done = r.run()
    took = time.time() - t0
    assert done.returncode == 0, done.stdout + done.stderr
    assert r.starts == 3
    assert re.findall(r"again in (\d+)s", r.log) == ["1", "2", "2"], r.log      # MIN=1, MAX=2 in this rig
    assert "stopping after 3 runs" in r.log
    assert took >= 2.5                                                          # it really waited 1 s and 2 s


def test_a_second_copy_is_never_started_while_a_bridge_answers_and_one_is_started_when_it_stops(rig):
    r = rig("sleep1", max_runs=1, watch_poll=1, watch_fails=2)
    server = serve_health(r.port)                                               # "a hand-started `make bridge`"
    try:
        proc = r.start()
        assert wait_for(lambda: "not starting a second copy" in r.log, 15), r.log
        time.sleep(2.5)                                                         # long enough to have started one, were it going to
        assert r.starts == 0, r.log
    finally:
        server.shutdown()
        server.server_close()                                                   # it "dies": the port stops answering
    assert wait_for(lambda: r.starts == 1, 20), r.log                           # and only now does the supervisor start its own
    assert "taking over" in r.log
    proc.wait(timeout=30)
    assert r.starts == 1


def test_a_second_supervisor_is_refused_while_one_runs(rig):
    r = rig("sleep4", max_runs=1)
    first = r.start()
    assert wait_for(lambda: r.starts == 1, 20), r.log
    second = r.run(timeout=30)                                                  # same state dir: same pid file
    assert second.returncode == 0
    assert "another supervisor is already running" in second.stdout
    assert r.starts == 1                                                        # it started nothing
    first.wait(timeout=30)
    assert not (r.state / "bridge-supervisor.pid").exists()                     # and a clean exit leaves no stale lock


def test_a_stale_pid_file_does_not_block_a_new_supervisor(rig):
    r = rig("sleep1", max_runs=1)
    r.state.mkdir()
    (r.state / "bridge-supervisor.pid").write_text("999999\n")                  # no such process
    done = r.run()
    assert done.returncode == 0 and r.starts == 1, done.stdout + done.stderr


def test_a_bridge_that_is_alive_but_mute_is_killed_and_restarted(rig):
    """The wedge case: the process runs, /health never answers (nothing listens on the rig's port)."""
    r = rig("hang hang", max_runs=2, health_fails=2, health_every=1, health_grace=0, kill_grace=0)
    done = r.run(timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert r.starts == 2, r.log
    assert r.log.count("looks wedged") == 2, r.log
    assert "did not answer (1 of 2)" in r.log and "did not answer (2 of 2)" in r.log


def test_a_healthy_bridge_is_left_alone_and_a_stop_signal_stops_it_too(tmp_path):
    """A fake bridge that answers /health stays up; TERM to the supervisor stops the bridge as well (no orphan)."""
    if " " in posix(tmp_path):
        pytest.skip("the supervisor splits BRIDGE_SUP_CMD on spaces")
    fake = tmp_path / "fake_bridge.py"
    fake.write_text(FAKE_BRIDGE, encoding="utf-8")
    port, state = free_port(), tmp_path / "state"
    env = {
        "BRIDGE_SUP_STATE_DIR": posix(state), "STUB_DIR": posix(tmp_path), "CLAUDE_BRIDGE_PORT": str(port),
        "BRIDGE_SUP_CMD": f"{posix(sys.executable)} {posix(fake)} {port}",
        "BRIDGE_SUP_HEALTH_FAILS": "4", "BRIDGE_SUP_HEALTH_EVERY": "1", "BRIDGE_SUP_HEALTH_GRACE": "1",
    }
    driver = f'bash "{posix(SCRIPT)}" > "{posix(tmp_path)}/out.txt" 2>&1 & sup=$!\nsleep 6\nkill -TERM $sup\nwait $sup\necho "exit=$?"\n'
    done = run_bash(["-c", driver], env=env, timeout=90)
    log = (state / "bridge-supervisor.log").read_text(encoding="utf-8", errors="replace")
    assert "exit=0" in done.stdout, done.stdout + done.stderr + log
    assert (tmp_path / "starts.txt").read_text().count("start") == 1, log      # one start in six seconds: never "wedged"
    assert "wedged" not in log and "told to stop" in log, log
    assert wait_for(lambda: not port_open(port), 10), log                       # the bridge went down with the supervisor


def test_an_unwritable_state_dir_does_not_stop_the_bridge_starting(rig, tmp_path):
    (tmp_path / "afile").write_text("x")
    r = rig("sleep1", max_runs=1, state_dir=posix(tmp_path / "afile" / "state"))   # mkdir under a FILE: cannot work
    done = r.run()
    assert done.returncode == 0 and r.starts == 1, done.stdout + done.stderr


def test_status_says_whether_the_bridge_answers_and_exits_accordingly(rig):
    r = rig()
    down = r.run("--status")
    assert down.returncode == 1 and "NOT answering" in down.stdout, down.stdout + down.stderr
    assert "supervisor: not running" in down.stdout
    server = serve_health(r.port)
    try:
        up = r.run("--status")
    finally:
        server.shutdown()
        server.server_close()
    assert up.returncode == 0 and "answering on port" in up.stdout and '"service": "claude-bridge"' in up.stdout, up.stdout + up.stderr


@pytest.mark.skipif(os.name != "nt", reason="Linux's bash does not accept CRLF scripts; the Captain's Git Bash does")
def test_a_windows_checkout_with_crlf_line_endings_still_runs(rig, tmp_path):
    """core.autocrlf=true on skippy-pc checks every .sh out with CRLF line endings (scripts/games.sh is one today)."""
    r = rig("fail", max_runs=1)
    crlf = tmp_path / "crlf" / "tools"
    crlf.mkdir(parents=True)
    (crlf / "bridge_supervisor.sh").write_bytes(SCRIPT.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    done = run_bash([posix(crlf / "bridge_supervisor.sh")], env=r.env, timeout=60)
    assert done.returncode == 0 and r.starts == 1, done.stdout + done.stderr + r.log


def test_an_unknown_option_is_refused(rig):
    done = rig().run("--bogus")
    assert done.returncode == 2 and "unknown option" in done.stderr
