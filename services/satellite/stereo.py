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


class RouterPlayer:
    """BoxMusic's `player` (async play(url), stop(), volume(step)) on the router's outputs."""

    def __init__(self, router, kinds: list[str], loop=None, popen=None, log=print, spotify=None, clock=time.monotonic):
        self.router, self.kinds, self.log = router, set(kinds), log
        self.loop = loop
        self.popen = popen
        self.music = None                                          # the BoxMusic driving this player (set by StereoMusic)
        self.play_obj = None
        self.gain = 1.0
        self.spotify, self.clock = spotify, clock                  # spotify.Client, or None (then BoxMusic skips Spotify items here too)
        self.spot_play = None                                      # the router's play of the spotify container's stream
        self.spot_uri = ""                                         # the Spotify track this player is playing (asked for, or picked up)
        self.spot_seen = False                                     # go-librespot has said it is playing spot_uri
        self.spot_asked = 0.0
        if spotify is None:
            self.play_spotify = None                               # BoxMusic looks for it: without one, Spotify items are skipped

    def available(self) -> bool:
        return any(self.router.available(k) for k in self.kinds) or not self.router.sinks

    async def play(self, url: str) -> None:
        import book_out                                             # noqa: PLC0415
        import router as router_mod                                 # noqa: PLC0415
        await self.stop()                                           # from Spotify too: a plain cut
        kw = {"popen": self.popen} if self.popen is not None else {}
        p = self.router.play(router_mod.PipeSource(book_out.pcm_args(url, 0.0), **kw), layer="media", targets=self.kinds, gain=self.gain)
        self.play_obj = p
        loop = self.loop or asyncio.get_running_loop()

        async def watch() -> None:
            await asyncio.to_thread(p.finished.wait)
            if self.play_obj is p and self.music is not None:     # it ended by itself (not a stop or the next track): on to the next
                self.play_obj = None
                await self.music.on_idle()
        loop.create_task(watch())

    async def stop(self) -> None:
        p, self.play_obj = self.play_obj, None
        if p is not None:
            self.router.stop(p)
        await self._stop_spotify()

    # ── Spotify ──
    def _ensure_stream(self) -> None:
        """The container's sound into the router (one stream, kept while Spotify tracks follow each other)."""
        if self.spot_play is not None and not self.spot_play.finished.is_set():
            return
        import router as router_mod                                 # noqa: PLC0415
        import spotify as spotify_mod                               # noqa: PLC0415
        kw = {"popen": self.popen} if self.popen is not None else {}
        self.spot_play = self.router.play(router_mod.PipeSource(spotify_mod.pcm_args(self.spotify.pcm), ahead_s=0.5, **kw),
                                          layer="media", targets=self.kinds, gain=self.gain)

    async def play_spotify(self, uri: str, pos: float = 0.0) -> None:
        p, self.play_obj = self.play_obj, None
        if p is not None:
            self.router.stop(p)                                     # Bilby was playing: a plain cut
        self._ensure_stream()                                       # listening before go-librespot starts, so nothing is lost
        self.spot_uri, self.spot_seen, self.spot_asked = uri, False, self.clock()
        try:
            await asyncio.to_thread(self.spotify.play, uri, int(pos * 1000))
        except Exception as e:                                      # noqa: BLE001 — the watcher moves on from a track that never starts
            self.log(f"(stereo: Spotify would not play {uri}: {e})")

    async def _stop_spotify(self) -> None:
        sp, self.spot_play = self.spot_play, None
        had = bool(self.spot_uri) or sp is not None
        self.spot_uri, self.spot_seen = "", False
        if sp is not None:
            self.router.stop(sp)
        if had and self.spotify is not None:
            try:
                await asyncio.to_thread(self.spotify.pause)
            except Exception:                                       # noqa: BLE001 — the container is away: nothing plays there anyway
                pass

    async def _drained(self, limit_s: float = 3.0) -> None:
        """Wait until what is already in the stream has gone out (the end of a track, before a cut to Bilby)."""
        sp = self.spot_play
        end = self.clock() + limit_s
        quiet = 0
        while sp is not None and self.clock() < end and quiet < 3:
            quiet = quiet + 1 if not getattr(sp.source, "have", 0) else 0
            await asyncio.sleep(0.1)

    async def spotify_tick(self, st: dict) -> float:
        """One look at go-librespot's status (spotify.Client.now()). Returns how long to wait before the next look."""
        if not st or not st.get("logged_in"):
            return 5.0
        uri, playing = st.get("uri") or "", bool(st.get("playing"))
        near_end = bool(st.get("ms")) and (st.get("pos_ms") or 0) >= st["ms"] - 1500
        box = self.music
        if self.spot_uri:
            if uri == self.spot_uri and playing:
                if not self.spot_seen and box is not None:
                    box.offset, box.started, box.playing = (st.get("pos_ms") or 0) / 1000.0, self.clock(), True
                self.spot_seen = True
                if box is not None and not box.playing:
                    box.offset, box.started, box.playing = (st.get("pos_ms") or 0) / 1000.0, self.clock(), True   # resumed from his phone
            if uri and uri != self.spot_uri and playing and self.spot_seen:   # it moved on by itself (an album or playlist played whole, from his
                self._adopt(st)                                             # phone or purple; the hub's own items are single tracks): follow it
            elif self.spot_seen and (st.get("stopped") or not uri or uri != self.spot_uri or (st.get("paused") and near_end)):
                self.spot_uri, self.spot_seen = "", False                   # the track ended: on to the next item, Bilby's or Spotify's
                await self._drained()
                if box is not None:
                    await box.on_idle()
            elif not self.spot_seen and self.clock() - self.spot_asked > 20:
                self.log(f"(stereo: Spotify never started {self.spot_uri}; skipping it)")
                self.spot_uri = ""
                if box is not None:
                    await box.on_idle()
            elif self.spot_seen and st.get("paused") and box is not None and box.playing:
                box.offset, box.playing = box.pos(), False                  # paused from his phone
            return 0.5
        if playing and uri:                                                 # started from elsewhere (his phone): play it on the stereo
            self._adopt(st)
            return 0.5
        if self.spot_play is not None:
            sp, self.spot_play = self.spot_play, None                       # nothing of ours plays: let the router go quiet
            self.router.stop(sp)
        return 1.5

    def _adopt(self, st: dict) -> None:
        p, self.play_obj = self.play_obj, None
        if p is not None:
            self.router.stop(p)
        self._ensure_stream()
        self.spot_uri, self.spot_seen = st["uri"], True
        if self.music is not None:
            self.music.adopt(st["uri"], st.get("title") or "", st.get("artist") or "", (st.get("pos_ms") or 0) / 1000.0)
        self.log(f"[stereo] Spotify: {st.get('title')} by {st.get('artist')} (started from {st.get('origin') or 'Spotify'})")

    async def watch_spotify(self) -> None:
        """Forever: follow go-librespot (a track ending, a play started from his phone)."""
        while True:
            try:
                st = await asyncio.to_thread(self.spotify.now)
                wait = await self.spotify_tick(st)
            except Exception as e:                                          # noqa: BLE001 — keep watching
                self.log(f"(stereo: Spotify watch: {type(e).__name__}: {e})")
                wait = 5.0
            await asyncio.sleep(wait)

    async def volume(self, step: float) -> None:
        pass                                                        # the stereo has its own knob

    def set_gain(self, g: float) -> None:
        self.gain = g
        for p in (self.play_obj, self.spot_play):
            if p is not None:
                p.gain = g


class StereoMusic:
    """Shuffle, stop, and duck under Skippy (the room calls duck() when he is addressed, as it does for a book)."""

    def __init__(self, router, kinds: list[str], media, log=print, clock=None, popen=None, spotify=None, env=None):
        import music as music_mod                                   # noqa: PLC0415
        pkw = {"popen": popen, "log": log, "spotify": spotify, **({"clock": clock} if clock is not None else {})}
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
        self.media, self.log = media, log
        self._undo: asyncio.TimerHandle | None = None

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

    async def play_tracks(self, tracks: list[str], mixed: bool = False, limit: int = 500) -> int:
        """Bilby's tracks on the stereo. `mixed`: in a random order, or in DJ mode a DJ's order (dj.walk: each next track mixes with the last)."""
        tracks = list(tracks)
        if mixed:
            random.shuffle(tracks)
            if self.env is not None:
                import dj                                           # noqa: PLC0415
                if dj.enabled(self.env):
                    tracks = await asyncio.to_thread(dj.order, tracks, self.player.lib, limit)
        await self.box.play_queue(tracks[:limit])
        return len(tracks[:limit])

    async def stop(self) -> None:
        await self.box.pause()

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

    def duck(self, on: bool, hold_s: float = 30.0) -> None:
        if self._undo is not None:
            self._undo.cancel()
            self._undo = None
        self.player.set_gain(DUCK if on else 1.0)
        if on:
            try:
                self._undo = asyncio.get_running_loop().call_later(hold_s, self.player.set_gain, 1.0)
            except RuntimeError:
                pass
