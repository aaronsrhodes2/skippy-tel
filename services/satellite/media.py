"""The hub's media server: what the room boxes play comes from here, over the home network (never the internet, never the portal).

    GET /v/<secret>/<id>.mp3|.wav            one of Skippy's spoken answers (kept in memory a few minutes, then gone)
    GET /m/<secret>/track.flac?p=<path>&s=<seconds>
                                            a track from Bilby's library, read from the local music gate (services/music-gate, on
                                            127.0.0.1:3005) with a pass the hub signs itself, and turned by ffmpeg into what the box plays:
                                            16 kHz mono FLAC (an .m4a is AAC, which an ESP32 cannot decode), starting `s` seconds in

<secret> is made fresh at every start and only handed to the boxes inside URLs: anyone else on the network gets a 404. The music gate's
pass is the same scheme as the portal's (portal/player.py), from the home token, so there is nothing new to configure.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import gzip
import hashlib
import hmac
import io
import json
import secrets
import socket
import time
import wave
from collections import OrderedDict
from urllib.parse import quote

CLIP_S = 600                                                     # a spoken answer is fetched within seconds; ten minutes is generous
MAX_CLIPS = 40
PASS_S = 8 * 3600
PASS_BUCKET_S = 6 * 3600
OUT_RATE = 16000                                                 # the box's speaker runs at 16 kHz (full duplex with its microphones)


def music_key(home_token: str) -> bytes:
    """The music gate's signing key (services/music-gate/gate.py gate_key), from the home token."""
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    return hmac.new(hashed.encode(), b"music-gate-v1", hashlib.sha256).digest()


def music_pass(key: bytes, now: float) -> str:
    exp = (int(now) // PASS_BUCKET_S + 1) * PASS_BUCKET_S + PASS_S
    return f"{exp}.{hmac.new(key, f'music:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]}"


def pcm_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def local_address(towards: str) -> str:
    """This machine's address as seen from `towards` (a box): the interface the route to it leaves by. No packet is sent."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((socket.gethostbyname(towards), 9))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def ffmpeg_args(source: str, start_s: float, speed: float = 1.0) -> list[str]:
    args = ["ffmpeg", "-v", "error", "-nostdin"]
    if start_s > 0:
        args += ["-ss", f"{start_s:.1f}"]
    tempo = ["-af", f"atempo={speed:.2f}"] if abs(speed - 1.0) > 0.01 else []     # an audiobook at 1.25x: atempo keeps the pitch
    return args + ["-i", source, "-vn", *tempo, "-ac", "1", "-ar", str(OUT_RATE), "-c:a", "flac", "-f", "flac", "pipe:1"]


class MediaServer:
    def __init__(self, port: int = 10800, host: str = "", gate: str = "http://127.0.0.1:3005", home_token: str = "",
                 clock=time.time, spawn=asyncio.create_subprocess_exec, fetch=None):
        self.port, self.host, self.gate, self.clock, self.spawn = port, host, gate.rstrip("/"), clock, spawn
        self.key = music_key(home_token) if home_token else b""
        self.secret = secrets.token_urlsafe(18)
        self.clips: OrderedDict[str, tuple[bytes, str, float]] = OrderedDict()
        self.fetch = fetch or self._fetch
        self._catalog: tuple[float, dict] | None = None
        self._runner = None
        self._seen_from: dict[str, str] = {}
        self.books: dict[str, str] = {}                          # token -> a book file on this machine (the box never sees a path)

    def _address(self, towards: str) -> str:
        if towards not in self._seen_from:                       # resolving skippy-kitchen.local takes a moment: once per box is enough
            self._seen_from[towards] = local_address(towards or "8.8.8.8")
        return self._seen_from[towards]

    # ── addresses ──
    def base(self, towards: str = "") -> str:
        return f"http://{self.host or self._address(towards)}:{self.port}"

    def put_clip(self, voice, towards: str = "") -> str:
        """A spoken answer ("mp3", bytes) or ("pcm", bytes, rate): its URL for the box."""
        if voice[0] == "mp3":
            body, kind, ext = voice[1], "audio/mpeg", "mp3"
        else:
            body, kind, ext = pcm_wav(voice[1], voice[2]), "audio/wav", "wav"
        clip = secrets.token_urlsafe(9)
        now = self.clock()
        self.clips[clip] = (body, kind, now + CLIP_S)
        while len(self.clips) > MAX_CLIPS or (self.clips and next(iter(self.clips.values()))[2] < now):
            self.clips.popitem(last=False)
        return f"{self.base(towards)}/v/{self.secret}/{clip}.{ext}"

    def track_url(self, path: str, start_s: float = 0.0, towards: str = "") -> str:
        return f"{self.base(towards)}/m/{self.secret}/track.flac?p={quote(path, safe='')}&s={max(0.0, start_s):.1f}"

    def book_url(self, path: str, start_s: float = 0.0, speed: float = 1.0, towards: str = "") -> str:
        """An audiobook file (a local file the library found) from `start_s`, for the box, through the same ffmpeg as a track."""
        token = hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:16]
        self.books[token] = path
        return f"{self.base(towards)}/b/{self.secret}/{token}.flac?s={max(0.0, start_s):.1f}&t={speed:.2f}"

    def gate_track(self, path: str) -> str:
        return f"{self.gate}/t/{music_pass(self.key, self.clock())}/a/{quote(path)}"

    # ── the library's titles (for the screen and for "play some Pink Floyd") ──
    def _fetch(self, url: str) -> bytes:
        import requests                                          # noqa: PLC0415
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content

    async def catalog(self) -> dict:
        """{path: (title, artist, album)} from the gate's catalog.json, cached ten minutes. {} when the gate is not answering."""
        if self._catalog and self.clock() - self._catalog[0] < 600:
            return self._catalog[1]
        try:
            raw = await asyncio.to_thread(self.fetch, f"{self.gate}/t/{music_pass(self.key, self.clock())}/catalog.json")
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            data = json.loads(raw)
            fields = data.get("fields") or []
            ip, it, ia, ial = (fields.index(f) if f in fields else -1 for f in ("p", "t", "a", "al"))
            out = {row[ip]: (row[it] if it >= 0 else "", row[ia] if ia >= 0 else "", row[ial] if ial >= 0 else "") for row in data.get("tracks") or []}
        except Exception:                                        # noqa: BLE001 — the gate is down or the catalog changed shape: no titles, still playable
            return self._catalog[1] if self._catalog else {}
        self._catalog = (self.clock(), out)
        return out

    # ── the HTTP side (aiohttp) ──
    async def start(self) -> None:
        from aiohttp import web                                  # noqa: PLC0415
        app = web.Application()
        app.router.add_get("/v/{secret}/{clip}", self._clip)
        app.router.add_get("/m/{secret}/track.flac", self._track)
        app.router.add_get("/b/{secret}/{token}", self._book)
        app.router.add_post("/shot", self._shot)                    # a box's screenshot (rooms/shot.py asks for it); LAN only, nothing secret on a screen
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _shot(self, request):
        from aiohttp import web                                  # noqa: PLC0415
        out = Path(__file__).resolve().parents[2] / "state"
        body = await request.read()
        (out / "box-shot.txt").write_text(request.headers.get("X-Dims", "480,480,960"), encoding="utf-8")
        (out / "box-shot.bin").write_bytes(body)
        return web.Response(text="ok")

    async def _clip(self, request):
        from aiohttp import web                                  # noqa: PLC0415
        if not hmac.compare_digest(request.match_info["secret"], self.secret):
            raise web.HTTPNotFound()
        clip = request.match_info["clip"].rsplit(".", 1)[0]
        found = self.clips.get(clip)
        if not found or found[2] < self.clock():
            raise web.HTTPNotFound()
        return web.Response(body=found[0], content_type=found[1])

    async def _book(self, request):
        from aiohttp import web                                  # noqa: PLC0415
        if not hmac.compare_digest(request.match_info["secret"], self.secret):
            raise web.HTTPNotFound()
        path = self.books.get(request.match_info["token"].rsplit(".", 1)[0])
        if not path:
            raise web.HTTPNotFound()
        try:
            start, speed = float(request.query.get("s", "0")), min(2.0, max(0.5, float(request.query.get("t", "1"))))
        except ValueError:
            start, speed = 0.0, 1.0
        proc = await self.spawn(*ffmpeg_args(path, start, speed), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        resp = web.StreamResponse(headers={"Content-Type": "audio/flac", "Cache-Control": "no-store"})
        await resp.prepare(request)
        try:
            while True:
                chunk = await proc.stdout.read(16384)
                if not chunk:
                    break
                await resp.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass                                                 # the box paused or skipped: stop transcoding
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        return resp

    async def _track(self, request):
        from aiohttp import web                                  # noqa: PLC0415
        if not hmac.compare_digest(request.match_info["secret"], self.secret) or not self.key:
            raise web.HTTPNotFound()
        path = request.query.get("p", "")
        try:
            start = float(request.query.get("s", "0"))
        except ValueError:
            start = 0.0
        if not path or ".." in path.split("/") or len(path) > 400:
            raise web.HTTPNotFound()
        proc = await self.spawn(*ffmpeg_args(self.gate_track(path), start), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        resp = web.StreamResponse(headers={"Content-Type": "audio/flac", "Cache-Control": "no-store"})
        await resp.prepare(request)
        try:
            while True:
                chunk = await proc.stdout.read(16384)
                if not chunk:
                    break
                await resp.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass                                                 # the box skipped or stopped: stop transcoding
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        return resp
