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

      GET  /state                      now playing (a song, a Spotify track or an audiobook: `kind`, `book`), position, the speakers (`outputs`: each with
                                       its switch, `listening`: the ones that hear it), `held`, volume, DJ mode, the pods' live stream
      POST /tool {name, args, room}    the hubs' music tools (station.run): play_music (his grammar), music, speakers, dj_mode, spotify, audiobook
      POST /control {action}           pause | resume | next | previous
      POST /shuffle                    the whole library (the morning routine)
      POST /duck {holder, on, hold_s}  Skippy speaks somewhere: the WHOLE stream goes down on every speaker while any holder (a room, the hub's router) talks
      POST /take {state, room}         a Connect "Play on" of a pod: pick up that player's queue, on that room's speakers
      POST /book/find {query}          the audiobook shelf: the one book that clearly matches, and all that match (the hub's plain spoken commands)
      POST /book/command {action, room}  one of the portal's `book:` commands (a pod forwards its own)

  * ONE stream, the devices decide (the Captain, 2026-11-05): an audiobook is an item of the queue (`book:<id>`), every speaker has a switch kept in
    state/radio/outputs.json (outputs.py; the places are shortcuts that set them), and with nobody listening the stream is held where the sound was lost and
    carries on when a speaker is back (HouseRadio.listen_tick). Skippy's voice ducks the whole stream, on every device.

  * Bilby Connect: the station checks in as "House stereo" (skippyroom-stereo), so the portal's players and the car control it.

    python services/satellite/radio.py          run it (tools/radio_run.sh keeps it running; log state/radio.log)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audiobooks                                                 # noqa: E402
import outputs as outputs_mod                                     # noqa: E402
import router as router_mod                                       # noqa: E402
import station                                                    # noqa: E402
import stereo                                                     # noqa: E402

STATE = Path(__file__).resolve().parents[2] / "state" / "radio"
PORT = 47850                                                      # the API, 127.0.0.1 only
LIVE_PORT = 47851                                                 # the pods' live stream, on the home network (its path is a secret)
MEDIA_PORT = 10801                                                # the radio's own media server (a track a DJ chain can't read from disk)
SAVE_S = 3.0
LISTEN_S = 1.0                                                    # how often the speakers' switches, the listeners and the car are looked at
HOLD_S = 30.0                                                     # nobody hears the stream for this long (a stereo that is on dropped out): it is held. RADIO_HOLD_S=0 turns it off
RESUME_S = 600.0                                                  # a stream held for lack of a speaker carries on by itself when one is back within this long
PODS = "pods"                                                     # the router output that feeds every pod tuned in
HOLD_WORDS = {"nobody": "no speaker is on", "car": "he is listening in the car"}


# ── the pods' live stream: a router output that is no device ──────────────────────────────────────────────────────────────────
class Tap:
    """The station's mix for the pods, paced by this PC's clock like router.NetStream, handed to every listener (each a live.flac)."""

    def __init__(self, callback, clock=time.monotonic, start: bool = True):
        self.callback, self.clock = callback, clock
        self.latency = 0.0
        self.alive = True
        self.listeners: set[queue.Queue] = set()
        self.labels: dict[queue.Queue, str] = {}                  # which room's box each listener is ("" when it did not say)
        self.lock = threading.Lock()
        self._stop = threading.Event()
        if start:
            threading.Thread(target=self._loop, daemon=True, name="radio-tap").start()

    def listen(self, depth: int = 100, label: str = "") -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=depth)
        with self.lock:
            self.listeners.add(q)
            self.labels[q] = label
        return q

    def leave(self, q) -> None:
        with self.lock:
            self.listeners.discard(q)
            self.labels.pop(q, None)

    def rooms(self) -> set[str]:
        """The rooms of the boxes tuned in now (outputs.Outputs.listening); "" stands for a box that did not say which room it is."""
        with self.lock:
            return set(self.labels.values())

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
    """StereoMusic whose speakers can be pods too ("pod:<room>": the pods' tap). ONE stream: every speaker has a switch (outputs.Outputs), the stream is held
    while nobody hears it, and Skippy's voice ducks all of it."""

    def __init__(self, router, kinds, media, outputs=None, mono=time.monotonic, wall=time.time, hold_s: float = HOLD_S, resume_s: float = RESUME_S, **kw):
        super().__init__(router, kinds, media, **kw)
        self.speakers: set[str] = set(kinds)
        self.mono, self.wall, self.hold_s, self.resume_s = mono, wall, hold_s, resume_s
        stereos = [k for k in router.sinks if k != PODS]
        self.outputs = outputs if outputs is not None else outputs_mod.Outputs(stereos, path=None, clock=mono, seed=kinds)
        self.held: dict | None = None                             # the stream is paused for lack of a listener ("nobody") or because the car has the book ("car")
        self._lost: tuple[float, float] | None = None             # (when, the place) nobody heard the stream any more
        self._duck_holders: dict[str, asyncio.TimerHandle] = {}   # who is speaking: a room, the hub's router; the stream is down while any is
        self.box.switch = self.switch_command                     # Connect: "out:<id>:on|off" (the portal's toggles)
        self.box.extra = self.connect_extra                       # Connect: the speakers and the hold, next to the track

    def attach_books(self, make) -> None:
        """The audiobook engine of this station: `make(out, on_end, on_halt)` builds it on the station's output. A book is an item of the queue, so when its last
        file ends the queue goes on, and when it stops itself (the sleep timer) the station is paused too."""
        async def ended() -> None:
            await self.box.advance()

        async def halted() -> None:
            engine = self.player.books
            self.box.offset, self.box.playing, self.box.epoch = engine.offset, False, self.box.epoch + 1
        self.player.books = make(self.player.book_out, ended, halted)

    # ── the speakers: switches in, a set of kinds out ──
    def _there(self, kind: str) -> bool:
        return self.player.router.available(kind)

    def apply(self) -> bool:
        """The speakers from the switches and from what is there (a box covers for its floor's stereo that is on but gone): move what plays. True when changed."""
        want = self.outputs.effective(self._there, self.mono())
        if want == self.speakers:
            return False
        self.move(want)
        return True

    def place(self, kinds) -> None:
        """A place in his words ("upstairs", "everywhere", a room) as switches (station.py): exactly those speakers on, the others off. What plays moves at once."""
        self.outputs.place(kinds)
        self.apply()

    def set_switch(self, ident: str, on: bool) -> bool:
        changed = self.outputs.set(ident, on)
        self.apply()
        return changed

    OUT_COMMAND = re.compile(r"out:(?P<id>[a-z0-9_-]{1,24}(?::[a-z0-9_-]{1,24})?):(?P<v>on|off)")

    def switch_command(self, action: str) -> bool:
        """Connect's "out:<id>:on|off". False for an id it does not have."""
        m = self.OUT_COMMAND.fullmatch(action)
        if not m or m["id"] not in self.outputs.ids():
            return False
        self.set_switch(m["id"], m["v"] == "on")
        return True

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

    def pod_rooms(self) -> set[str]:
        tap = TAPS.get("tap://pods")
        return tap.rooms() if tap is not None else set()

    def listening(self) -> set[str]:
        """The speakers that hear the stream right now: on, and really there (the Enclave has a snapclient, the Sony's output is open, a box holds the live stream)."""
        return self.outputs.listening(self._there, self.pod_rooms(), self.mono())

    def outputs_state(self) -> list[dict]:
        return self.outputs.snapshot(self._there, self.pod_rooms(), self.mono())

    def held_public(self) -> dict | None:
        return {"why": HOLD_WORDS.get(self.held["kind"], "waiting"), "since": self.held["since"], "kind": self.held["kind"]} if self.held else None

    def connect_extra(self) -> dict:
        """What the portal's Bilby page needs next to the track (portal/player.py keeps only fields it knows): each speaker's switch, and the hold."""
        outs = [{k: o[k] for k in ("id", "name", "on", "open", "floor")} for o in self.outputs_state()]
        return {"outs": outs, "held": ({"why": self.held_public()["why"], "since": self.held["since"]} if self.held else None)}

    def listening_note(self) -> str:
        """Said after a start that nobody would hear: the stream waits for a speaker (station.not_listening)."""
        if self.hold_s <= 0 or self.listening() or self.outputs.pending(self.hold_s, self.mono()):
            return ""
        off = ["the " + o["name"] for o in self.outputs_state() if not o["on"] and o.get("mode", "off") != "auto"]     # a box on auto is not switched off: it covers when it is needed
        said = off[0] if len(off) == 1 else ", ".join(off[:-1]) + " and " + off[-1] if off else ""
        return " But no speaker is on, so it waits" + (f" ({said} switched off)" if said else "") + ": say which to turn on and it plays."

    # ── nobody listening, no stream (the Captain, 2026-11-05: "If no devices are listening, the stream should pause.") ──
    async def listen_tick(self) -> None:
        """Once a second: the speakers from the switches and from what is there; the stream held while nobody hears it, carried on when someone does
        (within RESUME_S, and only if nobody gave a command since); the car's book left to the car."""
        self.apply()
        now, box = self.mono(), self.box
        heard = self.listening()
        if box.playing and self.held:
            self.held = None                                       # something started it again: no longer held
        if box.playing:
            if heard or self.hold_s <= 0:
                self._lost = None
            elif self._lost is None:
                self._lost = (now, box.pos())                      # where the sound stopped reaching anyone
            if self.hold_s > 0 and not heard and not self.outputs.pending(self.hold_s, now):
                await self.hold("nobody", self._lost[1] if self._lost else None)
        elif self.held:
            fresh = self.held["epoch"] == box.epoch and now - self.held["mono"] <= self.resume_s
            if not fresh:
                self.held = None                                   # a command came, or too long ago: an ordinary pause now
            elif self.held["kind"] == "nobody" and heard and box.queue:
                self.log("(radio: a speaker is back: the stream carries on)")
                self.held = None
                await box.resume()
        await self.car_tick()

    async def hold(self, kind: str, rewind_to: float | None = None) -> None:
        box = self.box
        await box.pause()                                          # the radio's own pause: his "stop" counter (RadioClient.pauses) is the hub's and untouched
        if rewind_to is not None:
            await box.rewind(rewind_to)
        self._lost = None
        self.held = {"kind": kind, "since": self.wall(), "mono": self.mono(), "epoch": box.epoch}
        self.log(f"(radio: held — {HOLD_WORDS.get(kind, kind)}{'; the place is where the sound was lost' if rewind_to is not None else ''})")

    async def car_tick(self) -> None:
        """A book the car is playing (the portal's Drive page saves its place every few seconds): the house's copy lets go without writing its place over the car's."""
        engine, box = self.player.books, self.box
        if engine is None or engine.book is None or not box.playing or box.kind() != "book":
            return
        if not engine.store.car_playing(engine.book["id"], self.wall()):
            return
        await engine.hand_over()
        box.offset, box.playing, box.epoch = engine.offset, False, box.epoch + 1
        self.held = {"kind": "car", "since": self.wall(), "mono": self.mono(), "epoch": box.epoch}
        self.log("(radio: the car is playing the book: the house lets go; 'play my book' carries on from the car's place)")

    # ── Skippy's voice ducks the whole stream, on every device ──
    def duck_stream(self, holder: str, on: bool, hold_s: float = 30.0) -> None:
        """Skippy speaks somewhere: all of the stream goes down on every speaker (he is the only listener). Several voices at once are holders with their own
        expiry, so one finishing never brings the stream back while another still talks; the last one out, or the last to expire (which is what saves a hub
        that died mid-sentence), restores it."""
        r = self.player.router
        old = self._duck_holders.pop(holder, None)
        if old is not None:
            old.cancel()
        if on:
            if not self._duck_holders:
                self.log(f"(radio: ducked under Skippy: {holder})")
            self._duck_holders[holder] = asyncio.get_running_loop().call_later(hold_s, lambda: self.duck_stream(holder, False))
            r.duck_kinds(set(r.sinks), True)
        elif not self._duck_holders:
            r.duck_kinds(set(r.sinks), False)

    def duck_kinds(self, kinds, on: bool, hold_s: float = 30.0) -> None:
        """The old per-room call (a hub that has not been restarted yet): it ducks the whole stream now, as a holder of its own."""
        self.duck_stream("kinds:" + ",".join(sorted(kinds)), on, hold_s)

    # ── what it remembers across its own restarts ──
    def snapshot(self) -> dict:
        b = self.box
        return {"queue": list(b.queue), "idx": b.idx, "pos": b.pos(), "playing": bool(b.playing), "speakers": sorted(self.speakers),
                "level": self.level, "spotify_titles": {q: list(t) for q, t in b.spotify_titles.items() if q in set(b.queue)},
                "held": ({"kind": self.held["kind"], "since": self.held["since"]} if self.held else None)}

    async def restore(self, saved: dict, log=print) -> bool:
        """Back where it was: the volume, the speakers (their switches are on disk; the first start after they arrived keeps where it played), and (if it was
        playing) the same item at the same place: a song at its second, a book at the store's place."""
        if saved.get("level"):
            self.set_level(float(saved["level"]))
        self.apply()
        self.box.spotify_titles.update({k: tuple(v) for k, v in (saved.get("spotify_titles") or {}).items()})
        q = [t for t in saved.get("queue") or [] if isinstance(t, str)]
        if not q:
            return False
        idx = min(max(0, int(saved.get("idx") or 0)), len(q) - 1)
        if saved.get("playing"):
            await self.box.play_queue(q, idx, float(saved.get("pos") or 0.0))
            log(f"(radio: resumed where it was: item {idx + 1} of {len(q)} at {float(saved.get('pos') or 0):.0f} s)")
            return True
        self.box.queue, self.box.idx, self.box.offset, self.box._queue_sent = q, idx, float(saved.get("pos") or 0.0), False
        try:
            self.box.titles = await self.media.catalog()          # "what's playing" names it, not its file
        except Exception:                                         # noqa: BLE001 — titles are a nicety
            pass
        engine = self.player.books
        if engine is not None and audiobooks.is_item(q[idx]):     # a paused book is the loaded one again (its place is the store's)
            try:
                await asyncio.to_thread(engine.library.refresh)
                engine.load(audiobooks.item_id(q[idx]))
            except (KeyError, OSError):
                pass
        held = saved.get("held")
        if isinstance(held, dict) and held.get("kind") in HOLD_WORDS:   # it was waiting for a speaker when the radio restarted: still is, for as long as it was
            age = max(0.0, self.wall() - float(held.get("since") or 0))
            self.held = {"kind": held["kind"], "since": float(held.get("since") or self.wall()), "mono": self.mono() - age, "epoch": self.box.epoch}
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
        kind = b.kind()
        return {"playing": bool(b.playing), "track": "" if kind == "book" else b.track(), "item": b.track(), "kind": kind, "title": title, "artist": artist,
                "pos": b.pos(), "idx": b.idx, "queue_len": len(b.queue), "speakers": sorted(h.speakers), "open": sorted(open_), "closed": sorted(closed),
                "where": station.where_said(h), "level": h.level, "dj": dj.enabled(self.env), "book": audiobooks.book_view(h.player.books),
                "outputs": h.outputs_state(), "listening": sorted(h.listening()), "held": h.held_public(),
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
            self.house.place(kinds)                               # "Play on" a box: that room's speakers, as switches
        await self.house.box.take(state)
        self.save()

    async def book_find(self, query: str) -> dict:
        """The audiobook shelf for the hub's plain spoken commands (book_voice.pre_route): the one book that clearly matches, and all that match."""
        engine = self.house.player.books
        if engine is None:
            return {"best": None, "many": []}
        await asyncio.to_thread(engine.library.refresh)
        best, many = engine.library.find(query)
        brief = lambda b: {"id": b["id"], "title": b["title"], "author": b["author"]}      # noqa: E731
        return {"best": brief(best) if best else None, "many": [brief(b) for b in many[:8]]}

    async def book_command(self, action: str, room: str = "") -> bool:
        """One of the portal's `book:` commands, for the station itself or forwarded by a pod (then that room's speakers play it)."""
        kinds = station.here_kinds(self.env, room) if room else None
        if kinds and action.startswith("book:play:"):
            self.house.place(kinds)
        ok = await self.house.box.book_command(action)
        self.save()
        return ok

    async def keep_listening(self, every: float = LISTEN_S) -> None:
        """Forever: the speakers' switches, who hears the stream, the car's book (HouseRadio.listen_tick)."""
        while True:
            try:
                await self.house.listen_tick()
            except Exception as e:                                # noqa: BLE001 — the music plays on
                self.log(f"(radio: listen tick failed: {type(e).__name__}: {e})")
            await asyncio.sleep(every)

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
            holder = str(body.get("holder") or "")[:60] or ("kinds:" + ",".join(sorted(str(k) for k in body.get("kinds") or [])))   # a hub not restarted yet names kinds
            self.house.duck_stream(holder, bool(body.get("on")), float(body.get("hold_s") or 30.0))
            return web.json_response({"ok": True})

        async def take(request):
            body = await request.json()
            await self.take(body.get("state") or {}, str(body.get("room") or ""))
            return web.json_response({"ok": True, "state": self.now()})

        async def book_find(request):
            body = await request.json()
            return web.json_response(await self.book_find(str(body.get("query") or "")))

        async def book_command(request):
            body = await request.json()
            ok = await self.book_command(str(body.get("action") or ""), str(body.get("room") or ""))
            return web.json_response({"ok": ok, "state": self.now()}, status=200 if ok else 400)

        app = web.Application()
        app.router.add_get("/state", state)
        app.router.add_get("/health", state)
        app.router.add_post("/tool", tool)
        app.router.add_post("/control", control)
        app.router.add_post("/shuffle", shuffle)
        app.router.add_post("/duck", duck)
        app.router.add_post("/take", take)
        app.router.add_post("/book/find", book_find)
        app.router.add_post("/book/command", book_command)
        return app

    def live_app(self):
        from aiohttp import web                                   # noqa: PLC0415

        async def live(request):
            if request.match_info["secret"] != self.secret:
                raise web.HTTPNotFound()
            tap = TAPS.get("tap://pods")
            if tap is None:
                raise web.HTTPServiceUnavailable()
            return await self.stream(request, tap, re.sub(r"[^a-z0-9_-]", "", request.query.get("room", "").lower())[:24])   # which room's box this is
        app = web.Application()
        app.router.add_get("/live/{secret}.flac", live)
        return app

    async def stream(self, request, tap: Tap, room: str = ""):
        """One pod tuned in: the tap's blocks, through ffmpeg, as the endless 16 kHz mono FLAC a pod plays (media.py's format). `room`: whose box it is."""
        from aiohttp import web                                   # noqa: PLC0415
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-nostdin", "-f", "f32le", "-ar", str(router_mod.RATE), "-ac", "2", "-i", "pipe:0",
            "-ac", "1", "-ar", "16000", "-f", "flac", "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        q = tap.listen(label=room)
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


def build(env: dict, log=print, state: Path = STATE) -> tuple[Radio, object]:
    """The station on its own router, its own media server, its Spotify, his favorites, its audiobook engine and its speakers' switches."""
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
    try:
        saved = json.loads((Path(state) / "station.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    stereos, pods, floors = outputs_mod.layout_floors(env)
    stereos = [k for k in stereos if k in r.sinks] + [k for k in r.sinks if k != PODS and k not in stereos]    # the speakers this router really has
    switches = outputs_mod.Outputs(stereos, pods, floors, path=Path(state) / "outputs.json", log=log, seed=saved.get("speakers"))   # a first start keeps where it plays now
    house = HouseRadio(r, kinds, media, outputs=switches, log=log, spotify=spot, env=env,
                       hold_s=float(env.get("RADIO_HOLD_S", HOLD_S)), resume_s=float(env.get("RADIO_RESUME_S", RESUME_S)))

    house.attach_books(lambda out, on_end, on_halt: audiobooks.build_engine(env, out, log=log, on_end=on_end, on_halt=on_halt))
    token = env.get("SKIPPY_HOME_TOKEN", "")
    favs = music_mod.Favorites(env.get("PORTAL_URL") or music_mod.PORTAL, token) if token else None
    return Radio(env, house, favorites=favs, state=state, log=log), media


async def _main(env: dict) -> int:
    from aiohttp import web                                       # noqa: PLC0415
    import music as music_mod                                     # noqa: PLC0415
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)   # noqa: E731
    radio, media = build(env, log)
    try:
        radio.state.mkdir(parents=True, exist_ok=True)
        (radio.state / "radio.pid").write_text(str(os.getpid()), encoding="utf-8")        # speaker_out.Ducker leaves this process alone: the radio ducks its own stream
    except OSError:
        pass
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
        connect.add("stereo", env.get("SKIPPY_STEREO_NAME", "House stereo"), radio.house.box, book=radio.house.player.books)
    try:
        await asyncio.gather(radio.keep_saving(), radio.keep_listening(), *([connect.run()] if connect is not None else []))
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
