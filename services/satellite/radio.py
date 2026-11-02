"""Bilby's house radio: ONE station on skippy-pc, every speaker tuned in (the Captain, 2026-11-01: "Bilby on SkippyTel should be the DJ, not
those hubs ... There is one tap for music everywhere, and it's playing the same tune for everyone, like a radio station. The hubs can change
it, or add to it, but they are not the players").

Before it, the DJ and the player lived inside hub.py, so every hub restart (25 of them on 2026-11-01) killed the music, and three pods would
have been three DJs. Now this is its own process (tools/radio_run.sh, the SkippyRadio task), and the hub is a remote:

  * the station: stereo.StereoMusic with dj.DjPlayer (the beat-matched mixing), his Spotify (local first, an instant fallback to Bilby),
    the queue, discovery, favorites and the grammar's filters (station.py runs every command);
  * its own audio router (router.py) for its own speakers, the same outputs and the same delays as the hub's (sync.py's alignment), so the
    floors stay together: the Sony on skippy-pc's HDMI (Windows mixes it with the hub's voice), the Enclave over the tailnet into skippy-core's
    snapserver on its OWN source, `radio` on :4954 (core/install/enclave.sh: a second snapclient, PipeWire mixes it with the hub's), and every
    pod tuned in through `pods`: one live FLAC stream (GET /live/<secret>.flac on RADIO_LIVE_PORT) the pod's media player plays from here;
  * its state (state/radio/station.json: the queue, where it is in it, which speakers are on, the volume), written every few seconds, so
    its own restart resumes where it was;
  * a small API on 127.0.0.1:RADIO_PORT (no door to the network; the hubs and the portal's Connect are its remotes):

      GET  /state                      now playing, position, speakers (on / off), volume, DJ mode, the pods' live stream
      POST /tool {name, args, room}    the hubs' music tools (station.run): play_music (his grammar), music, speakers, dj_mode, spotify
      POST /control {action}           pause | resume | next | previous
      POST /shuffle                    the whole library (the morning routine)
      POST /duck {kinds, on, hold_s}   Skippy speaks in a room: that room's speakers duck the station, nothing else
      POST /take {state, room}         a Connect "Play on" of a pod: pick up that player's queue, on that room's speakers

  * Bilby Connect: the station checks in as "House stereo" (skippyroom-stereo), so the portal's players and the car control it.

    python services/satellite/radio.py          run it (tools/radio_run.sh keeps it running; log state/radio.log)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import router as router_mod                                       # noqa: E402
import station                                                    # noqa: E402
import stereo                                                     # noqa: E402

STATE = Path(__file__).resolve().parents[2] / "state" / "radio"
PORT = 47850                                                      # the API, 127.0.0.1 only
LIVE_PORT = 47851                                                 # the pods' live stream, on the home network (its path is a secret)
MEDIA_PORT = 10801                                                # the radio's own media server (a track a DJ chain can't read from disk)
SAVE_S = 3.0
PODS = "pods"                                                     # the router output that feeds every pod tuned in


# ── the pods' live stream: a router output that is no device ──────────────────────────────────────────────────────────────────
class Tap:
    """The station's mix for the pods, paced by this PC's clock like router.NetStream, handed to every listener (each a live.flac)."""

    def __init__(self, callback, clock=time.monotonic, start: bool = True):
        self.callback, self.clock = callback, clock
        self.latency = 0.0
        self.alive = True
        self.listeners: set[queue.Queue] = set()
        self.lock = threading.Lock()
        self._stop = threading.Event()
        if start:
            threading.Thread(target=self._loop, daemon=True, name="radio-tap").start()

    def listen(self, depth: int = 100) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=depth)
        with self.lock:
            self.listeners.add(q)
        return q

    def leave(self, q) -> None:
        with self.lock:
            self.listeners.discard(q)

    def once(self) -> None:
        np = router_mod._np()                                     # noqa: SLF001
        buf = np.zeros((router_mod.BLOCK, 2), dtype="float32")
        self.callback(buf, router_mod.BLOCK, None, None)
        with self.lock:
            listeners = list(self.listeners)
        for q in listeners:
            try:
                q.put_nowait(buf)
            except queue.Full:                                    # a pod that stopped reading: its oldest block goes
                try:
                    q.get_nowait()
                    q.put_nowait(buf)
                except (queue.Empty, queue.Full):
                    pass

    def _loop(self) -> None:
        nxt = self.clock()
        while not self._stop.is_set():
            self.once()
            nxt += router_mod.BLOCK_S
            pause = nxt - self.clock()
            if pause > 0:
                time.sleep(pause)
            elif pause < -1.0:
                nxt = self.clock()

    def stop(self) -> None:
        self._stop.set()
        self.alive = False

    def close(self) -> None:
        self.stop()


TAPS: dict[str, Tap] = {}


def _find(part: str):
    if part.startswith("tap://"):
        return (part, PODS)
    return router_mod._find_device(part)                          # noqa: SLF001


def _factory(device, callback):
    if isinstance(device, str) and device.startswith("tap://"):
        TAPS[device] = Tap(callback)
        return TAPS[device]
    return router_mod._sd_stream(device, callback)                # noqa: SLF001


def outputs(env: dict) -> list[tuple[str, str, float]]:
    """The station's speakers as router outputs: the hub's own outputs and places (the same names and the same delays as router.shared),
    the Enclave on the radio's own snapserver source, and the pods' tap."""
    import speaker_out                                            # noqa: PLC0415
    import sync                                                   # noqa: PLC0415
    parts = speaker_out.output_parts(env)
    parts["enclave"] = env.get("RADIO_OUT_ENCLAVE", "tcp://skippy-core:4954")
    used = {k for k in router_mod.used_kinds(env) if k in parts}
    delays = speaker_out.output_delays(env)
    if speaker_out.one_voice(env):                                # the hub's sums exactly (router.shared), so the station sounds with his voice
        align = sync.saved_align(used)
        for k in used:
            delays[k] = float(env.get("SATELLITE_SOLO_DELAY", str(router_mod.SOLO_DELAY_S))) + align.get(k, 0.0)
    else:
        delays.update(sync.saved_delays())
    return [(k, parts[k], delays.get(k, 0.0)) for k in sorted(used)] + [(PODS, "tap://pods", 0.0)]


def live_secret(token: str) -> str:
    """The pods' stream path: from the home token, so it is the same after a restart and nobody on the network can guess it."""
    return hashlib.sha256(b"skippy-radio-live:" + token.encode()).hexdigest()[:24]


# ── the station ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
class HouseRadio(stereo.StereoMusic):
    """StereoMusic whose speakers can be pods too ("pod:<room>": the pods' tap), ducked one room at a time."""

    def __init__(self, router, kinds, media, **kw):
        super().__init__(router, kinds, media, **kw)
        self.speakers: set[str] = set(kinds)
        self._ducks: dict[str, asyncio.TimerHandle] = {}

    def move(self, kinds) -> None:
        self.speakers = set(kinds)
        out = {k for k in kinds if not k.startswith(station.POD)}
        if len(out) < len(self.speakers):
            out.add(PODS)
        super().move(out)

    def where(self) -> tuple[set, set]:
        r = self.player.router
        open_ = {k for k in self.speakers if r.available(PODS if k.startswith(station.POD) else k)}
        return open_, self.speakers - open_

    def duck_kinds(self, kinds, on: bool, hold_s: float = 30.0) -> None:
        """Skippy speaks in a room: its speakers duck the station (the pods' tap too, which every pod plays), the rest play on."""
        r = self.player.router
        kinds = {PODS if k.startswith(station.POD) or k == "pod" else k for k in kinds} & set(r.sinks)
        if on and kinds - set(self._ducks):
            self.log(f"(radio: ducked under Skippy: {', '.join(sorted(kinds))})")
        for k in kinds:
            old = self._ducks.pop(k, None)
            if old is not None:
                old.cancel()
        r.duck_kinds(kinds, on)
        if on:
            loop = asyncio.get_running_loop()
            for k in kinds:
                self._ducks[k] = loop.call_later(hold_s, lambda k=k: (self._ducks.pop(k, None), r.duck_kinds({k}, False)))

    # ── what it remembers across its own restarts ──
    def snapshot(self) -> dict:
        b = self.box
        return {"queue": list(b.queue), "idx": b.idx, "pos": b.pos(), "playing": bool(b.playing), "speakers": sorted(self.speakers),
                "level": self.level, "spotify_titles": {q: list(t) for q, t in b.spotify_titles.items() if q in set(b.queue)}}

    async def restore(self, saved: dict, log=print) -> bool:
        """Back where it was: the speakers, the volume, and (if it was playing) the same track at the same place."""
        if saved.get("speakers"):
            self.move(saved["speakers"])
        if saved.get("level"):
            self.set_level(float(saved["level"]))
        self.box.spotify_titles.update({k: tuple(v) for k, v in (saved.get("spotify_titles") or {}).items()})
        q = [t for t in saved.get("queue") or [] if isinstance(t, str)]
        if not q:
            return False
        idx = min(max(0, int(saved.get("idx") or 0)), len(q) - 1)
        if saved.get("playing"):
            await self.box.play_queue(q, idx, float(saved.get("pos") or 0.0))
            log(f"(radio: resumed where it was: track {idx + 1} of {len(q)} at {float(saved.get('pos') or 0):.0f} s)")
            return True
        self.box.queue, self.box.idx, self.box.offset, self.box._queue_sent = q, idx, float(saved.get("pos") or 0.0), False
        try:
            self.box.titles = await self.media.catalog()          # "what's playing" names it, not its file
        except Exception:                                         # noqa: BLE001 — titles are a nicety
            pass
        return False


class Radio:
    """The service: the station, its state file, its API, its Connect check-in."""

    def __init__(self, env: dict, house: HouseRadio, favorites=None, state: Path = STATE, log=print, clock=time.time):
        self.env, self.house, self.favorites, self.state, self.log, self.clock = env, house, favorites, Path(state), log, clock
        self.secret = live_secret(env.get("SKIPPY_HOME_TOKEN", "") or "local")
        self.live_port = int(env.get("RADIO_LIVE_PORT", LIVE_PORT))
        self._saved = None

    # ── state ──
    @property
    def path(self) -> Path:
        return self.state / "station.json"

    def save(self) -> None:
        snap = self.house.snapshot()
        if snap == self._saved:
            return
        self.state.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**snap, "at": self.clock()}), encoding="utf-8")
        tmp.replace(self.path)
        self._saved = snap

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    async def keep_saving(self, every: float = SAVE_S) -> None:
        while True:
            try:
                self.save()
            except Exception as e:                                # noqa: BLE001 — the music plays on
                self.log(f"(radio: could not save its state: {type(e).__name__}: {e})")
            await asyncio.sleep(every)

    def now(self) -> dict:
        import dj                                                 # noqa: PLC0415
        b, h = self.house.box, self.house
        title, artist = b.title()
        open_, closed = h.where()
        return {"playing": bool(b.playing), "track": b.track(), "title": title, "artist": artist, "pos": b.pos(), "idx": b.idx,
                "queue_len": len(b.queue), "speakers": sorted(h.speakers), "open": sorted(open_), "closed": sorted(closed),
                "where": station.where_said(h), "level": h.level, "dj": dj.enabled(self.env),
                "live": {"port": self.live_port, "path": f"/live/{self.secret}.flac"}}

    # ── what it is told ──
    async def tool(self, name: str, args: dict, room: str = "") -> str:
        try:
            said = await station.run(self.house, name, args, room, self.favorites)
        except Exception as e:                                    # noqa: BLE001 — said aloud, never raised
            self.log(f"(radio: {name} failed: {type(e).__name__}: {e})")
            said = f"The house radio could not do that ({type(e).__name__})."
        self.log(f"[radio] {room or '-'} {name} {json.dumps(args, ensure_ascii=False)[:160]} -> {said[:160]}")
        self.save()
        return said

    async def control(self, action: str) -> bool:
        b = self.house.box
        step = {"pause": b.pause, "stop": b.pause, "resume": b.resume, "play": b.resume, "next": b.next, "previous": b.prev, "prev": b.prev}.get(action)
        if step is None:
            return False
        await step()
        self.save()
        return True

    async def take(self, state: dict, room: str = "") -> None:
        kinds = station.here_kinds(self.env, room) if room else None
        if kinds:
            self.house.move(kinds)
        await self.house.box.take(state)
        self.save()

    # ── the HTTP side (aiohttp) ──
    def app(self):
        from aiohttp import web                                   # noqa: PLC0415

        async def state(_request):
            return web.json_response(self.now())

        async def tool(request):
            body = await request.json()
            said = await self.tool(str(body.get("name") or ""), body.get("args") or {}, str(body.get("room") or ""))
            return web.json_response({"said": said, "state": self.now()})

        async def control(request):
            body = await request.json()
            ok = await self.control(str(body.get("action") or ""))
            return web.json_response({"ok": ok, "state": self.now()}, status=200 if ok else 400)

        async def shuffle(_request):
            n = await self.house.shuffle()
            self.save()
            return web.json_response({"queued": n, "state": self.now()})

        async def duck(request):
            body = await request.json()
            self.house.duck_kinds(body.get("kinds") or [], bool(body.get("on")), float(body.get("hold_s") or 30.0))
            return web.json_response({"ok": True})

        async def take(request):
            body = await request.json()
            await self.take(body.get("state") or {}, str(body.get("room") or ""))
            return web.json_response({"ok": True, "state": self.now()})

        app = web.Application()
        app.router.add_get("/state", state)
        app.router.add_get("/health", state)
        app.router.add_post("/tool", tool)
        app.router.add_post("/control", control)
        app.router.add_post("/shuffle", shuffle)
        app.router.add_post("/duck", duck)
        app.router.add_post("/take", take)
        return app

    def live_app(self):
        from aiohttp import web                                   # noqa: PLC0415

        async def live(request):
            if request.match_info["secret"] != self.secret:
                raise web.HTTPNotFound()
            tap = TAPS.get("tap://pods")
            if tap is None:
                raise web.HTTPServiceUnavailable()
            return await self.stream(request, tap)
        app = web.Application()
        app.router.add_get("/live/{secret}.flac", live)
        return app

    async def stream(self, request, tap: Tap):
        """One pod tuned in: the tap's blocks, through ffmpeg, as the endless 16 kHz mono FLAC a pod plays (media.py's format)."""
        from aiohttp import web                                   # noqa: PLC0415
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-nostdin", "-f", "f32le", "-ar", str(router_mod.RATE), "-ac", "2", "-i", "pipe:0",
            "-ac", "1", "-ar", "16000", "-f", "flac", "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        q = tap.listen()
        loop = asyncio.get_running_loop()
        done = threading.Event()

        def feed() -> None:
            while not done.is_set():
                try:
                    block = q.get(timeout=1.0)
                except queue.Empty:
                    continue
                try:
                    asyncio.run_coroutine_threadsafe(_write(block.tobytes()), loop).result(5)
                except Exception:                                 # noqa: BLE001 — the pod went away
                    break

        async def _write(data: bytes) -> None:
            proc.stdin.write(data)
            await proc.stdin.drain()
        threading.Thread(target=feed, daemon=True, name="radio-live-feed").start()
        resp = web.StreamResponse(headers={"Content-Type": "audio/flac", "Cache-Control": "no-store"})
        await resp.prepare(request)
        self.log(f"(radio: a pod tuned in from {request.remote})")
        try:
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                await resp.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            done.set()
            tap.leave(q)
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            self.log(f"(radio: a pod tuned out: {request.remote})")
        return resp


def build(env: dict, log=print) -> tuple[Radio, object]:
    """The station on its own router, its own media server, its Spotify and his favorites."""
    import music as music_mod                                     # noqa: PLC0415
    import speaker_out                                            # noqa: PLC0415
    import spotify as spotify_mod                                 # noqa: PLC0415
    from media import MediaServer                                 # noqa: PLC0415
    r = router_mod.Router(outputs(env), find=_find, stream_factory=_factory, log=log,
                          prefer=[c for c in speaker_out.prefer_chains({"SKIPPY_STEREO_OUT": env.get("SKIPPY_STEREO_OUT", "")})])
    r.start()
    media = MediaServer(port=int(env.get("RADIO_MEDIA_PORT", MEDIA_PORT)), host=env.get("SATELLITE_MEDIA_HOST", ""),
                        gate=env.get("SATELLITE_MUSIC_GATE", "http://127.0.0.1:3005"), home_token=env.get("SKIPPY_HOME_TOKEN", ""))
    media.log = log
    spot = None if env.get("SKIPPY_SPOTIFY", "on").lower() in ("off", "0", "no") else spotify_mod.Client.from_env(env)
    kinds = speaker_out.split_kinds(env.get("SKIPPY_STEREO_OUT", "hdmi"))
    house = HouseRadio(r, kinds, media, log=log, spotify=spot, env=env)
    token = env.get("SKIPPY_HOME_TOKEN", "")
    favs = music_mod.Favorites(env.get("PORTAL_URL") or music_mod.PORTAL, token) if token else None
    return Radio(env, house, favorites=favs, log=log), media


async def _main(env: dict) -> int:
    from aiohttp import web                                       # noqa: PLC0415
    import music as music_mod                                     # noqa: PLC0415
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)   # noqa: E731
    radio, media = build(env, log)
    await media.start()
    runners = []
    for app, host, port in ((radio.app(), "127.0.0.1", int(env.get("RADIO_PORT", PORT))), (radio.live_app(), "0.0.0.0", radio.live_port)):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        runners.append(runner)
    radio.house.start()                                           # follow the Spotify speaker
    await asyncio.sleep(1.5)                                      # the outputs open (PortAudio's first scan, the Enclave's status)
    await radio.house.restore(radio.load(), log)
    log(f"radio: on the air at 127.0.0.1:{env.get('RADIO_PORT', PORT)}, speakers {sorted(radio.house.speakers)}")
    connect = None
    if env.get("SKIPPY_HOME_TOKEN"):                              # the station is a Bilby Connect player: the portal's players and the car steer it
        connect = music_mod.Connect(env.get("PORTAL_URL", music_mod.PORTAL), env["SKIPPY_HOME_TOKEN"], log=log)
        connect.add("stereo", env.get("SKIPPY_STEREO_NAME", "House stereo"), radio.house.box)
    try:
        await asyncio.gather(radio.keep_saving(), *([connect.run()] if connect is not None else []))
    finally:
        radio.save()
        for runner in runners:
            await runner.cleanup()
        await media.stop()
    return 0


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    from homectl import load_env                                  # noqa: PLC0415 — the vault .env, without the audio libraries
    env = load_env()
    os.environ.setdefault("PYTHONUTF8", "1")
    return asyncio.run(_main(env))


if __name__ == "__main__":
    sys.exit(main())
