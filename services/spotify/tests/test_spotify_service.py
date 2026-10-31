"""The spotify container's service (service.py) without Spotify or go-librespot: the token kept as Spotify rotates it, the 2026 Development Mode
endpoints (search pages of 10, an artist's songs by search, /playlists/{id}/items, PUT /me/library), the API's words, and the sign-in's vault edit."""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service as S  # noqa: E402
import signin  # noqa: E402

DP = {"id": "dp", "name": "Daft Punk"}


def track(n, artist=DP):
    return {"uri": f"spotify:track:{n:0>22}", "name": f"Song {n}", "artists": [artist], "album": {"name": "Discovery"}, "duration_ms": 1000, "type": "track"}


class FakeSpotify:
    def __init__(self):
        self.calls = []
        self.refreshes = 0

    def __call__(self, method, url, headers, body, timeout=15.0):
        u = urllib.parse.urlsplit(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        self.calls.append((method, u.path, q, json.loads(body) if body and u.path.startswith("/v1") else None))
        if u.path == "/api/token":
            self.refreshes += 1
            return 200, json.dumps({"access_token": f"at{self.refreshes}", "expires_in": 3600, "refresh_token": f"rt{self.refreshes}"}).encode()
        if u.path == "/v1/search":
            assert int(q["limit"]) <= 10                                          # Development Mode's page
            if q["type"] == "artist":
                return 200, json.dumps({"artists": {"items": [DP]}}).encode()
            off = int(q["offset"])
            items = [track(i) for i in range(off, off + 10)] if off < 20 else [track(20)]
            return 200, json.dumps({"tracks": {"items": items}}).encode()
        if u.path == "/v1/me":
            return 200, b'{"id": "aaron"}'
        if u.path == "/v1/me/playlists":
            return 200, json.dumps({"items": [{"id": "g" * 22, "name": "Gym Mix", "uri": "spotify:playlist:" + "g" * 22, "owner": {"id": "aaron"},
                                               "items": {"total": 3}}], "next": None}).encode()
        if u.path.startswith("/v1/playlists/") and u.path.endswith("/items"):
            if method == "GET":
                return 200, json.dumps({"items": [{"item": track(1)}, {"track": track(2)}, {"item": {"type": "episode", "uri": "spotify:episode:x"}}],
                                        "next": None}).encode()
            return 200, b'{"snapshot_id": "s"}'
        if u.path == "/v1/me/library":
            return 200, b""
        return 404, b'{"error": {"message": "no"}}'


def web(tmp_path, seed="seed"):
    fake = FakeSpotify()
    return S.WebApi("cid", seed, state=tmp_path, http=fake), fake


def test_the_refresh_token_spotify_hands_back_is_kept_and_a_new_vault_token_wins(tmp_path):
    w, fake = web(tmp_path)
    w.token()
    assert json.loads((tmp_path / "token.json").read_text())["refresh"] == "rt1"
    again = S.WebApi("cid", "seed", state=tmp_path, http=fake)            # a restart with the same vault token: the rotated one is used
    assert again.refresh == "rt1"
    fresh = S.WebApi("cid", "new-sign-in", state=tmp_path, http=fake)     # signin.py wrote a new one into the vault
    assert fresh.refresh == "new-sign-in"


def test_an_artists_songs_come_from_a_search_paged_by_ten(tmp_path):
    w, fake = web(tmp_path)
    got = w.tracks_for("daft punk", 25)
    assert got["kind"] == "artist" and got["name"] == "Daft Punk" and len(got["tracks"]) == 21
    pages = [c for c in fake.calls if c[1] == "/v1/search" and c[2]["type"] == "track"]
    assert [p[2]["offset"] for p in pages] == ["0", "10", "20"] and pages[0][2]["q"] == 'artist:"Daft Punk"'


def test_playlists_by_name_their_items_and_adding_and_saving(tmp_path):
    w, fake = web(tmp_path)
    assert w.find_playlist("my gym playlist")["name"] == "Gym Mix"
    assert [t["title"] for t in w.playlist_tracks("g" * 22)] == ["Song 1", "Song 2"]      # both shapes of an item; an episode is left out
    w.add_to_playlist("gym", ["spotify:track:" + "1" * 22])
    post = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/items")][-1]
    assert post[1] == f"/v1/playlists/{'g' * 22}/items"
    w.save(["spotify:track:" + "1" * 22])
    assert any(c[0] == "PUT" and c[1] == "/v1/me/library" for c in fake.calls)


class FakePlayer:
    def __init__(self, logged_in=False):
        self.logged_in, self.played = logged_in, []

    def status(self):
        return {"logged_in": self.logged_in, "playing": False}

    def pairing(self):
        return None if self.logged_in else {"url": "https://spotify.com/pair?code=ABC123", "code": "ABC123", "expires_at": ""}

    def play(self, uri, pos_ms=0):
        self.played.append((uri, pos_ms))
        return 200


def test_health_says_what_is_missing_and_play_takes_only_spotify_uris(tmp_path):
    w = S.WebApi("", "", state=tmp_path, http=FakeSpotify())
    svc = S.Service(w, FakePlayer(), home_token="home")
    code, h = svc.handle("GET", "/health", {}, {})
    assert code == 503 and h["pair"]["code"] == "ABC123" and "spotify.com/pair" in h["deps"]["player"]["detail"]
    assert "signin.py" in h["deps"]["web_api"]["detail"]
    assert svc.handle("POST", "/play", {}, {"uri": "file:///etc/passwd"})[0] == 400
    assert svc.handle("POST", "/play", {}, {"uri": "spotify:track:" + "a" * 22, "pos_ms": 5000})[0] == 200
    assert svc.handle("GET", "/playlists", {}, {})[0] == 503                # not signed in: said, not raised
    assert svc.handle("POST", "/token", {}, {"client_id": "c", "refresh_token": "r"}, "Bearer nope")[0] == 403


def test_the_player_config_is_the_pipe_in_float_with_its_api_on_localhost():
    cfg = S.librespot_config("Skippy")
    for line in ("audio_backend: pipe", "audio_output_pipe_format: f32le", "  address: 127.0.0.1", "disable_autoplay: true", "  type: device_auth",
                 "crossfade_duration: 0"):
        assert line in cfg.splitlines()


def test_signin_edits_only_its_own_lines_of_the_vault(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"A=1\r\nSPOTIFY_CLIENT_ID=old\r\nB=2\r\n")
    signin.set_env({"SPOTIFY_CLIENT_ID": "new", "SPOTIFY_REFRESH_TOKEN": "rt"}, env)
    assert env.read_bytes() == b"A=1\r\nSPOTIFY_CLIENT_ID=new\r\nB=2\r\nSPOTIFY_REFRESH_TOKEN=rt\r\n"
    url = urllib.parse.urlsplit(signin.authorize_url("cid", "chal", "st"))
    q = dict(urllib.parse.parse_qsl(url.query))
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"] == "http://127.0.0.1:43874/callback"
    assert "playlist-modify-private" in q["scope"] and "user-library-modify" in q["scope"]


import os  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a FIFO needs Linux (the container; CI)")
def test_the_relay_hands_the_fifo_to_the_hub_unaltered_and_drops_what_nobody_heard(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    relay = S.Relay(tmp_path / "audio", port, host="127.0.0.1", log=lambda *_: None, sleep=lambda _s: None)
    threading.Thread(target=relay.serve, daemon=True).start()
    for _ in range(50):
        if (tmp_path / "audio").exists():
            break
        time.sleep(0.05)
    w = os.open(tmp_path / "audio", os.O_WRONLY | os.O_NONBLOCK)              # the player's open: fails unless a reader holds the FIFO
    os.set_blocking(w, True)
    os.write(w, b"old" * 100)                                                # nobody listening: read and dropped
    time.sleep(0.3)
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    time.sleep(0.3)
    os.write(w, b"\x01\x02\x03\x04" * 4096)
    got = b""
    while len(got) < 4 * 4096:
        got += c.recv(65536)
    assert got == b"\x01\x02\x03\x04" * 4096
    c.close()
    os.close(w)
