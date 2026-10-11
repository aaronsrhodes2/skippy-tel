"""Our Garry's Mod addon (github.com/aaronsrhodes2/sigma-ground, stage/gmod/addon) goes onto the server's persistent disk before srcds
starts, at every boot and at every `ops restart-game garrysmod`, and a failure to fetch it never stops the game from starting.

  * infra/gcp-games/gmod_addon.sh: shallow sparse clone -> addons/mentat-stage; run here for real against a local repo.
  * startup.sh runs it for garrysmod only, before the containers are created; ops_agent.py runs it between stop and start.
  * the file times stay put when the addon hasn't changed (game_backup.py fingerprints mtimes and backs up garrysmod/addons).
"""
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_fix_watchdog_startup import needs_bash, run_boot

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra" / "gcp-games"
SCRIPT = INFRA / "gmod_addon.sh"
needs_git = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")


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

def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / "origin"
    (repo / "stage" / "gmod" / "addon" / "lua" / "autorun").mkdir(parents=True)
    (repo / "stage" / "gmod" / "addon" / "lua" / "autorun" / "mentat_stage.lua").write_text("print('v1')\n")
    (repo / "big").mkdir()
    (repo / "big" / "data.json").write_text("{}")
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def refresh(tmp_path, repo_url, **extra):
    env = {**os.environ, "DATA": str(tmp_path / "data"), "GMOD_ADDON_REPO": repo_url, "GMOD_ADDON_OWNER": f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "0:0", **extra}
    done = subprocess.run([shutil.which("bash"), str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr                                           # ALWAYS 0
    return done.stdout


def installed(tmp_path):
    return tmp_path / "data" / "garrysmod" / "addons" / "mentat-stage"


@needs_git
def test_a_fresh_boot_installs_only_the_addon_folder_and_leaves_no_clone_behind(tmp_path, origin):
    out = refresh(tmp_path, origin.as_uri())
    assert "mentat-stage updated none ->" in out
    assert (installed(tmp_path) / "lua" / "autorun" / "mentat_stage.lua").read_text().strip() == "print('v1')"
    assert not (installed(tmp_path) / ".git").exists() and not (installed(tmp_path) / "big").exists()
    assert sorted(p.name for p in (tmp_path / "data" / "garrysmod").iterdir()) == ["addons"]         # no scratch folder left


@needs_git
def test_an_unchanged_addon_is_not_touched_so_the_backup_does_not_archive_it_at_every_boot(tmp_path, origin):
    refresh(tmp_path, origin.as_uri())
    f = installed(tmp_path) / "lua" / "autorun" / "mentat_stage.lua"
    os.utime(f, (1_000_000_000, 1_000_000_000))
    out = refresh(tmp_path, origin.as_uri())
    assert "already at" in out and f.stat().st_mtime == 1_000_000_000


@needs_git
def test_a_push_to_master_shows_up_at_the_next_start(tmp_path, origin):
    refresh(tmp_path, origin.as_uri())
    (origin / "stage" / "gmod" / "addon" / "lua" / "autorun" / "mentat_stage.lua").write_text("print('v2')\n")
    (origin / "stage" / "gmod" / "addon" / "lua" / "new.lua").write_text("x\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "two")
    out = refresh(tmp_path, origin.as_uri())
    assert "updated" in out and "already" not in out
    assert (installed(tmp_path) / "lua" / "autorun" / "mentat_stage.lua").read_text().strip() == "print('v2')"
    assert (installed(tmp_path) / "lua" / "new.lua").exists()


@needs_git
def test_when_github_cannot_be_reached_the_previous_copy_stays_and_the_script_still_succeeds(tmp_path, origin):
    refresh(tmp_path, origin.as_uri())
    out = refresh(tmp_path, (tmp_path / "nowhere").as_uri())
    assert "couldn't fetch" in out and "keeping the addon as it is" in out
    assert (installed(tmp_path) / "lua" / "autorun" / "mentat_stage.lua").exists()


@needs_git
def test_a_first_boot_with_no_network_starts_without_the_addon_and_says_so(tmp_path):
    out = refresh(tmp_path, (tmp_path / "nowhere").as_uri())
    assert "couldn't fetch" in out and "(none)" in out and not installed(tmp_path).exists()


@needs_git
def test_a_path_that_is_not_in_the_repo_keeps_what_there_is(tmp_path, origin):
    refresh(tmp_path, origin.as_uri())
    out = refresh(tmp_path, origin.as_uri(), GMOD_ADDON_PATH="stage/gmod/not-there")
    assert "isn't in" in out and installed(tmp_path).exists()


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


def _head(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@needs_git
def test_a_pinned_commit_is_exactly_what_runs_even_after_master_moves(tmp_path, origin):
    pin = _head(origin)
    (origin / "stage" / "gmod" / "addon" / "lua" / "autorun" / "mentat_stage.lua").write_text("print('v2 evil')\n")
    _git(origin, "commit", "-q", "-am", "two")
    out = refresh(tmp_path, origin.as_uri(), GMOD_ADDON_REF=pin)
    assert f"updated none -> {pin[:7]}" in out
    assert (installed(tmp_path) / "lua" / "autorun" / "mentat_stage.lua").read_text().strip() == "print('v1')"
    assert not (installed(tmp_path) / ".git").exists() and not (installed(tmp_path) / "big").exists()
    assert "already at" in refresh(tmp_path, origin.as_uri(), GMOD_ADDON_REF=pin)                       # and a second boot touches nothing


@needs_git
def test_the_version_can_come_from_the_games_env_file(tmp_path, origin):
    pin = _head(origin)
    (origin / "stage" / "gmod" / "addon" / "lua" / "autorun" / "mentat_stage.lua").write_text("print('v2')\n")
    _git(origin, "commit", "-q", "-am", "two")
    env_file = tmp_path / "games.env"
    env_file.write_text(f"FOO=1\nGARRYSMOD_ADDON_REF={pin}\nBAR=2\n")
    refresh(tmp_path, origin.as_uri(), GAMES_ENV=str(env_file))
    assert (installed(tmp_path) / "lua" / "autorun" / "mentat_stage.lua").read_text().strip() == "print('v1')"
    env_file.write_text("GARRYSMOD_ADDON_REF=\n")                                                       # blank follows master again
    refresh(tmp_path, origin.as_uri(), GAMES_ENV=str(env_file))
    assert (installed(tmp_path) / "lua" / "autorun" / "mentat_stage.lua").read_text().strip() == "print('v2')"


@needs_git
def test_a_version_that_is_not_a_commit_or_branch_name_is_refused_and_the_addon_stays(tmp_path, origin):
    refresh(tmp_path, origin.as_uri())
    out = refresh(tmp_path, origin.as_uri(), GMOD_ADDON_REF="--upload-pack=touch /tmp/x")
    assert "is not a commit id or a branch name" in out and "keeping the addon as it is" in out
    assert (installed(tmp_path) / "lua" / "autorun" / "mentat_stage.lua").exists()
