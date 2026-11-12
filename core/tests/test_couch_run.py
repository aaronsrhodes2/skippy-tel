"""The bedroom TV's kiosk loop (core/couch/run.sh): watch() ends a Moonlight that hangs instead of exiting (2026-11-03: a 4.5 hour black TV after
the OOM killer took the audio server and Moonlight's teardown stuck in "Stopping audio stream"), and leaves a healthy or cleanly ending one alone.
The scenarios run in couch_watch_check.sh against a fake Moonlight (a sleep) and a fake log; nothing of the kiosk is touched."""
import shutil
import subprocess
import sys

import pytest

from core_testlib import CORE

pytestmark = pytest.mark.skipif(sys.platform != "linux" or not shutil.which("bash"), reason="the kiosk is a Linux shell script")


def test_a_hung_teardown_or_a_flooded_decoder_is_ended_and_nothing_else_is():
    run = subprocess.run(["bash", str(CORE / "tests" / "couch_watch_check.sh"), str(CORE / "couch" / "run.sh")],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "all passed" in run.stdout


def test_the_stream_leaves_skippy_pcs_sound_on():
    """The kiosk's stream never ends, and with --no-audio-on-host Sunshine keeps Windows' default playback device on Steam Streaming Speakers
    the whole time and resets it whenever he picks his projector: every game on skippy-pc was silent (2026-11-04)."""
    text = (CORE / "couch" / "run.sh").read_text()
    args = text[text.index("flatpak run"):].split()
    assert "--audio-on-host" in args and "--no-audio-on-host" not in args


def test_the_loop_trims_its_log_and_starts_the_audio_services_before_each_stream():
    text = (CORE / "couch" / "run.sh").read_text()
    loop = text[text.index("wait_s=3"):]
    assert loop.index("tail -n 400") < loop.index("systemctl --user start") < loop.index("flatpak run")
    assert "pipewire-pulse.service" in loop and "reset-failed" in loop
