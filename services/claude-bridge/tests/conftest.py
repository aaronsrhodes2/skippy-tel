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


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return make_shop(tmp_path, monkeypatch)


@pytest.fixture
def door(shop, monkeypatch):
    call, server = make_door(shop, monkeypatch)
    yield call
    server.shutdown()
    server.server_close()
