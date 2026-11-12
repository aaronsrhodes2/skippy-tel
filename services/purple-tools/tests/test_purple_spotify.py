"""purple's `spotify` tool: words in, the spotify container's API (services/spotify/service.py) out."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402

URI = "spotify:track:" + "a" * 22


def fake(replies):
    calls = []

    def request(method, path, body=None):
        calls.append((method, path, body))
        for prefix, reply in replies.items():
            if path.startswith(prefix):
                return reply
        return 404, {"error": "no such endpoint"}
    return request, calls


def test_play_words_plays_the_best_match_and_a_uri_plays_as_it_is():
    req, calls = fake({"/tracks": (200, {"tracks": [{"uri": URI, "title": "One More Time", "artist": "Daft Punk"}]}), "/play": (200, {"ok": True})})
    assert tools.spotify({"action": "play", "query": "Daft Punk"}, request=req) == f"Playing {URI} on the house stereo (the Skippy speaker)."
    assert calls[-1] == ("POST", "/play", {"uri": URI})
    album = "spotify:album:" + "b" * 22
    tools.spotify({"action": "play", "query": album}, request=req)
    assert calls[-1] == ("POST", "/play", {"uri": album})


def test_add_takes_the_song_playing_now_and_says_the_playlist():
    req, calls = fake({"/now": (200, {"logged_in": True, "playing": True, "uri": URI}), "/playlist/add": (200, {"playlist": {"name": "Gym"}})})
    assert tools.spotify({"action": "add", "playlist": "gym"}, request=req) == f"Added {URI} to his playlist Gym."
    assert calls[-1] == ("POST", "/playlist/add", {"playlist": "gym", "uris": [URI]})


def test_a_refusal_comes_back_as_its_words():
    req, _ = fake({"/playlists": (503, {"error": "not signed in to Spotify yet"})})
    with pytest.raises(ValueError, match="not signed in"):
        tools.spotify({"action": "playlists"}, request=req)
    assert tools.call("spotify", {"action": "nonsense"})[1] is True
