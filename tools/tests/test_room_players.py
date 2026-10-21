"""tools/room_players.py: one Chrome app window per room, in one shared profile, with the flags a window nobody touches needs."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("room_players", Path(__file__).resolve().parents[1] / "room_players.py")
rp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rp)


def test_each_room_opens_its_own_music_page_in_the_shared_profile():
    a = rp.command("downstairs", "chrome", Path("P"))
    assert a[0] == "chrome" and any(x.endswith("/music?room=downstairs") for x in a) and "--user-data-dir=P" in a
    assert "--autoplay-policy=no-user-gesture-required" in a and "--use-fake-ui-for-media-stream" in a
    assert "--disable-renderer-backgrounding" in a


def test_print_starts_nothing_and_names_both_rooms(capsys):
    assert rp.main(["--print"]) == 0
    out = capsys.readouterr().out
    assert "room=downstairs" in out and "room=upstairs" in out


def test_an_unknown_room_is_refused():
    with pytest.raises(SystemExit):
        rp.main(["--print", "garage"])


def test_the_rooms_match_the_portals():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "portal"))
    import player
    assert set(rp.ROOMS) == set(player.ROOMS)
