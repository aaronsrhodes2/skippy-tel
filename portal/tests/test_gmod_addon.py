"""Our Garry's Mod addon (sigma-ground, stage/gmod/addon) goes onto the server's persistent disk before srcds starts, at every boot and
at every `ops restart-game garrysmod`, and a failure to fetch it never stops the game from starting.

  * infra/gcp-games/gmod_addon.sh: one authenticated GET of the addon's tarball from the games bucket -> addons/mentat-stage; run here for
    real against a folder that stands in for the bucket (skippy-core's publisher writes it: core/cd/cd.py publish_tarball).
  * startup.sh runs it for garrysmod only, before the containers are created; ops_agent.py runs it between stop and start.
  * the file times stay put when the addon hasn't changed (game_backup.py fingerprints mtimes and backs up garrysmod/addons).
  * nothing in it touches GitHub or git (docs/no-github.md).
"""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from test_fix_watchdog_startup import needs_bash, run_boot

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra" / "gcp-games"
SCRIPT = INFRA / "gmod_addon.sh"
needs_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("bash", "curl", "tar")), reason="needs bash, curl and tar")


# ── the plumbing ─────────────────────────────────────────────────────────────

def test_the_script_is_shipped_in_metadata_and_fetched_by_the_boot_script_and_the_ops_agent():
    assert 'gmod-addon       = file("${path.module}/gmod_addon.sh")' in (INFRA / "main.tf").read_text(encoding="utf-8")
    startup = (INFRA / "startup.sh").read_text(encoding="utf-8")
    assert "meta gmod-addon > /usr/local/bin/skippy-gmod-addon" in startup
    assert startup.index("skippy-gmod-addon") < startup.index("compose $PROFILES up -d")           # before the containers exist
    assert startup.index("skippy-game-backup backup") < startup.index("skippy-gmod-addon")         # the backup is the state the session started from
    assert '"/gmod-addon"' in (INFRA / "ops_agent.py").read_text(encoding="utf-8")
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/bash")


def test_the_script_can_never_fail_the_boot():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "set -e" not in text and text.rstrip().endswith("exit 0")
    assert b"\r" not in SCRIPT.read_bytes()                       # a CRLF copy would not run on the VM ("#!/bin/bash" + CR)


# ── the script, run for real ─────────────────────────────────────────────────

def test_the_script_names_no_github_and_runs_no_git():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "github.com" not in text and "git clone" not in text and "git fetch" not in text and "git init" not in text
    assert "storage.googleapis.com/skippy-tel-game-assets/gmod-addon" in text                         # the games bucket


class Bucket:
    """A folder standing in for gs://skippy-tel-game-assets/gmod-addon, written the way core/cd/cd.py publish_tarball writes it:
    <commit>.tar.gz and master.tar.gz, each holding addon/<files> and SOURCE ("<tree> <commit>")."""

    def __init__(self, path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.count = 0

    @property
    def url(self):
        return self.path.as_uri()

    def archive(self, files, source, links=()):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(f"addon/{name}")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            for name, target in links:
                info = tarfile.TarInfo(f"addon/{name}")
                info.type = tarfile.SYMTYPE
                info.linkname = target
                t.addfile(info)
            if source is not None:
                data = source.encode()
                info = tarfile.TarInfo("SOURCE")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def publish(self, files, name="master", source=True, links=()):
        self.count += 1
        tree = hashlib.sha1(json.dumps(sorted(files.items())).encode()).hexdigest()
        commit = hashlib.sha1(f"{tree}{self.count}".encode()).hexdigest()
        data = self.archive(files, f"{tree} {commit}\n" if source is True else source, links)
        for n in (commit, name):
            (self.path / f"{n}.tar.gz").write_bytes(data)
        return commit


LUA = "lua/autorun/mentat_stage.lua"


@pytest.fixture
def origin(tmp_path):
    bucket = Bucket(tmp_path / "bucket")
    bucket.v1 = bucket.publish({LUA: "print('v1')\n"})
    return bucket


def refresh(tmp_path, base_url, **extra):
    env = {**os.environ, "DATA": (tmp_path / "data").as_posix(), "GMOD_ADDON_BASE": base_url, "GMOD_ADDON_OWNER": f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "0:0", **extra}
    done = subprocess.run([shutil.which("bash"), str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr                                           # ALWAYS 0
    return done.stdout


def installed(tmp_path):
    return tmp_path / "data" / "garrysmod" / "addons" / "mentat-stage"


@needs_tools
def test_a_fresh_boot_installs_only_the_addon_folder_and_leaves_no_scratch_behind(tmp_path, origin):
    out = refresh(tmp_path, origin.url)
    assert "mentat-stage updated none ->" in out
    assert (installed(tmp_path) / LUA).read_text().strip() == "print('v1')"
    assert not (installed(tmp_path) / "SOURCE").exists() and not (installed(tmp_path) / "addon").exists()
    assert (installed(tmp_path) / ".source").read_text().split()[1] == origin.v1
    assert sorted(p.name for p in (tmp_path / "data" / "garrysmod").iterdir()) == ["addons"]         # no scratch folder left


@needs_tools
def test_an_unchanged_addon_is_not_touched_so_the_backup_does_not_archive_it_at_every_boot(tmp_path, origin):
    refresh(tmp_path, origin.url)
    f = installed(tmp_path) / LUA
    os.utime(f, (1_000_000_000, 1_000_000_000))
    out = refresh(tmp_path, origin.url)
    assert "already at" in out and f.stat().st_mtime == 1_000_000_000


@needs_tools
def test_a_new_master_shows_up_at_the_next_start(tmp_path, origin):
    refresh(tmp_path, origin.url)
    origin.publish({LUA: "print('v2')\n", "lua/new.lua": "x\n"})
    out = refresh(tmp_path, origin.url)
    assert "updated" in out and "already" not in out
    assert (installed(tmp_path) / LUA).read_text().strip() == "print('v2')"
    assert (installed(tmp_path) / "lua" / "new.lua").exists()


@needs_tools
def test_when_the_bucket_cannot_be_reached_the_previous_copy_stays_and_the_script_still_succeeds(tmp_path, origin):
    refresh(tmp_path, origin.url)
    out = refresh(tmp_path, (tmp_path / "nowhere").as_uri())
    assert "couldn't fetch" in out and "keeping the addon as it is" in out
    assert (installed(tmp_path) / LUA).exists()


@needs_tools
def test_a_first_boot_with_no_network_starts_without_the_addon_and_says_so(tmp_path):
    out = refresh(tmp_path, (tmp_path / "nowhere").as_uri())
    assert "couldn't fetch" in out and "(none)" in out and not installed(tmp_path).exists()


@needs_tools
@pytest.mark.parametrize("files, source", [
    ({"README.md": "no lua here\n"}, True),                                          # nothing to run
    ({LUA: "print('x')\n"}, None),                                                   # no SOURCE
    ({LUA: "print('x')\n"}, "not a tree and a commit\n"),                           # a SOURCE that isn't ids
])
def test_an_archive_that_is_not_the_addon_keeps_what_there_is(tmp_path, origin, files, source):
    refresh(tmp_path, origin.url)
    origin.publish(files, source=source)
    out = refresh(tmp_path, origin.url)
    assert "keeping the addon as it is" in out
    assert (installed(tmp_path) / LUA).read_text().strip() == "print('v1')"


@needs_tools
@pytest.mark.skipif(os.name == "nt", reason="Windows cannot create the symlink the archive carries: skippy-core's CI (Linux) runs this")
def test_links_in_the_archive_are_dropped_because_this_runs_as_root_on_outside_content(tmp_path, origin):
    origin.publish({LUA: "print('v3')\n"}, links=[("lua/passwd", "/etc/passwd")])
    refresh(tmp_path, origin.url)
    assert (installed(tmp_path) / LUA).exists() and not (installed(tmp_path) / "lua" / "passwd").exists()
    assert not any(q.is_symlink() for q in installed(tmp_path).rglob("*"))


# ── startup.sh's tail, run for real (the same fakes as test_fix_watchdog_startup.py) ───────────────

@needs_bash
def test_garrysmod_boot_fetches_the_script_and_runs_it_before_the_containers(tmp_path):
    _, out, calls, created = run_boot(tmp_path, "garrysmod valheim")
    assert (tmp_path / "bin" / "skippy-gmod-addon").read_text().startswith("# gmod-addon")          # the fake `meta` answered
    assert sorted(created) == ["garrysmod", "valheim"] and "started: garrysmod valheim" in out.splitlines()


@needs_bash
def test_other_games_do_not_fetch_the_addon_script(tmp_path):
    run_boot(tmp_path, "valheim")
    assert not (tmp_path / "bin" / "skippy-gmod-addon").exists()


# ── ops restart-game garrysmod ───────────────────────────────────────────────

class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def load_agent():
    spec = importlib.util.spec_from_file_location("ops_agent_gmod", INFRA / "ops_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def agent_with(monkeypatch, tmp_path, body=b"#!/bin/bash\necho hi\n", fail_run_for=None):
    agent = load_agent()
    calls = []

    def fake_run(*argv, timeout=60):
        calls.append(argv)
        if fail_run_for and argv[0] == fail_run_for:
            raise RuntimeError("boom")
        return "updated" if argv[0] == "bash" else "garrysmod"

    monkeypatch.setattr(agent, "container", lambda name: name)
    monkeypatch.setattr(agent, "GMOD_ADDON", str(tmp_path / "skippy-gmod-addon"))
    monkeypatch.setattr(agent, "run", fake_run)
    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda req, timeout=0: (calls.append(req.full_url), FakeResponse(body))[1])
    return agent, calls


def test_restart_game_garrysmod_stops_refreshes_and_starts(monkeypatch, tmp_path):
    agent, calls = agent_with(monkeypatch, tmp_path)
    out = agent.cmd_restart_game(["garrysmod"])
    assert [c if isinstance(c, str) else c[:2] for c in calls] == [
        ("docker", "stop"), calls[1], ("bash", str(tmp_path / "skippy-gmod-addon")), ("docker", "start")]
    assert calls[1].endswith("/attributes/gmod-addon") and "restarted garrysmod" in out and "updated" in out
    assert (tmp_path / "skippy-gmod-addon").read_bytes().startswith(b"#!/bin/bash")


def test_a_metadata_value_that_is_not_a_script_is_never_run_and_the_game_still_starts(monkeypatch, tmp_path):
    agent, calls = agent_with(monkeypatch, tmp_path, body=b"<html>nope</html>")
    out = agent.cmd_restart_game(["garrysmod"])
    assert not any(isinstance(c, tuple) and c[0] == "bash" for c in calls) and ("docker", "start", "garrysmod") in calls
    assert "isn't a script" in out


def test_a_refresh_that_blows_up_still_starts_the_game_again(monkeypatch, tmp_path):
    agent, calls = agent_with(monkeypatch, tmp_path, fail_run_for="bash")
    out = agent.cmd_restart_game(["garrysmod"])
    assert ("docker", "start", "garrysmod") in calls and "refresh FAILED" in out


def test_other_games_still_use_docker_restart(monkeypatch, tmp_path):
    agent, calls = agent_with(monkeypatch, tmp_path)
    agent.cmd_restart_game(["valheim"])
    assert calls == [("docker", "restart", "--time", "60", "valheim")]


@needs_tools
def test_a_pinned_commit_is_exactly_what_runs_even_after_master_moves(tmp_path, origin):
    pin = origin.v1
    origin.publish({LUA: "print('v2 evil')\n"})
    out = refresh(tmp_path, origin.url, GMOD_ADDON_REF=pin)
    assert f"updated none -> {pin[:7]}" in out
    assert (installed(tmp_path) / LUA).read_text().strip() == "print('v1')"
    assert "already at" in refresh(tmp_path, origin.url, GMOD_ADDON_REF=pin)                            # and a second boot touches nothing


@needs_tools
def test_the_version_can_come_from_the_games_env_file(tmp_path, origin):
    pin = origin.v1
    origin.publish({LUA: "print('v2')\n"})
    env_file = tmp_path / "games.env"
    env_file.write_text(f"FOO=1\nGARRYSMOD_ADDON_REF={pin}\nBAR=2\n")
    refresh(tmp_path, origin.url, GAMES_ENV=env_file.as_posix())
    assert (installed(tmp_path) / LUA).read_text().strip() == "print('v1')"
    env_file.write_text("GARRYSMOD_ADDON_REF=\n")                                                       # blank follows master again
    refresh(tmp_path, origin.url, GAMES_ENV=env_file.as_posix())
    assert (installed(tmp_path) / LUA).read_text().strip() == "print('v2')"


@needs_tools
@pytest.mark.parametrize("bad", ["--upload-pack=touch /tmp/x", "../../etc/passwd", "some-branch", "ABCDEF"])
def test_a_version_that_is_not_a_commit_or_master_is_refused_and_the_addon_stays(tmp_path, origin, bad):
    refresh(tmp_path, origin.url)
    out = refresh(tmp_path, origin.url, GMOD_ADDON_REF=bad)
    assert "is not a commit id or master" in out and "keeping the addon as it is" in out
    assert (installed(tmp_path) / LUA).exists()


# ── the admin-only gate (docs/games/proposals/gmod-admin-only.md) ─────────────

@needs_tools
def test_the_admin_gate_lets_only_the_captain_in_and_is_written_once(tmp_path, origin):
    refresh(tmp_path, origin.url)
    gate = tmp_path / "data" / "garrysmod" / "addons" / "skippy-admin-only" / "lua" / "autorun" / "server" / "sv_admin_only.lua"
    text = gate.read_text(encoding="utf-8")
    assert '["76561197989151002"] = true' in text and text.count("= true") == 1      # the Captain is the only admin
    assert 'hook.Add("CheckPassword"' in text and "return false" in text
    assert "lua/autorun/server" in gate.as_posix()
    mtime = gate.stat().st_mtime_ns
    refresh(tmp_path, origin.url)                                                  # the next boot: same content, same file times
    assert gate.stat().st_mtime_ns == mtime
    assert not installed(tmp_path).joinpath("lua", "autorun", "server", "sv_admin_only.lua").exists()   # the Mentat addon stays separate
