"""Pure-logic tests — no Flask/Authlib/google-cloud-compute needed, so
these run with nothing but the stdlib in any Python 3.10+."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog import GAMES, SERVICES, machine_for  # noqa: E402


def test_every_game_has_a_working_connect_string():
    for game in GAMES.values():
        text = game.connect("34.1.2.3", {})
        assert "34.1.2.3" in text or "Skippy" in text    # Dragonwilds: found by server name, no IP
        assert game.ram_gb > 0


def test_dragonwilds_connect_uses_configured_world_name():
    text = GAMES["dragonwilds"].connect("34.1.2.3", {"DRAGONWILDS_WORLD_NAME": "Skippy"})
    assert "Skippy" in text
    assert "Ashenfall" not in text


def test_machine_for_sizes_match_games_sh():
    assert machine_for(["factorio"]) == "e2-standard-2"
    assert machine_for(["valheim"]) == "e2-standard-2"
    assert machine_for(["valheim", "factorio"]) == "e2-standard-4"
    assert machine_for(["satisfactory", "dragonwilds"]) == "e2-highmem-4"
    assert machine_for(["valheim", "factorio", "satisfactory", "dragonwilds"]) == "e2-highmem-4"


def test_machine_for_ignores_unknown_game_ids():
    assert machine_for(["not-a-real-game"]) == "e2-standard-2"


def test_services_have_no_duplicate_ids():
    ids = [s.id for s in SERVICES]
    assert len(ids) == len(set(ids))
