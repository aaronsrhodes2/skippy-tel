"""One grant per launcher tile a friend may be given (catalog.PORTALS): Skippy, each game server, Bilby's Audio Service,
Nagatha's Video Service, Browser Games and Experiments. Dashboard and Admin are never grantable. A tile shows only with its
grant, and the app behind it answers only with it."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import catalog  # noqa: E402
import friends  # noqa: E402
from test_app import ADMIN, _add_friend, login  # noqa: E402
from test_player import client  # noqa: E402,F401  (a portal whose home is paired and reporting)

PAL = "pal@example.com"


def test_every_friend_grantable_tile_has_a_grant_and_the_captains_own_dont():
    for key in ("skippy", "audio", "video", "play", "experiments", *catalog.GAMES):
        assert key in catalog.PORTALS
    for key in ("dashboard", "admin", "me", "bilby"):
        assert key not in catalog.PORTALS


def test_the_old_bilby_grant_reads_as_audio_and_nothing_more():
    assert friends.portals_of({"portals": ["bilby"]}) == ["audio"]
    assert friends.portals_of({"portals": ["bilby", "audio"]}) == ["audio"]


@pytest.mark.parametrize("grant,tile,page", [("audio", "tile:music", "/music"), ("video", "tile:video", "/video"),
                                             ("play", "tile:play", None), ("experiments", "tile:experiments", None)])
def test_a_tile_and_its_app_come_with_the_grant_only(client, grant, tile, page):  # noqa: F811
    _add_friend(client, PAL, ["skippy"])
    login(client, PAL)
    assert f'data-tel="{tile}"'.encode() not in client.get("/apps").data
    if page:
        assert client.get(page).status_code == 403
    client.get("/logout")
    _add_friend(client, PAL, ["skippy", grant])
    login(client, PAL)
    launcher = client.get("/apps").data.decode()
    assert f'data-tel="{tile}"' in launcher
    if page:
        assert client.get(page).status_code == 200
        assert client.get(page + "/gate").status_code in (200, 503)      # a pass, or "home can't give one right now"


def test_a_friend_reads_aarons_library_and_their_viewing_stays_out_of_continue_watching(client):  # noqa: F811
    _add_friend(client, PAL, ["audio", "video"])
    login(client, PAL)
    launcher = client.get("/apps").data.decode()
    assert 'data-tel="tile:music"' in launcher and 'data-tel="tile:video"' in launcher     # both controls, granted
    assert 'data-progress="0"' in client.get("/video").data.decode()
    assert client.get("/me").status_code == 403 and client.get("/admin").status_code == 403
    client.get("/logout")
    login(client, ADMIN)
    assert 'data-progress="1"' in client.get("/video").data.decode()


def test_the_invite_no_longer_calls_it_a_game_server_portal():
    _, body = friends.invite_text(PAL, ["audio", "valheim"], "Aaron", "https://makerportal.live/", {})
    assert "my portal for" in body and "Bilby's Audio Service" in body
