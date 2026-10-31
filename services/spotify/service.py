"""The Captain's Spotify at home (the Captain, 2026-10-31): skippy-pc is a Spotify Connect speaker, and this is the one place that holds his sign-in.

Three jobs in one container, beside go-librespot (github.com/devgianlu/go-librespot: unofficial, Premium only), which this process starts and keeps up:

  * THE SOUND: go-librespot's pipe backend writes 44.1 kHz stereo float into a FIFO; the relay holds that FIFO open and hands it, unaltered, to the
    one TCP client (the hub, services/satellite/spotify.py), which resamples it into the audio router like a book. Nothing is mixed, faded or cut
    here. With no client the relay reads at real-time pace and drops it, so the player never stalls (a stalled pipe write would also block its
    API); a new client gets only what is written after it connected.
  * HIS LIBRARY: the Spotify Web API with his own sign-in (authorization code + PKCE, services/spotify/signin.py; his password never comes here):
    search, his playlists, adding to a playlist, saving to his library. The refresh token comes from the vault .env (SPOTIFY_REFRESH_TOKEN) and,
    because Spotify hands a new one on refresh, the current one is kept in /state/token.json.
  * THE PLAYER: go-librespot's own API (play a URI, pause, resume, its status), passed through.

Endpoints (JSON; 127.0.0.1 only on the host, see docker-compose.override.yml):
    GET  /health                    the readiness contract (ready.py): web sign-in, player login, the pairing code while it waits for one
    GET  /now                       what the player is doing
    POST /play {uri, pos_ms}        POST /pause   POST /resume
    GET  /tracks?what=&n=           tracks for a spoken request (an artist's, else a search)
    GET  /search?q=&type=&n=        GET /playlists   GET /playlist?name=|id=   GET /liked?n=
    POST /playlist/add {playlist, uris}             POST /save {uris}
    POST /token {client_id, refresh_token}          from signin.py, with the home token

Development Mode rules (February 2026, still in force): the app owner needs Premium, at most 5 users, search returns at most 10 a page (so this
pages with offset), an artist's top tracks and batch lookups are gone, playlist items are /playlists/{id}/items and only for his own playlists,
and saving to his library is PUT /me/library with URIs.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import select
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import ready as ready_mod

STATE = Path(os.environ.get("SPOTIFY_STATE", "/state"))
FIFO = Path(os.environ.get("SPOTIFY_FIFO", "/run/spotify/audio"))
PCM_PORT = int(os.environ.get("SPOTIFY_PCM_PORT", "43872"))
API_PORT = int(os.environ.get("SPOTIFY_API_PORT", "43873"))
PLAYER = os.environ.get("SPOTIFY_PLAYER_URL", "http://127.0.0.1:43871")
RATE = 44100
BYTES_PER_S = RATE * 2 * 4                      # f32le stereo
WEB = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = ("user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private playlist-read-collaborative "
          "playlist-modify-private playlist-modify-public user-library-read user-library-modify")
PAGE = 10                                       # Development Mode's largest search page


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (s or "").lower().replace("&", "and")))


def brief(t: dict) -> dict:
    """A track as the hub and purple want it."""
    t = t or {}
    return {"uri": t.get("uri", ""), "title": t.get("name", ""), "artist": ", ".join(a.get("name", "") for a in t.get("artists") or []),
            "album": (t.get("album") or {}).get("name", ""), "ms": t.get("duration_ms") or 0}


# ── the Web API, signed in as him ────────────────────────────────────────────────────────────────────────────────────────────────
class NotSignedIn(Exception):
    pass


def _http(method: str, url: str, headers: dict, body: bytes | None, timeout: float = 15.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:        # noqa: S310 — Spotify's own API, or the player on localhost
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class WebApi:
    def __init__(self, client_id: str, seed_refresh: str = "", state: Path = STATE, http=_http, clock=time.time):
        self.client_id, self.state, self.http, self.clock = client_id, state, http, clock
        self.path = state / "token.json"
        self._access: tuple[str, float] | None = None
        self._lock = threading.Lock()
        self.error = ""
        saved = self._load()
        # the vault's token wins when it is new (a fresh sign-in); otherwise the latest one Spotify handed us
        seed_id = _fingerprint(seed_refresh)
        if seed_refresh and saved.get("seed") != seed_id:
            saved = {"refresh": seed_refresh, "seed": seed_id, "client_id": client_id}
            self._save(saved)
        self.refresh = saved.get("refresh", "")
        self.client_id = client_id or saved.get("client_id", "")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        try:
            self.state.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            self.error = f"could not keep the token: {e}"

    def set_token(self, client_id: str, refresh: str) -> None:
        with self._lock:
            self.client_id, self.refresh, self._access = client_id, refresh, None
            self._save({"refresh": refresh, "seed": _fingerprint(refresh), "client_id": client_id})

    @property
    def signed_in(self) -> bool:
        return bool(self.client_id and self.refresh)

    def token(self) -> str:
        with self._lock:
            if self._access and self.clock() < self._access[1] - 60:
                return self._access[0]
            if not self.signed_in:
                raise NotSignedIn("not signed in to Spotify yet (services/spotify/signin.py)")
            body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": self.refresh, "client_id": self.client_id}).encode()
            code, raw = self.http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
            got = _json(raw)
            if code != 200 or not got.get("access_token"):
                self.error = f"Spotify refused the sign-in ({code} {got.get('error', '')}): sign in again"
                raise NotSignedIn(self.error)
            self.error = ""
            self._access = (got["access_token"], self.clock() + float(got.get("expires_in") or 3600))
            if got.get("refresh_token") and got["refresh_token"] != self.refresh:
                self.refresh = got["refresh_token"]
                saved = self._load()
                saved.update(refresh=self.refresh, client_id=self.client_id)
                self._save(saved)
            return self._access[0]

    def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        url = WEB + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"Authorization": "Bearer " + self.token()}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        code, raw = self.http(method, url, headers, data)
        if code == 401:                                                   # an expired access token: once more with a fresh one
            self._access = None
            headers["Authorization"] = "Bearer " + self.token()
            code, raw = self.http(method, url, headers, data)
        got = _json(raw)
        if code >= 400:
            msg = (got.get("error") or {}).get("message") if isinstance(got.get("error"), dict) else got.get("error")
            raise RuntimeError(f"Spotify said {code}: {msg or raw[:120]!r}")
        return got

    # ── what the hub and purple ask for ──
    def search(self, q: str, kind: str = "track", n: int = PAGE) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while len(out) < n and offset < 200:
            got = self.call("GET", "/search", {"q": q, "type": kind, "limit": PAGE, "offset": offset})
            items = [i for i in ((got.get(kind + "s") or {}).get("items") or []) if i]
            out += items
            if len(items) < PAGE:
                break
            offset += PAGE
        return out[:n]

    def tracks_for(self, what: str, n: int = 30) -> dict:
        """Tracks for "play <what> on Spotify": a named artist's songs (no top-tracks endpoint any more: a search by artist), else a search."""
        want = _norm(what)
        for a in self.search(what, "artist", 3):
            if _norm(a.get("name", "")) == want:
                # a plain search, kept to his tracks: the field filter (artist:"...") answers only 5 in Development Mode (measured 2026-10-31)
                found = self.search(a["name"], "track", min(200, n * 2))
                tracks = [t for t in found if any(x.get("id") == a.get("id") for x in t.get("artists") or [])][:n]
                if tracks:
                    return {"kind": "artist", "name": a["name"], "tracks": [brief(t) for t in tracks]}
        return {"kind": "search", "name": what, "tracks": [brief(t) for t in self.search(what, "track", min(n, 10))]}

    def playlists(self) -> list[dict]:
        out, url_params = [], {"limit": 50, "offset": 0}
        while url_params["offset"] < 500:
            got = self.call("GET", "/me/playlists", dict(url_params))
            items = [p for p in got.get("items") or [] if p]
            out += [{"id": p.get("id", ""), "name": p.get("name", ""), "uri": p.get("uri", ""), "mine": (p.get("owner") or {}).get("id") == self._me(),
                     "count": ((p.get("items") or p.get("tracks") or {}).get("total") or 0)} for p in items]
            if not got.get("next"):
                break
            url_params["offset"] += 50
        return out

    _me_id: str | None = None

    def _me(self) -> str:
        if self._me_id is None:
            try:
                self._me_id = self.call("GET", "/me").get("id", "")
            except Exception:                                             # noqa: BLE001
                return ""
        return self._me_id

    def find_playlist(self, name: str) -> dict | None:
        lists = self.playlists()
        want = _norm(name)
        want = re.sub(r"^(?:my |the )?|(?: playlist)$", "", want).strip() or want
        exact = [p for p in lists if _norm(p["name"]) == want]
        if exact:
            return exact[0]
        inside = [p for p in lists if want and want in _norm(p["name"])]
        if inside:
            return min(inside, key=lambda p: len(p["name"]))
        close = difflib.get_close_matches(want, [_norm(p["name"]) for p in lists], n=1, cutoff=0.6)
        return next((p for p in lists if _norm(p["name"]) == close[0]), None) if close else None

    def playlist_tracks(self, pid: str, n: int = 500) -> list[dict]:
        out, offset = [], 0
        while len(out) < n:
            got = self.call("GET", f"/playlists/{pid}/items", {"limit": 100, "offset": offset})
            items = got.get("items") or []
            for it in items:
                t = (it or {}).get("item") or (it or {}).get("track")
                if t and t.get("type", "track") == "track" and t.get("uri", "").startswith("spotify:track:"):
                    out.append(brief(t))
            if not got.get("next") or not items:
                break
            offset += 100
        return out[:n]

    def liked(self, n: int = 200) -> list[dict]:
        out, offset = [], 0
        while len(out) < n:
            got = self.call("GET", "/me/tracks", {"limit": 50, "offset": offset})
            items = got.get("items") or []
            out += [brief(i.get("track")) for i in items if i and i.get("track")]
            if not got.get("next") or not items:
                break
            offset += 50
        return out[:n]

    def add_to_playlist(self, name_or_id: str, uris: list[str]) -> dict:
        p = self.find_playlist(name_or_id) if not re.fullmatch(r"[A-Za-z0-9]{22}", name_or_id or "") else {"id": name_or_id, "name": name_or_id, "mine": True}
        if p is None:
            raise LookupError(f"none of his playlists is called {name_or_id!r}")
        self.call("POST", f"/playlists/{p['id']}/items", body={"uris": uris})
        return p

    def save(self, uris: list[str]) -> None:
        self.call("PUT", "/me/library", {"uris": ",".join(uris)})


def _fingerprint(s: str) -> str:
    import hashlib                                                        # noqa: PLC0415
    return hashlib.sha256(s.encode()).hexdigest()[:16] if s else ""


def _json(raw: bytes) -> dict:
    try:
        got = json.loads(raw or b"{}")
        return got if isinstance(got, dict) else {"value": got}
    except ValueError:
        return {}


# ── the player (go-librespot) ─────────────────────────────────────────────────────────────────────────────────────────────────────
class Player:
    def __init__(self, base: str = PLAYER, http=_http):
        self.base, self.http = base.rstrip("/"), http

    def _req(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        try:
            code, raw = self.http(method, self.base + path, {"Content-Type": "application/json"}, data, 10.0)
        except OSError as e:
            return 0, {"error": f"the player is not answering ({type(e).__name__})"}
        return code, _json(raw)

    def status(self) -> dict:
        code, got = self._req("GET", "/status")
        if code == 204:
            return {"logged_in": False}
        if code != 200:
            return {"logged_in": False, "error": got.get("error") or f"player said {code}"}
        t = got.get("track") or {}
        return {"logged_in": True, "playing": not got.get("stopped") and not got.get("paused") and bool(t), "paused": bool(got.get("paused")),
                "stopped": bool(got.get("stopped")), "buffering": bool(got.get("buffering")), "uri": t.get("uri", ""), "title": t.get("name", ""),
                "artist": ", ".join(t.get("artist_names") or []), "album": t.get("album_name", ""), "pos_ms": t.get("position") or 0,
                "ms": t.get("duration") or 0, "origin": got.get("play_origin") or "", "volume": got.get("volume"), "device": got.get("device_name", "")}

    def pairing(self) -> dict | None:
        code, got = self._req("GET", "/auth/code")
        return {"url": got.get("url", ""), "code": got.get("code", ""), "expires_at": got.get("expires_at", "")} if code == 200 else None

    def play(self, uri: str, pos_ms: int = 0) -> int:
        return self._req("POST", "/player/play", {"uri": uri, "position": int(pos_ms)} if pos_ms else {"uri": uri})[0]

    def pause(self) -> int:
        return self._req("POST", "/player/pause")[0]

    def resume(self) -> int:
        return self._req("POST", "/player/resume")[0]


def librespot_config(name: str) -> str:
    """go-librespot's config.yml: the pipe backend in f32le, its API on localhost only, the device login (spotify.com/pair), no autoplay
    (the hub's queue decides what comes next) and no crossfade (his streams are played as they are)."""
    return "\n".join([
        f"device_name: {json.dumps(name)}", "device_type: avr", "audio_backend: pipe", f"audio_output_pipe: {FIFO}", "audio_output_pipe_format: f32le",
        "bitrate: 320", "initial_volume: 100", "ignore_last_volume: true", "disable_autoplay: true", "crossfade_duration: 0",
        "credentials:", "  type: device_auth", "server:", "  enabled: true", "  address: 127.0.0.1", f"  port: {PLAYER.rsplit(':', 1)[-1]}", ""])


def run_librespot(name: str, log=print) -> None:
    """go-librespot, kept up: a crash or an exit starts it again (backing off to a minute)."""
    cfg = STATE / "librespot"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yml").write_text(librespot_config(name), encoding="utf-8")
    wait = 2.0
    while True:
        started = time.time()
        code = subprocess.call(["/usr/bin/go-librespot", "--config_dir", str(cfg)])
        log(f"go-librespot exited ({code}); starting it again")
        wait = 2.0 if time.time() - started > 120 else min(60.0, wait * 2)
        time.sleep(wait)


# ── the sound: FIFO → one TCP client ─────────────────────────────────────────────────────────────────────────────────────────────
class Relay:
    def __init__(self, fifo: Path = FIFO, port: int = PCM_PORT, host: str = "0.0.0.0", log=print, sleep=time.sleep):
        self.fifo, self.port, self.host, self.log, self.sleep = fifo, port, host, log, sleep
        self.client: socket.socket | None = None
        self.sent = 0                                                     # bytes handed to the hub (the readiness report's progress)

    def open_fifo(self) -> int:
        self.fifo.parent.mkdir(parents=True, exist_ok=True)
        if not self.fifo.exists():
            os.mkfifo(self.fifo)
        return os.open(self.fifo, os.O_RDWR)                              # read+write: the FIFO always has a reader, and never reads EOF

    @staticmethod
    def drain(fd: int) -> int:
        """Throw away what is already in the FIFO (written while nobody listened): a new client starts from now."""
        os.set_blocking(fd, False)
        n = 0
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                n += len(chunk)
        except BlockingIOError:
            pass
        finally:
            os.set_blocking(fd, True)
        return n

    def serve(self) -> None:
        fd = self.open_fifo()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(2)
        self.log(f"relay: {self.fifo} -> tcp :{self.port}")
        while True:
            self.step(srv, fd)

    def step(self, srv, fd: int) -> None:
        if self.client is None:
            r, _, _ = select.select([srv, fd], [], [], 0.5)
            if srv in r:
                self.client, addr = srv.accept()
                self.client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.log(f"relay: the hub is listening ({addr[0]}), {self.drain(fd)} old bytes dropped")
                return
            if fd in r:                                                   # nobody is listening: keep the player moving at real time, drop it
                chunk = os.read(fd, 16384)
                self.sleep(len(chunk) / BYTES_PER_S)
            return
        r, _, _ = select.select([fd, self.client], [], [], 0.5)
        if self.client in r:
            try:
                gone = not self.client.recv(4096)
            except OSError:
                gone = True
            if gone:
                self._drop()
                return
        if fd in r:
            chunk = os.read(fd, 65536)
            try:
                self.client.sendall(chunk)                                # blocks while the hub is not reading: the player waits (real-time pace)
                self.sent += len(chunk)
            except OSError:
                self._drop()

    def _drop(self) -> None:
        c, self.client = self.client, None
        if c is not None:
            try:
                c.close()
            except OSError:
                pass
            self.log("relay: the hub stopped listening")


# ── the API ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
class Service:
    def __init__(self, web: WebApi, player: Player, relay: Relay | None = None, home_token: str = ""):
        self.web, self.player, self.relay, self.home_token = web, player, relay, home_token
        self.readiness = ready_mod.Readiness("spotify")

    def health(self) -> dict:
        st = self.player.status()
        pair = None if st.get("logged_in") else self.player.pairing()
        web_ok = self.web.signed_in and not self.web.error
        web_detail = self.web.error or ("" if self.web.signed_in else "not signed in (services/spotify/signin.py)")
        player_detail = "" if st.get("logged_in") else (f"pair it: {pair['url']} code {pair['code']}" if pair else (st.get("error") or "not logged in"))
        deps = {"web_api": {"ready": web_ok, "detail": web_detail}, "player": {"ready": bool(st.get("logged_in")), "detail": player_detail}}
        return self.readiness.report(True, deps=deps, pair=pair, playing=bool(st.get("playing")), listening=bool(self.relay and self.relay.client))

    def handle(self, method: str, path: str, query: dict, body: dict, auth: str = "") -> tuple[int, dict]:
        q = lambda k, d="": (query.get(k) or [d])[0]                     # noqa: E731
        try:
            if method == "GET" and path == "/health":
                h = self.health()
                return ready_mod.status_code(h), h
            if method == "GET" and path == "/now":
                return 200, self.player.status()
            if method == "POST" and path == "/play":
                uri = str(body.get("uri") or "")
                if not re.fullmatch(r"spotify:(track|album|playlist|artist|episode):[A-Za-z0-9]{22}|spotify:user:[^:]+:collection", uri):
                    return 400, {"error": "uri must be a spotify: URI"}
                with self.readiness.job("play"):
                    code = self.player.play(uri, int(body.get("pos_ms") or 0))
                return (200, {"ok": True}) if code == 200 else (502, {"error": f"the player said {code}"})
            if method == "POST" and path in ("/pause", "/resume"):
                code = (self.player.pause if path == "/pause" else self.player.resume)()
                return (200, {"ok": True}) if code == 200 else (502, {"error": f"the player said {code}"})
            if method == "GET" and path == "/tracks":
                with self.readiness.job("tracks"):
                    return 200, self.web.tracks_for(q("what"), int(q("n", "30")))
            if method == "GET" and path == "/search":
                kind = q("type", "track")
                if kind not in ("track", "artist", "album", "playlist"):
                    return 400, {"error": "type is track, artist, album or playlist"}
                with self.readiness.job("search"):
                    items = self.web.search(q("q"), kind, min(50, int(q("n", "10"))))
                if kind == "track":
                    return 200, {"items": [brief(t) for t in items]}
                return 200, {"items": [{"uri": i.get("uri", ""), "name": i.get("name", ""),
                                        "by": ", ".join(a.get("name", "") for a in i.get("artists") or []) or (i.get("owner") or {}).get("display_name", "")}
                                       for i in items]}
            if method == "GET" and path == "/playlists":
                return 200, {"playlists": self.web.playlists()}
            if method == "GET" and path == "/playlist":
                p = {"id": q("id"), "name": q("id")} if q("id") else self.web.find_playlist(q("name"))
                if p is None:
                    return 404, {"error": f"none of his playlists is called {q('name')!r}"}
                return 200, {"playlist": p, "tracks": self.web.playlist_tracks(p["id"])}
            if method == "GET" and path == "/liked":
                return 200, {"tracks": self.web.liked(int(q("n", "200")))}
            if method == "POST" and path == "/playlist/add":
                uris = [u for u in body.get("uris") or [] if isinstance(u, str) and u.startswith("spotify:track:")]
                if not uris or not body.get("playlist"):
                    return 400, {"error": "playlist and uris (spotify:track:...) are needed"}
                p = self.web.add_to_playlist(str(body["playlist"]), uris)
                return 200, {"playlist": p}
            if method == "POST" and path == "/save":
                uris = [u for u in body.get("uris") or [] if isinstance(u, str) and u.startswith("spotify:")]
                if not uris:
                    return 400, {"error": "uris are needed"}
                self.web.save(uris)
                return 200, {"ok": True}
            if method == "POST" and path == "/token":
                if not self.home_token or auth != "Bearer " + self.home_token:
                    return 403, {"error": "the home token is needed"}
                if not body.get("client_id") or not body.get("refresh_token"):
                    return 400, {"error": "client_id and refresh_token"}
                self.web.set_token(str(body["client_id"]), str(body["refresh_token"]))
                self.web.token()
                return 200, {"ok": True}
            return 404, {"error": "no such endpoint"}
        except NotSignedIn as e:
            return 503, {"error": str(e)}
        except LookupError as e:
            return 404, {"error": str(e)}
        except Exception as e:                                            # noqa: BLE001 — said back as words, never a stack trace
            return 502, {"error": f"{type(e).__name__}: {str(e)[:200]}"}


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        def _do(self, method: str) -> None:
            u = urllib.parse.urlsplit(self.path)
            body = {}
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                body = _json(self.rfile.read(min(n, 65536)))
            code, out = service.handle(method, u.path, urllib.parse.parse_qs(u.query), body, self.headers.get("Authorization", ""))
            raw = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802
            self._do("GET")

        def do_POST(self):  # noqa: N802
            self._do("POST")

        def log_message(self, *_a):
            pass
    return Handler


def main() -> None:
    web = WebApi(os.environ.get("SPOTIFY_CLIENT_ID", ""), os.environ.get("SPOTIFY_REFRESH_TOKEN", ""))
    relay = Relay()
    threading.Thread(target=relay.serve, daemon=True, name="relay").start()
    time.sleep(0.5)                                                       # the FIFO exists before the player opens it
    threading.Thread(target=run_librespot, args=(os.environ.get("SPOTIFY_DEVICE_NAME", "Skippy"),), daemon=True, name="librespot").start()
    service = Service(web, Player(), relay, os.environ.get("SKIPPY_HOME_TOKEN", ""))
    print(f"spotify: api on :{API_PORT}, web sign-in {'yes' if web.signed_in else 'NO (services/spotify/signin.py)'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", API_PORT), make_handler(service)).serve_forever()


if __name__ == "__main__":
    main()
