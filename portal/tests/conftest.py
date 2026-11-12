import sys
from pathlib import Path

# Import portal modules the way the image does (flat, from /app).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import time  # noqa: E402

import pytest  # noqa: E402

_REAL_SLEEP = time.sleep


@pytest.fixture(autouse=True)
def _time_sleep_is_left_alone():
    """2026-11-03: a test that wrote `remote.time.sleep = ...` (remote.time IS the time module) left time.sleep a no-op for every test after it in
    that worker, and test_chat_pending's polling loop then never waited: red CI on three of four runs, depending on which worker got which
    file. A test that must change time.sleep uses monkeypatch; this names the one that didn't."""
    yield
    assert time.sleep is _REAL_SLEEP, "this test left time.sleep patched for every later test in the process: use monkeypatch.setattr"
