"""Bilby's library on the house stereo (the Captain, 2026-10-31): the routines shuffle his music on skippy-pc's own outputs through the audio router
(router.py), on the `media` layer, so Skippy's voice ducks it instead of stopping it. Today that is the downstairs Sony over HDMI; an upstairs
stereo is one more output name in SKIPPY_STEREO_OUT ("hdmi", "bt", or "hdmi+bt").

music.BoxMusic does the queue and the titles as it does for a pod; this module is only the "player" it drives: a track is the hub's own
media-server URL (a FLAC transcode of the music gate's file), decoded by one ffmpeg into the router. When a track ends the router says so and
BoxMusic moves on to the next.

Spotify (2026-10-31, spotify.py): the same queue may hold Spotify URIs. One stream from the spotify container goes into the router while Spotify
tracks follow each other (the player tells go-librespot which one; its status says when it ended), and it is cut plainly where Bilby takes over
(no crossfade on their audio). A Spotify play started elsewhere (his phone's app picking the "Skippy" speaker) is picked up the same way and
becomes this player's one track.
"""
from __future__ import annotations

import asyncio
import random
import time

DUCK = 0.25


IDLE_LOOK_S = 20.0                   # no Spotify row of ours playing or next: how often the speaker is looked at
RESTING_LOOK_S = 60.0                # while Spotify rests after a refusal


def blend_rule(env: dict | None) -> tuple[int, int] | None:
    """BILBY_BLEND="3:2" (the default): in a shuffle on the stereo, two of his Spotify liked songs after every three of Bilby's. "off" for none."""
    raw = str((env or {}).get("BILBY_BLEND", "3:2")).strip().lower()
    try:
        every, take = (int(x) for x in raw.split(":"))
    except ValueError:
        return None
    return (every, take) if every > 0 and take > 0 else None


def blend(bilby: list[str], spotify: list[str], every: int = 3, take: int = 2) -> list[str]:
    """Bilby's tracks with `take` Spotify items after every `every` of them, while Spotify items last."""
    out, si = [], 0
    for i, t in enumerate(bilby):
        out.append(t)
        if (i + 1) % every == 0 and si < len(spotify):
            out += spotify[si:si + take]
            si += take
    return out


class StationBookOut:
    """The audiobook engine's output (audiobooks.BookPlayer.pc_outs): its file, from a second, at its speed, as one play on the router's `media` layer, on
    the station's speakers and at the station's gain. So Skippy's voice ducks a book as it ducks a song, the volume is the station's, a switched-off
    speaker stays silent, and the pods hear it through the same live tap."""

    kind = "station"
    gain = 1.0                                                     # the engine's own ducking lands here and is ignored: the station's gain rules

    def __init__(self, player: "RouterPlayer"):
        self.player = player
        self.play = None

    def available(self):
        return self.player.available() or None

    def start(self, source: str, start_s: float, speed: float = 1.0) -> bool:
        import book_out                                             # noqa: PLC0415
        import router as router_mod                                 # noqa: PLC0415
        self.stop()
        kw = {"popen": self.player.popen} if self.player.popen is not None else {}
        self.play = self.player.router.play(router_mod.PipeSource(book_out.pcm_args(source, start_s, speed), **kw), layer="media",
                                            targets=set(self.player.kinds), gain=self.player.gain)
        return True

    def stop(self) -> None:
        p, self.play = self.play, None
        if p is not None:
            self.player.router.stop(p)


class RouterPlayer:
    """BoxMusic's `player` (async play(url), stop(), volume(step)) on the router's outputs."""

    def __init__(self, router, kinds: list[str], loop=None, popen=None, log=print, spotify=None, clock=time.monotonic, xfade_s: float = 6.0):
        self.router, self.kinds, self.log = router, set(kinds), log
        self.loop = loop
        self.popen = popen
        self.music = None                                          # the BoxMusic driving this player (set by StereoMusic)
        self.play_obj = None
        self.books = None                                          # audiobooks.BookPlayer: the station's audiobook engine; a book is the queue item "book:<id>" (set by radio.py)
        self.book_out = StationBookOut(self)
        self.gain = 1.0
        self.spotify, self.clock = spotify, clock                  # spotify.Client, or None (then BoxMusic skips Spotify items here too)
        self.spot_play = None                                      # the router's play of the Spotify sound (the cable, or go-librespot's stream)
        self.spot_uri = ""                                         # the Spotify track this player is playing (asked for, or picked up)
        self.spot_seen = False                                     # the speaker has said it is playing spot_uri
        self.spot_ours = False                                     # we asked for it (else his phone did): a change of track means the run ended
        self.spot_run: list[str] = []                              # the Spotify tracks handed to the speaker as one list (spot_uri first)
        self.spot_asked = 0.0
        self.spot_last = (0, 0)                                    # (pos_ms, ms) of spot_uri, last seen
        self.xfade_s = xfade_s                                     # Spotify -> Bilby: Spotify fades out over its last seconds as Bilby fades in
        self._handing = False                                      # the next play() is that crossfade (the Spotify sound fades, not cut)
        self.cable_retry = 0.0                                     # when to try the cable again after it would not open (0: it is not failing)
        if spotify is None:
            self.play_spotify = None                               # BoxMusic looks for it: without one, Spotify items are skipped

    def available(self) -> bool:
        return any(self.router.available(k) for k in self.kinds) or not self.router.sinks

    async def play(self, url: str) -> None:
        import book_out                                             # noqa: PLC0415
        import router as router_mod                                 # noqa: PLC0415
        fading = self._handing
        await self.stop()                                           # from Spotify too: a plain cut, or its crossfade when one is under way
        kw = {"popen": self.popen} if self.popen is not None else {}
        p = self.router.play(router_mod.PipeSource(book_out.pcm_args(url, 0.0), **kw), layer="media", targets=self.kinds,
                             gain=0.0 if fading else self.gain)
        self.play_obj = p
        loop = self.loop or asyncio.get_running_loop()
        if fading:
            self.fade_in(p)

        async def watch() -> None:
            await asyncio.to_thread(p.finished.wait)
            if self.play_obj is p and self.music is not None:     # it ended by itself (not a stop or the next track): on to the next
                self.play_obj = None
                await self.music.on_idle()
        loop.create_task(watch())

    async def stop(self) -> None:
        """The one stop: a song, Spotify and a book all end here (a book keeps its place: BookPlayer.pause remembers it)."""
        await self._release()
        b = self.books
        if b is not None and b.playing:
            await b.pause()

    async def _release(self) -> None:
        """End what is not the book: a song's play and Spotify."""
        p, self.play_obj = self.play_obj, None
        if p is not None:
            self.router.stop(p)
        await self._stop_spotify()

    # ── an audiobook as the queue's item "book:<id>" (audiobooks.py is the engine: place, chapters, speed, sleep timer) ──
    async def play_book(self, bid: str, pos: float = 0.0) -> None:
        """The queue came to the book `bid`: where it was left (the store's place; the car or the browser may have moved it), or resumed if it is the loaded one.
        Raises KeyError / FileNotFoundError when the book or its file is gone: BoxMusic goes on to the next item."""
        b = self.books
        if b is None:
            raise LookupError("no audiobook engine here")
        await self._release()
        await asyncio.to_thread(b.library.refresh)                  # cached for minutes: the first call after a start scans the shelf
        loaded = b.book is not None and b.book["id"] == bid
        if loaded and b.playing:
            return
        if loaded:
            await b.resume()
            return
        if b.playing:
            await b.pause()                                         # another book was playing: its place is kept
        await b.play(bid)

    def book_pos(self) -> float | None:
        b = self.books
        return None if b is None or b.book is None else b.pos()

    async def book_chapter(self, step: int) -> bool:
        """Next (+1) or previous (-1) chapter of the playing book. False when there is none (the last chapter): the queue goes on."""
        b = self.books
        if b is None or b.book is None:
            return False
        return (await b.next_chapter() if step > 0 else await b.previous_chapter()) is not None

    async def book_seek(self, pos: float) -> None:
        if self.books is not None and self.books.book is not None:
            await self.books.seek(pos)

    # ── crossfades: router gains, nothing else is done to the sound ──
    def _ramp(self, p, up: bool, seconds: float, then=None) -> None:
        """Move one play's gain from silent to self.gain (up) or back down, equal-power, over `seconds`; `then()` after a fade out."""
        async def go() -> None:
            t0 = self.clock()
            while True:
                k = min(1.0, (self.clock() - t0) / max(seconds, 1e-3))
                g = (k if up else 1.0 - k) ** 0.5
                if p.finished.is_set():
                    return
                p.gain = self.gain * g
                if k >= 1.0:
                    break
                await asyncio.sleep(0.05)
            if then is not None:
                await then()
        (self.loop or asyncio.get_running_loop()).create_task(go())

    def fade_in(self, p, seconds: float | None = None) -> None:
        """A play started at gain 0 (the incoming side of a crossfade) comes up to full (DjPlayer's chain too)."""
        self._handing = False
        self._ramp(p, True, self.xfade_s if seconds is None else seconds)

    # ── Spotify ──
    def _ensure_stream(self) -> None:
        """The Spotify sound into the router (one stream, kept while Spotify tracks follow each other)."""
        if self.spot_play is not None and not self.spot_play.finished.is_set():
            return
        import router as router_mod                                 # noqa: PLC0415
        import spotify as spotify_mod                               # noqa: PLC0415
        pcm = self.spotify.pcm
        if pcm.startswith("capture:"):                              # the Spotify app on the virtual cable
            try:
                src = spotify_mod.CaptureSource(pcm.split(":", 1)[1])
            except Exception as e:                                  # noqa: BLE001 — said in the log; spotify_tick tries again every few seconds
                if not self.cable_retry:
                    self.log(f"(stereo: Spotify's cable would not open, trying again: {type(e).__name__}: {e})")
                self.cable_retry = self.clock() + 3.0
                return
            if self.cable_retry:
                self.log("(stereo: Spotify's cable is open)")
            self.cable_retry = 0.0
        else:
            kw = {"popen": self.popen} if self.popen is not None else {}
            src = router_mod.PipeSource(spotify_mod.pcm_args(pcm), ahead_s=0.5, **kw)
        self.spot_play = self.router.play(src, layer="media", targets=self.kinds, gain=self.gain)

    def _run_from(self, uri: str) -> list[str]:
        """What the speaker is handed: one track. (Spotify's desktop app starts nothing for a list of tracks, 2026-10-31, so each Spotify item is
        played by itself and the next one is started just before this one ends.)"""
        return [uri]

    def _next_is_spotify(self) -> bool:
        import spotify as spotify_mod                               # noqa: PLC0415
        m = self.music
        return bool(m is not None and m.playing and m.idx + 1 < len(m.queue) and spotify_mod.is_uri(m.queue[m.idx + 1]))

    async def play_spotify(self, uri: str, pos: float = 0.0, fade_s: float = 0.0) -> None:
        """Play a Spotify track (and the Spotify run after it). From Bilby: a plain cut, or with `fade_s` Bilby fades out as Spotify comes in
        (handoff_to_spotify)."""
        if self.books is not None and self.books.playing:
            await self.books.pause()                                # a book never plays under Spotify: its place is kept
        p, self.play_obj = self.play_obj, None
        if p is not None:
            if fade_s > 0:
                self._ramp(p, False, fade_s, then=lambda: self._router_stop(p))
            else:
                self.router.stop(p)                                 # Bilby was playing: a plain cut
        self._ensure_stream()                                       # listening before the speaker starts, so nothing is lost
        self.spot_run = self._run_from(uri)
        self.spot_uri, self.spot_seen, self.spot_ours, self.spot_asked, self.spot_last = uri, False, True, self.clock(), (0, 0)
        try:
            await asyncio.to_thread(self.spotify.play, uri, int(pos * 1000), self.spot_run[1:])
        except Exception as e:                                      # noqa: BLE001 — a refusal (a 429, no device): the caller plays Bilby at once
            self.log(f"(stereo: Spotify would not play {uri}: {e})")
            return False
        return True

    async def handoff_to_spotify(self, fade_s: float = 0.0) -> None:
        """The next queue item is Spotify's and the playing Bilby track is near its end (DjPlayer calls this at its phrase point): move the queue
        on and start Spotify while Bilby fades out over `fade_s` (0: a plain cut)."""
        m = self.music
        if m is None or m.idx + 1 >= len(m.queue):
            return
        m.idx, m.offset, m.started, m.playing = m.idx + 1, 0.0, m.clock(), True
        m._queue_sent = False
        await self.play_spotify(m.track(), 0.0, fade_s)

    async def _router_stop(self, p) -> None:
        self.router.stop(p)

    async def _stop_spotify(self) -> None:
        sp, self.spot_play = self.spot_play, None
        had = bool(self.spot_uri) or sp is not None
        self.spot_uri, self.spot_seen, self.spot_ours, self.spot_run = "", False, False, []
        if sp is not None:
            if self._handing:                                       # the crossfade to Bilby: fade it out, then pause the speaker
                self._ramp(sp, False, self.xfade_s, then=lambda: self._pause_after(sp))
                return
            self.router.stop(sp)
        if had and self.spotify is not None:
            await self._pause_speaker()

    async def _pause_after(self, sp) -> None:
        self.router.stop(sp)
        if self.spot_play is None:                                  # Spotify didn't start again meanwhile
            await self._pause_speaker()

    async def _pause_speaker(self) -> None:
        try:
            await asyncio.to_thread(self.spotify.pause)
        except Exception:                                           # noqa: BLE001 — the container is away: nothing plays there anyway
            pass

    async def _drained(self, limit_s: float = 3.0) -> None:
        """Wait until what is already in the stream has gone out (the end of a track, before a cut to Bilby). The cable has nothing to drain."""
        sp = self.spot_play
        if sp is None or getattr(sp.source, "live", False):
            return
        end = self.clock() + limit_s
        quiet = 0
        while self.clock() < end and quiet < 3:
            quiet = quiet + 1 if not getattr(sp.source, "have", 0) else 0
            await asyncio.sleep(0.1)

    def _next_is_bilby(self) -> bool:
        import spotify as spotify_mod                               # noqa: PLC0415
        m = self.music
        return bool(m is not None and m.playing and m.idx + 1 < len(m.queue) and not spotify_mod.is_uri(m.queue[m.idx + 1]))

    async def spotify_tick(self, st: dict) -> float:
        """One look at the speaker's status (spotify.Client.now()). Returns how long to wait before the next look."""
        if not st or not st.get("logged_in"):
            return 5.0
        uri, playing = st.get("uri") or "", bool(st.get("playing"))
        pos, ms = st.get("pos_ms") or 0, st.get("ms") or 0
        near_end = bool(ms) and pos >= ms - 1500
        box = self.music
        if self.spot_uri:
            if st.get("elsewhere") and self.spot_seen:              # he moved it to another speaker (his phone): let it go, don't pull it back
                self.log(f"(stereo: Spotify moved to {st['elsewhere']})")
                sp, self.spot_play = self.spot_play, None
                if sp is not None:
                    self.router.stop(sp)
                self.spot_uri, self.spot_seen, self.spot_ours = "", False, False
                if box is not None:
                    box.offset, box.playing = box.pos(), False
                return 1.5
            if uri == self.spot_uri and playing:
                if self.cable_retry and self.clock() >= self.cable_retry:  # it would not open when this track started (Windows' sound was busy)
                    self._ensure_stream()
                if not self.spot_seen:
                    self._road(uri)
                    if box is not None:
                        box.offset, box.started, box.playing = pos / 1000.0, self.clock(), True
                self.spot_seen = True
                self.spot_last = (pos, ms)
                if box is not None and not box.playing:
                    box.offset, box.started, box.playing = pos / 1000.0, self.clock(), True   # resumed from his phone
                if (self.spot_ours and self.xfade_s > 0 and ms and pos >= ms - self.xfade_s * 1000 and self.spot_run[-1:] == [uri]
                        and self._next_is_bilby() and box is not None):
                    self._handing = True                            # the run's last Spotify track is ending into Bilby: crossfade them
                    self.spot_uri = ""
                    try:
                        await box.next()
                        if self._handing and self.play_obj is not None:  # a player that didn't fade its start in itself (DjPlayer's chain)
                            self.play_obj.gain = 0.0
                            self.fade_in(self.play_obj)
                    finally:
                        self._handing = False
                    return 0.5
                if self.spot_ours and ms and pos >= ms - 1500 and self._next_is_spotify() and box is not None:
                    self.spot_uri = ""                              # the next Spotify item, started before the app's album goes on by itself
                    await box.next()
                    return 0.5
            moved = bool(uri) and uri != self.spot_uri and self.spot_seen
            if moved and self.spot_ours and uri in self.spot_run and box is not None and uri in box.queue:
                self.spot_uri, self.spot_last = uri, (pos, ms)       # the speaker went on to the run's next track by itself: the queue follows
                box.idx, box.offset, box.started, box.playing, box._queue_sent = box.queue.index(uri, box.idx), pos / 1000.0, self.clock(), True, False
                self._road(uri)
                title, artist = box.title()
                self.log(f"[{box.name}] playing {title}" + (f" by {artist}" if artist else "") + " (Spotify)")
            elif moved and playing and not self.spot_ours:          # his phone's album or playlist went on by itself: follow it
                self._adopt(st)
            elif self.spot_seen and (st.get("stopped") or not uri or moved or (st.get("paused") and near_end)
                                     or (st.get("paused") and pos < 1500 and self.spot_last[1] and self.spot_last[0] >= self.spot_last[1] - 6000)):
                self.spot_uri, self.spot_seen = "", False           # the track (the run) ended: on to the next item, Bilby's or Spotify's
                await self._drained()
                if box is not None:
                    if self.spot_run and box.track() in self.spot_run:
                        box.idx = box.queue.index(self.spot_run[-1], box.idx)   # past the whole run, whatever the speaker reported last
                    await box.on_idle()
            elif not self.spot_seen and self.clock() - self.spot_asked > 20:
                self.log(f"(stereo: Spotify never started {self.spot_uri}; skipping it)")
                self.spot_uri = ""
                if box is not None:
                    await box.on_idle()
            elif self.spot_seen and st.get("paused") and box is not None and box.playing:
                box.offset, box.playing = box.pos(), False          # paused from his phone
            return 0.5
        if playing and uri:                                         # started from elsewhere (his phone): play it on the stereo
            self._adopt(st)
            return 0.5
        if self.spot_play is not None and not self._handing:
            sp, self.spot_play = self.spot_play, None               # nothing of ours plays: let the router go quiet
            self.router.stop(sp)
        return 1.5

    def _adopt(self, st: dict) -> None:
        p, self.play_obj = self.play_obj, None
        if p is not None:
            self.router.stop(p)
        if self.books is not None and self.books.playing:
            (self.loop or asyncio.get_running_loop()).create_task(self.books.pause())
        self._ensure_stream()
        self.spot_uri, self.spot_seen, self.spot_ours, self.spot_run = st["uri"], True, False, [st["uri"]]
        self._road(st["uri"])
        if self.music is not None:
            self.music.adopt(st["uri"], st.get("title") or "", st.get("artist") or "", (st.get("pos_ms") or 0) / 1000.0)
        self.log(f"[stereo] Spotify: {st.get('title')} by {st.get('artist')} (started from {st.get('origin') or 'his phone'})")

    def _road(self, uri: str) -> None:
        """A Spotify track that started playing goes into his "Skippy Road" playlist, once (his phone keeps it downloaded for the drive)."""
        def add() -> None:
            try:
                self.spotify.road(uri)
            except Exception as e:                                          # noqa: BLE001 — the music plays either way
                self.log(f"(stereo: Skippy Road: {type(e).__name__}: {e})")
        try:
            asyncio.get_running_loop().run_in_executor(None, add)
        except RuntimeError:
            add()

    async def watch_spotify(self) -> None:
        """Forever: follow the Spotify speaker (a track ending, a play started from his phone). Each look is a Web API call, so it looks
        closely only while a Spotify row of ours plays or is next, else every IDLE_LOOK_S (a play from his phone is picked up that late),
        and every RESTING_LOOK_S while Spotify rests after a refusal (2026-11-01: a 429 at the DJ's hand-over, from a hub that kept asking)."""
        while True:
            try:
                st = await asyncio.to_thread(self.spotify.now)
                wait = await self.spotify_tick(st)
            except Exception as e:                                          # noqa: BLE001 — keep watching
                self.log(f"(stereo: Spotify watch: {type(e).__name__}: {e})")
                wait = 5.0
            await asyncio.sleep(self.look_after(wait))

    def look_after(self, wait: float) -> float:
        if getattr(self, "spotify_down", lambda: False)():
            return max(wait, RESTING_LOOK_S)
        if not self.spot_uri and not self._next_is_spotify():
            return max(wait, IDLE_LOOK_S)
        return wait

    async def volume(self, step: float) -> None:
        pass                                                        # the stereo has its own knob

    def set_gain(self, g: float) -> None:
        self.gain = g
        for p in (self.play_obj, self.spot_play, self.book_out.play):
            if p is not None:
                p.gain = g


class StereoMusic:
    """Shuffle, stop, and duck under Skippy (the room calls duck() when he is addressed, as it does for a book)."""

    def __init__(self, router, kinds: list[str], media, log=print, clock=None, popen=None, spotify=None, env=None):
        import music as music_mod                                   # noqa: PLC0415
        pkw = {"popen": popen, "log": log, "spotify": spotify, **({"clock": clock} if clock is not None else {}),
               **({"xfade_s": float(env.get("SPOTIFY_XFADE_S", "6"))} if env is not None else {})}   # Spotify -> Bilby (0: plain cuts)
        if env is not None:                                         # the hub: Bilby's tracks as one gapless chain, mixed like a DJ in DJ mode (dj.py)
            import dj                                               # noqa: PLC0415
            self.player = dj.DjPlayer(router, kinds, env=env, **pkw)
        else:
            self.player = RouterPlayer(router, kinds, **pkw)
        self.env = env
        kw = {"clock": clock} if clock is not None else {}
        self.box = music_mod.BoxMusic(self.player, media, log=log, name="stereo", **kw)
        # BoxMusic's settle window is for a pod that reports "idle" while a track starts; the router never does, so no settle is needed
        self.player.music = self.box
        if env is not None:                                         # Bilby's page shows and switches DJ mode (the same switch as "DJ mode on")
            import dj                                               # noqa: PLC0415
            xfade = float(env.get("SPOTIFY_XFADE_S", "6"))
            self.box.mix = {"get": lambda: {"on": dj.enabled(env), "xfade": xfade}, "set": dj.set_enabled}
        self.media, self.log = media, log
        self._undo: asyncio.TimerHandle | None = None
        self.level = 1.0                                            # his "turn it down" (hub.stereo_volume): the ducking below is on top of it

    def set_level(self, level: float) -> None:
        self.level = max(0.05, min(1.0, level))
        if self._undo is None:                                      # ducked under Skippy right now: it comes back to the new level
            self.player.set_gain(self.level)

    @property
    def playing(self) -> bool:
        return bool(self.box.playing)

    async def shuffle(self, limit: int = 500) -> int:
        """The whole library in a random order (the first `limit` tracks of it). -> how many are queued (0: the gate is not answering)."""
        catalog = await self.media.catalog()
        tracks = list(catalog)
        if not tracks:
            self.log("(stereo: the music gate's catalog is empty or not answering)")
            return 0
        return await self.play_tracks(tracks, True, limit)

    async def play_tracks(self, tracks: list[str], mixed: bool = False, limit: int = 500, premixed: bool = False) -> int:
        """Bilby's tracks on the stereo. `mixed`: in a random order, or in DJ mode a DJ's order (dj.walk: each next track mixes with the last).
        `premixed`: the queue is a mix already (mixes.resolve: its Spotify rows and the favorite every fourth placed), so it is neither shuffled
        nor blended again; in DJ mode only Bilby's own tracks are put in a DJ's order, each Spotify row keeping its place."""
        tracks = list(tracks)
        if mixed and premixed:
            if self.env is not None:
                import dj                                           # noqa: PLC0415
                import spotify                                      # noqa: PLC0415
                if dj.enabled(self.env):
                    mine = [t for t in tracks[:limit] if not spotify.is_uri(t)]
                    walked = iter(await asyncio.to_thread(dj.order, mine, self.player.lib, limit))
                    tracks = [t if spotify.is_uri(t) else next(walked, None) for t in tracks[:limit]]
                    tracks = [t for t in tracks if t]
        elif mixed:
            random.shuffle(tracks)
            if self.env is not None:
                import dj                                           # noqa: PLC0415
                if dj.enabled(self.env):
                    tracks = await asyncio.to_thread(dj.order, tracks, self.player.lib, limit)
            tracks = await self._blend(tracks[:limit])
        await self.box.play_queue(tracks[:limit * 2])
        return len(tracks[:limit * 2])

    async def _blend(self, tracks: list[str]) -> list[str]:
        """A shuffle with his Spotify mixed in (blend_rule). Spotify not answering: Bilby alone. A Spotify item that won't play when its turn comes
        is skipped by the player (dj.DjPlayer), so Bilby fills in."""
        rule = blend_rule(self.env) if self.env is not None else None
        spot = getattr(self.player, "spotify", None)
        if rule is None or spot is None or getattr(self.player, "spotify_down", lambda: False)():
            return tracks
        try:
            liked = await asyncio.to_thread(spot.liked, 200)
        except Exception as e:                                      # noqa: BLE001 — Bilby alone
            self.log(f"(stereo: no Spotify songs for the blend: {type(e).__name__}: {e})")
            return tracks
        random.shuffle(liked)
        return blend(tracks, self.box.add_spotify(liked), *rule)

    async def stop(self) -> None:
        await self.box.pause()

    def move(self, kinds) -> None:
        """Play on these router outputs from now on, what plays now included (a place on command: speaker_out.place_kinds)."""
        old, new = set(self.player.kinds), set(kinds)
        if old != new:
            self.player.kinds = new
            self.player.router.retarget(old, new)

    def where(self) -> tuple[set, set]:
        """(the outputs it plays on that are open, the ones it should play on that aren't): what Skippy may say, and what he must not claim."""
        kinds = set(self.player.kinds)
        open_ = {k for k in kinds if self.player.router.available(k)}
        return open_, kinds - open_

    async def play_spotify(self, tracks: list[dict], queue: bool = False) -> int:
        """Spotify tracks (spotify.Client's dicts) on the stereo: now, or after what plays ("queue"). -> how many."""
        items = self.box.add_spotify(tracks)
        if items:
            await (self.box.append(items) if queue else self.box.play_queue(items))
        return len(items)

    def start(self) -> None:
        """Follow the Spotify speaker (when there is one), on the running loop."""
        if self.player.spotify is not None:
            asyncio.get_running_loop().create_task(self.player.watch_spotify())

    def _unduck(self) -> None:
        self._undo = None
        self.player.set_gain(self.level)

    def duck(self, on: bool, hold_s: float = 30.0) -> None:
        if self._undo is not None:
            self._undo.cancel()
            self._undo = None
        self.player.set_gain((DUCK if on else 1.0) * self.level)
        if on:
            try:
                self._undo = asyncio.get_running_loop().call_later(hold_s, self._unduck)
            except RuntimeError:
                pass
