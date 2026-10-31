"""Bilby's library on the house stereo (the Captain, 2026-10-31): the routines shuffle his music on skippy-pc's own outputs through the audio router
(router.py), on the `media` layer, so Skippy's voice ducks it instead of stopping it. Today that is the downstairs Sony over HDMI; an upstairs
stereo is one more output name in SKIPPY_STEREO_OUT ("hdmi", "bt", or "hdmi+bt").

music.BoxMusic does the queue and the titles as it does for a pod; this module is only the "player" it drives: a track is the hub's own
media-server URL (a FLAC transcode of the music gate's file), decoded by one ffmpeg into the router. When a track ends the router says so and
BoxMusic moves on to the next.
"""
from __future__ import annotations

import asyncio
import random

DUCK = 0.25


class RouterPlayer:
    """BoxMusic's `player` (async play(url), stop(), volume(step)) on the router's outputs."""

    def __init__(self, router, kinds: list[str], loop=None, popen=None, log=print):
        self.router, self.kinds, self.log = router, set(kinds), log
        self.loop = loop
        self.popen = popen
        self.music = None                                          # the BoxMusic driving this player (set by StereoMusic)
        self.play_obj = None
        self.gain = 1.0

    def available(self) -> bool:
        return any(self.router.available(k) for k in self.kinds) or not self.router.sinks

    async def play(self, url: str) -> None:
        import book_out                                             # noqa: PLC0415
        import router as router_mod                                 # noqa: PLC0415
        await self.stop()
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

    async def volume(self, step: float) -> None:
        pass                                                        # the stereo has its own knob

    def set_gain(self, g: float) -> None:
        self.gain = g
        if self.play_obj is not None:
            self.play_obj.gain = g


class StereoMusic:
    """Shuffle, stop, and duck under Skippy (the room calls duck() when he is addressed, as it does for a book)."""

    def __init__(self, router, kinds: list[str], media, log=print, clock=None, popen=None):
        import music as music_mod                                   # noqa: PLC0415
        self.player = RouterPlayer(router, kinds, popen=popen, log=log)
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
        random.shuffle(tracks)
        await self.box.play_queue(tracks[:limit])
        return len(tracks[:limit])

    async def stop(self) -> None:
        await self.box.pause()

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
