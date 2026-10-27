import os
import sys
import tempfile
from pathlib import Path

# Before anything imports the engine or the bridge (whose module-level Workshop reads it): no test may ever write
# into the Captain's real conversations folder (D:\Aaron\conversations).
os.environ["WORKSHOP_CONVERSATIONS"] = tempfile.mkdtemp(prefix="skippy-conv-test-")

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import workshop_engine as engine  # noqa: E402
from bridge_testkit import make_door, make_shop  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_claude_login_probe(monkeypatch):
    """/health learns Claude's sign-in by running `claude auth status` in the background. No test may reach the real
    claude on this PC (it would depend on whoever is signed in, and the solid-gold tests call /health through the
    module-level Workshop), so by default the probe finds out nothing ("unknown"). The login tests give a probe their
    own runner, or put the real function back deliberately."""
    monkeypatch.setattr(engine, "run_auth_status", lambda claude_cmd, timeout: None)
    # Nor the real Skippy Core: a conversation's name is asked of it on a thread (chat_names.py). Here it is never there, so
    # every name is the fallback (the naming tests give the log a namer of their own).
    monkeypatch.setattr(engine.Workshop, "ask_core", lambda self, system, text: (_ for _ in ()).throw(RuntimeError("no Core in tests")))


@pytest.fixture(autouse=True)
def these_tests_exercise_the_claude_brain_unless_they_name_another(monkeypatch):
    """A request that names no brain is Skippy Core's coder now (the Captain, 2026-10-27: no brain named = purple). Most of these tests were
    written for the Claude brain's CLI path and start runs without naming one: they keep that path by asking for it. A test that names a brain
    (the core tests) is untouched, and test_default_brain_client_id.py checks the real default."""
    real = engine.Workshop.start

    def start(self, *args, brain=None, **kwargs):
        return real(self, *args, brain=brain or "claude", **kwargs)
    monkeypatch.setattr(engine.Workshop, "start", start)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return make_shop(tmp_path, monkeypatch)


@pytest.fixture
def door(shop, monkeypatch):
    call, server = make_door(shop, monkeypatch)
    yield call
    server.shutdown()
    server.server_close()
