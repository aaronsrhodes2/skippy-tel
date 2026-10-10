"""Garry's Mod, the sixth game server: the portal's entry for it, the settings that end up on srcds's command line, and that every
place a game has to be named (compose, firewall, watchdog, backups, readiness, Discord, the scripts) names it."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import catalog

ROOT = Path(__file__).resolve().parents[2]
G = catalog.GAMES["garrysmod"]
SETTINGS = {s.key: s for s in G.settings}


def _text(*parts) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_game_has_its_own_glyph_a_port_and_a_way_in():
    assert G.name == "Garry's Mod" and G.glyph == "cube" and f'id="g-cube"' in _text("portal", "templates", "_glyphs.html")
    assert "connect 203.0.113.9:27015" in G.connect("203.0.113.9", {}) and G.direct("203.0.113.9", {}) == "203.0.113.9:27015"
    assert G.join_password_key == "GARRYSMOD_PASSWORD" and SETTINGS["GARRYSMOD_PASSWORD"].secret
    assert not G.invite_code and G.max_players == 16


@pytest.mark.parametrize("key, good, bad", [
    ("GARRYSMOD_PASSWORD", ["", "pw-1.x_y"], ["has space", "a;b", "$(x)", "it's", "x" * 65]),
    ("GARRYSMOD_SERVER_NAME", ["Skippy-GMod"], ["Skippy GMod", "it's"]),
    ("GARRYSMOD_GAMEMODE", ["", "sandbox", "terrortown"], ["two words", "a;b", "../x"]),
    ("GARRYSMOD_MAP", ["", "gm_construct", "ttt_minecraft_b5"], ["gm construct", "a/b"]),
    ("GARRYSMOD_MAX_PLAYERS", ["", "1", "128"], ["0", "129", "many", "-1", "٣"]),
    ("GARRYSMOD_WORKSHOP_COLLECTION", ["", "382793424"], ["https://steamcommunity.com/x", "12 34", "abc"]),
])
def test_what_reaches_the_command_line_is_checked_first(key, good, bad):
    check = SETTINGS[key].check
    for v in good:
        assert check(v)[1] is None, v
    for v in bad:
        assert check(v)[1], v


def test_every_place_a_game_is_named_names_garrysmod():
    assert "garrysmod" in _text("infra", "gcp-games", "startup.sh") and "--profile garrysmod" in _text("infra", "gcp-games", "shutdown.sh")
    assert '"garrysmod": [(27015, 27015)]' in _text("infra", "gcp-games", "idle_watchdog.py")
    assert '"27015"' in _text("infra", "gcp-games", "main.tf") and "garrysmod" in _text("infra", "gcp-games", "game_backup.py")
    assert "garrysmod" in _text("infra", "gcp-games", "discord-notify.py") and "garrysmod)" in _text("scripts", "games.sh")
    assert "garrysmod" in _text("infra", "gcp-games", "game_facts.py") and "garrysmod" in _text("infra", "gcp-games", "diag.sh")
    assert catalog.GAMES["garrysmod"].ram_gb == 4


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose isn't here")
def test_the_compose_file_builds_srcds_arguments_only_from_what_is_set():
    def args(**env):
        out = subprocess.run(["docker", "compose", "-f", str(ROOT / "games" / "docker-compose.yml"), "--profile", "garrysmod", "config"],
                             capture_output=True, text=True, env={**__import__("os").environ, **env}, timeout=60)
        assert out.returncode == 0, out.stderr
        return re.search(r"^\s+ARGS:\s*(.*)$", out.stdout.split("garrysmod:")[1], re.M).group(1).strip().strip("'\"").strip()
    assert args() == ""
    assert args(GARRYSMOD_PASSWORD="pw1") == "+sv_password pw1"
    assert args(GARRYSMOD_WORKSHOP_COLLECTION="123", GARRYSMOD_PASSWORD="pw1") == "+host_workshop_collection 123 +sv_password pw1"
