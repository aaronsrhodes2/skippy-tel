"""The Captain's Spotify in the hub (the Captain, 2026-10-31): the spotify container (services/spotify) is a Spotify Connect speaker and holds his
sign-in; this is the hub's side of it.

* Client: the container's API (search, his playlists, adding to one, saving, play/pause/resume, what it plays).
* pcm_args(): one ffmpeg reading the container's sound over TCP (44.1 kHz stereo float, exactly what go-librespot wrote) and resampling it to the
  router's 48 kHz stereo float, so it plays through router.PipeSource like a book: the Sony over HDMI and the other PC outputs, on the `media`
  layer, ducked under Skippy's voice. Nothing else is done to the sound: no crossfade, no cut inside a track (their terms frown on altering it).
* A queue (music.BoxMusic) holds Bilby paths and Spotify URIs side by side; is_uri() tells them apart. The stereo (stereo.py) plays each kind its
  own way, with a plain cut where the kind changes.

    SPOTIFY_URL=http://127.0.0.1:43873      the container's API        SPOTIFY_PCM=tcp://127.0.0.1:43872      its sound
"""
from __future__ import annotations

import re

URI_RE = re.compile(r"spotify:(?:track|episode):[A-Za-z0-9]{22}")
RATE_IN = 44100


def is_uri(track: str) -> bool:
    return bool(URI_RE.fullmatch(track or ""))


def pcm_args(pcm: str = "tcp://127.0.0.1:43872") -> list[str]:
    import router                                               # noqa: PLC0415
    return ["ffmpeg", "-v", "error", "-nostdin", "-fflags", "nobuffer", "-probesize", "32", "-analyzeduration", "0",
            "-f", "f32le", "-ar", str(RATE_IN), "-ac", "2", "-i", pcm, "-f", "f32le", "-ac", "2", "-ar", str(router.RATE), "pipe:1"]


class SpotifyError(Exception):
    """The container said no, in words worth saying aloud."""


class Client:
    def __init__(self, base: str = "http://127.0.0.1:43873", pcm: str = "tcp://127.0.0.1:43872", request=None):
        self.base, self.pcm = base.rstrip("/"), pcm
        self.request = request or self._request

    @classmethod
    def from_env(cls, env: dict) -> "Client":
        return cls(env.get("SPOTIFY_URL") or "http://127.0.0.1:43873", env.get("SPOTIFY_PCM") or "tcp://127.0.0.1:43872")

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> tuple[int, dict]:
        import requests                                          # noqa: PLC0415
        try:
            r = requests.request(method, self.base + path, params=params, json=body, timeout=20)
        except requests.RequestException as e:
            return 0, {"error": f"the Spotify speaker is not running ({type(e).__name__})"}
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}

    def _ok(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        code, got = self.request(method, path, params, body)
        if code != 200:
            raise SpotifyError(got.get("error") or got.get("detail") or f"Spotify said {code}")
        return got

    # ── his library ──
    def tracks_for(self, what: str, n: int = 30) -> dict:
        return self._ok("GET", "/tracks", {"what": what, "n": n})

    def search(self, q: str, kind: str = "track", n: int = 10) -> list[dict]:
        return self._ok("GET", "/search", {"q": q, "type": kind, "n": n}).get("items") or []

    def playlist(self, name: str) -> dict:
        return self._ok("GET", "/playlist", {"name": name})

    def playlists(self) -> list[dict]:
        return self._ok("GET", "/playlists").get("playlists") or []

    def liked(self, n: int = 200) -> list[dict]:
        return self._ok("GET", "/liked", {"n": n}).get("tracks") or []

    def add_to_playlist(self, playlist: str, uris: list[str]) -> dict:
        return self._ok("POST", "/playlist/add", body={"playlist": playlist, "uris": uris}).get("playlist") or {}

    def save(self, uris: list[str]) -> None:
        self._ok("POST", "/save", body={"uris": uris})

    # ── the speaker ──
    def play(self, uri: str, pos_ms: int = 0) -> None:
        self._ok("POST", "/play", body={"uri": uri, "pos_ms": int(pos_ms)})

    def pause(self) -> None:
        self._ok("POST", "/pause")

    def resume(self) -> None:
        self._ok("POST", "/resume")

    def now(self) -> dict:
        code, got = self.request("GET", "/now")
        return got if code == 200 else {}

    def health(self) -> dict:
        return self.request("GET", "/health")[1]


LIKED_RE = re.compile(r"^(?:my |his )?(?:liked|saved|favou?rite)(?: songs?| tracks?| music)?$")


def queue_for(client: Client, what: str) -> tuple[list[dict], str]:
    """(tracks, how to say what they are) for a spoken request: his liked songs, one of his playlists by name, else an artist or a search."""
    plain = " ".join(re.findall(r"[a-z0-9']+", (what or "").lower()))
    plain = re.sub(r"\s*\b(?:on|from|in) spotify$|^spotify\s*", "", plain).strip()
    if LIKED_RE.match(plain):
        return client.liked(), "his liked songs"
    named = re.sub(r"^(?:my |his |the )?(?:playlist )?|(?: playlist)$", "", plain).strip()
    if "playlist" in plain or plain.startswith(("my ", "his ")):
        try:
            got = client.playlist(named)
            return got.get("tracks") or [], f"his playlist {got.get('playlist', {}).get('name', named)}"
        except SpotifyError:
            if "playlist" in plain:
                raise
    got = client.tracks_for(plain)
    how = f"{got.get('name')}" if got.get("kind") == "artist" else f"what Spotify found for {plain}"
    return got.get("tracks") or [], how
