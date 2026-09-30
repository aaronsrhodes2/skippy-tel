"""EverQuest on the VM had no login server: the launcher starts it only when web-admin.launcher.runLoginserver is true and the
world registers with it at 127.0.0.1:5998. Only the database was carried over from home (where both were set by hand), so
nothing listened on 5998/5999 and every client login failed ("a connection to your server could not be reached"), while the
server's own log still said "online at ...". Found 2026-09-30 from a live snapshot: world, ucs and zones running, no loginserver.

The patch lives as Python inside infra/gcp-games/everquest.sh; these tests run THAT block, not a copy of it."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra" / "gcp-games"
SCRIPT = (INFRA / "everquest.sh").read_text(encoding="utf-8")
PATCH = re.search(r"""python3 - "\$AKK/server/eqemu_config.json" "\$IP" "\$UCS_PORT" "\$NAMES" <<'PY'\n(.*?)\nPY\n""", SCRIPT, re.S).group(1)

FRESH = {   # what AkkStack's fresh config really has (read from the live VM): three login servers, the first one legacy
    "server": {"world": {"address": "old", "shortname": "x", "longname": "y",
                         "loginserver1": {"host": "login.eqemulator.net", "port": "5998", "account": "", "password": "", "legacy": "1"},
                         "loginserver2": {"host": "127.0.0.1", "port": "5998", "account": "", "password": ""},
                         "loginserver3": {"host": "login.projecteq.net", "port": "5998", "account": "", "password": ""}},
               "ucs": {"host": "old", "port": "7778"}},
    "web-admin": {"launcher": {"minZoneProcesses": 3}},
}


def patch(tmp_path, config, ip="35.1.2.3", names="Solo EQ - Golden Era\tsoloeq\n"):
    path = tmp_path / "eqemu_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    subprocess.run([sys.executable, "-", str(path), ip, "7779", names], input=PATCH, text=True, check=True, timeout=30)
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_launcher_is_told_to_run_the_login_server_and_the_world_to_use_only_it(tmp_path):
    out = patch(tmp_path, FRESH)
    assert out["web-admin"]["launcher"]["runLoginserver"] is True
    world = out["server"]["world"]
    assert world["loginserver1"] == {"host": "127.0.0.1", "port": "5998", "account": "", "password": ""}   # local, no legacy flag
    assert not [k for k in world if k.startswith("loginserver") and k != "loginserver1"]                  # no public or duplicate entries
    assert out["web-admin"]["launcher"]["minZoneProcesses"] == 3                    # nothing else in the launcher block is lost


def test_two_entries_for_the_same_login_server_are_what_made_it_flap():
    """The live log: 'World tried to login but a server with that name already exists, destroying' about every 100 ms,
    the world logging 'Connected to Legacy Loginserver' then 'Connected to Loginserver' alternately. Two connections to one
    login server each replace the other. Pointing loginserver1 at localhost and stopping there (the first fix) leaves TWO
    local entries in AkkStack's fresh file, because loginserver2 is already 127.0.0.1."""
    def local_entries(world):
        return [k for k, v in world.items() if k.startswith("loginserver") and v["host"] == "127.0.0.1"]

    fresh_world = FRESH["server"]["world"]
    assert local_entries(fresh_world) == ["loginserver2"]                       # the fresh file: only loginserver2 is local
    naive = json.loads(json.dumps(FRESH))["server"]["world"]
    naive["loginserver1"]["host"] = "127.0.0.1"
    assert local_entries(naive) == ["loginserver1", "loginserver2"]             # the naive patch: two, the bug
    assert not any(v["host"] == "login.projecteq.net" for v in patch_world(FRESH).values() if isinstance(v, dict) and "host" in v)


def patch_world(config):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        return patch(Path(d), config)["server"]["world"]


def test_a_numeric_port_stays_numeric_and_credentials_are_never_touched(tmp_path):
    config = json.loads(json.dumps(FRESH))
    config["server"]["world"]["loginserver1"] = {"host": "x", "port": 5999, "account": "acct", "password": "pw", "legacy": "1"}
    out = patch(tmp_path, config)["server"]["world"]["loginserver1"]
    assert out == {"host": "127.0.0.1", "port": 5998, "account": "acct", "password": "pw"}


def test_a_config_with_no_launcher_or_login_block_gets_both(tmp_path):
    out = patch(tmp_path, {"server": {"world": {}}})
    assert out["web-admin"]["launcher"] == {"runLoginserver": True}
    assert out["server"]["world"]["loginserver1"] == {"host": "127.0.0.1", "port": "5998"}


def test_running_it_twice_changes_nothing_and_the_other_settings_still_apply(tmp_path):
    once = patch(tmp_path, FRESH)
    twice = patch(tmp_path, once)
    assert once == twice
    world = twice["server"]["world"]
    assert world["address"] == "35.1.2.3" and world["longname"] == "Solo EQ - Golden Era" and world["shortname"] == "soloeq"
    assert twice["server"]["ucs"] == {"host": "35.1.2.3", "port": "7779"}


def test_online_is_only_claimed_when_the_login_port_is_really_bound():
    """The old script printed 'online at ...' unconditionally, right after starting the launcher."""
    assert "login_up()" in SCRIPT and ":176F " in SCRIPT and "/proc/net/udp" in SCRIPT     # 0x176F = 5999, the client login port
    online = [m.start() for m in re.finditer(r'log "online at', SCRIPT)]
    assert len(online) == 1
    guard = SCRIPT.rindex("if login_up; then", 0, online[0])
    assert 0 < online[0] - guard < 120                                                    # it is the first line inside the guard
    assert 'WARNING: started, but nothing is listening on the login port 5999' in SCRIPT


def load_agent():
    spec = importlib.util.spec_from_file_location("ops_agent_eq", INFRA / "ops_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_ops_can_restart_everquest_without_a_reboot_from_the_newest_script(tmp_path, monkeypatch):
    agent = load_agent()
    target = tmp_path / "skippy-everquest"
    calls = []
    monkeypatch.setattr(agent, "EVERQUEST", str(target))
    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda req, timeout=0: (calls.append(req.full_url), FakeResponse(b"#!/bin/bash\necho hi\n"))[1])
    monkeypatch.setattr(agent, "run", lambda *argv, timeout=60: (calls.append(argv), "everquest: online")[1])
    out = agent.cmd_restart_game(["everquest"])
    assert calls[0].endswith("/attributes/everquest-script")                              # the newest copy, from metadata
    assert target.read_bytes().startswith(b"#!/bin/bash")
    assert calls[1] == (str(target), "start") and "re-ran the EverQuest start script" in out
    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda req, timeout=0: FakeResponse(b"<html>not a script</html>"))
    with pytest.raises(RuntimeError, match="isn't a script"):
        agent.cmd_restart_game(["everquest"])                                              # never runs whatever metadata holds


def test_other_games_still_restart_their_container(monkeypatch):
    agent = load_agent()
    seen = []
    monkeypatch.setattr(agent, "container", lambda name: name)
    monkeypatch.setattr(agent, "run", lambda *argv, timeout=60: (seen.append(argv), "ok")[1])
    assert "restarted valheim" in agent.cmd_restart_game(["valheim"])
    assert seen == [("docker", "restart", "--time", "60", "valheim")]
