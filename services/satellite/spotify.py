"""The Captain's Spotify in the hub (the Captain, 2026-10-31): the spotify container (services/spotify) is a Spotify Connect speaker and holds his
sign-in; this is the hub's side of it.

* Client: the container's API (search, his playlists, adding to one, saving, play/pause/resume, what it plays).
* pcm_args(): one ffmpeg reading the container's sound over TCP (44.1 kHz stereo float, exactly what go-librespot wrote) and resampling it to the
  router's 48 kHz stereo float, so it plays through router.PipeSource like a book: the Sony over HDMI and the other PC outputs, on the `media`
  layer, ducked under Skippy's voice. Nothing else is done to the sound: no crossfade, no cut inside a track (their terms frown on altering it).
* A queue (music.BoxMusic) holds Bilby paths and Spotify URIs side by side; is_uri() tells them apart. The stereo (stereo.py) plays each kind its
  own way, with a plain cut where the kind changes.

    SPOTIFY_URL=http://127.0.0.1:43873      the container's API
    SPOTIFY_PCM=capture:CABLE Output        its sound: the Spotify app on the virtual cable (the default), or tcp://127.0.0.1:43872 (go-librespot)
    SPOTIFY_XFADE_S=6                       the crossfade where Spotify hands over to Bilby (0: plain cuts)

Since 2026-10-31 the speaker is Spotify's own app (Spotify refuses go-librespot the audio key on his account): the container drives it through the
Web API, the hub captures the cable (CaptureSource), and a run of Spotify tracks goes to the app as one list, so the app's own crossfade applies
between them.
"""
from __future__ import annotations

import re
import threading
from collections import deque

URI_RE = re.compile(r"spotify:(?:track|episode):[A-Za-z0-9]{22}")
RATE_IN = 44100


def is_uri(track: str) -> bool:
    return bool(URI_RE.fullmatch(track or ""))


def pcm_args(pcm: str = "tcp://127.0.0.1:43872") -> list[str]:
    import router                                               # noqa: PLC0415
    return ["ffmpeg", "-v", "error", "-nostdin", "-fflags", "nobuffer", "-probesize", "32", "-analyzeduration", "0",
            "-f", "f32le", "-ar", str(RATE_IN), "-ac", "2", "-i", pcm, "-f", "f32le", "-ac", "2", "-ar", str(router.RATE), "pipe:1"]


class CaptureSource:
    """The Spotify app's sound, from the virtual cable (2026-10-31: his account plays only in Spotify's own app, whose output is set to "CABLE
    Input" in Windows' volume mixer): a WASAPI input on "CABLE Output" at the router's 48 kHz stereo float, as a router source. It keeps at most
    `keep_s` ready (the cable's clock and the mixer's drift apart: the oldest goes first), and it never ends by itself. `live`: nothing to drain."""
    live = True

    def __init__(self, part: str = "CABLE Output", open_stream=None, keep_s: float = 0.3):
        import router                                           # noqa: PLC0415
        self.rate, self.keep = router.RATE, int(router.RATE * keep_s)
        self.buf: deque = deque()
        self.have = 0
        self.lock = threading.Lock()
        self.failed = False
        self.stream = (open_stream or _open_capture)(part, self._callback)

    def _callback(self, indata, _frames, _time, _status) -> None:
        block = indata[:, :2].copy()
        with self.lock:
            self.buf.append(block)
            self.have += len(block)
            while self.have - len(self.buf[0]) >= self.keep and len(self.buf) > 1:
                self.have -= len(self.buf.popleft())

    def read(self, frames: int):
        import numpy as np                                      # noqa: PLC0415
        parts, got = [], 0
        with self.lock:
            while got < frames and self.buf:
                head = self.buf[0]
                n = min(frames - got, len(head))
                parts.append(head[:n])
                got += n
                if n < len(head):
                    self.buf[0] = head[n:]
                else:
                    self.buf.popleft()
            self.have -= got
        return np.concatenate(parts) if parts else np.zeros((0, 2), dtype="float32")

    @property
    def done(self) -> bool:
        return self.failed

    def close(self) -> None:
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:                                   # noqa: BLE001
                pass


APIS = ("WASAPI", "DirectSound", "MME")                          # the cable's inputs, tried in this order (WDM-KS never: it takes the device)


def _open_capture(part: str, callback):
    """The cable's first input that opens and starts. (2026-10-31: in the hub, WASAPI's start once failed every time while a fresh process
    opened it at once, so the others stand behind it; each failure is named in the error.)"""
    import router                                               # noqa: PLC0415
    import sounddevice as sd                                    # noqa: PLC0415
    apis = [a["name"] for a in sd.query_hostapis()]
    devs = [(i, d) for i, d in enumerate(sd.query_devices()) if part.lower() in d["name"].lower() and d["max_input_channels"] >= 2]
    devs = sorted((x for x in devs if any(k in apis[x[1]["hostapi"]] for k in APIS)),
                  key=lambda x: next(n for n, k in enumerate(APIS) if k in apis[x[1]["hostapi"]]))
    if not devs:
        raise OSError(f"no input called {part!r} (is VB-CABLE installed?)")
    why = []
    for i, d in devs:
        s = None
        try:
            s = sd.InputStream(samplerate=router.RATE, channels=2, dtype="float32", device=i, callback=callback, blocksize=router.BLOCK,
                               latency="low")
            s.start()
            return s
        except Exception as e:                                  # noqa: BLE001 — the next one may open
            why.append(f"{apis[d['hostapi']]} #{i}: {e}")
            if s is not None:
                s.close()
    raise OSError("; ".join(why))


class SpotifyError(Exception):
    """The container said no, in words worth saying aloud."""


class Client:
    def __init__(self, base: str = "http://127.0.0.1:43873", pcm: str = "capture:CABLE Output", request=None):
        self.base, self.pcm = base.rstrip("/"), pcm
        self.request = request or self._request

    @classmethod
    def from_env(cls, env: dict) -> "Client":
        return cls(env.get("SPOTIFY_URL") or "http://127.0.0.1:43873", env.get("SPOTIFY_PCM") or "capture:CABLE Output")

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

    def road(self, uri: str) -> dict:
        """Into his "Skippy Road" playlist, once: every Spotify track played through Skippy, which his phone keeps downloaded for the drive."""
        return self._ok("POST", "/road", body={"uri": uri})

    # ── the speaker ──
    def play(self, uri: str, pos_ms: int = 0, then: list[str] | None = None) -> None:
        """`uri` from `pos_ms`, and `then` after it with no gap (the app's own crossfade between them)."""
        self._ok("POST", "/play", body={"uri": uri, "pos_ms": int(pos_ms), **({"then": then} if then else {})})

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
