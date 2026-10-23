"""Bilby's music in the rooms: each ESPHome box is a speaker Bilby's Audio Service can send music to, like the speaker windows on skippy-pc.

* BoxMusic is one box's queue: it plays a track by handing the box a URL from the hub's media server (media.py), knows where it is in it,
  pauses (stops, remembering the place), resumes there, moves on when a track ends, and says what it is doing in Connect's words.
* Connect checks every box in with the portal as a Connect device (POST /home/music/checkin with the home token, as the Captain), so every
  Bilby player lists it under "Play on". The portal hands back the commands waiting for it: play, pause, next, prev, and take:<device>
  ("pick up what that phone is playing"), exactly what a speaker window gets (portal/player.py).
* search() finds tracks in the library for "Skippy, play some Pink Floyd".

The music itself never passes through the portal: the box fetches it from skippy-pc over the home network.
"""
from __future__ import annotations

import asyncio
import re
import time

PORTAL = "https://skippy-portal-1015050363686.us-west1.run.app"
SETTLE_S = 2.5                       # after starting a track the box reports idle for a moment: that is not "the track ended"
MAX_QUEUE = 2000


def device_id(room: str) -> str:
    return f"skippyroom-{room}"      # 8 to 40 of [A-Za-z0-9_-], what the portal accepts for a Connect device


def room_key(room: str) -> str:
    return f"box-{room}"


class BoxMusic:
    """`player` is the box: async play(url), stop(), volume(step). `media` makes track URLs and knows the titles."""

    def __init__(self, player, media, towards: str = "", clock=time.monotonic, log=print, name: str = ""):
        self.player, self.media, self.towards, self.clock, self.log, self.name = player, media, towards, clock, log, name
        self.queue: list[str] = []
        self.idx, self.offset, self.started, self.playing = 0, 0.0, 0.0, False
        self.titles: dict = {}
        self._settle_until = 0.0
        self._queue_sent = False

    # ── where it is ──
    def pos(self) -> float:
        return round(self.offset + (self.clock() - self.started if self.playing else 0.0), 1)

    def track(self) -> str:
        return self.queue[self.idx] if 0 <= self.idx < len(self.queue) else ""

    def title(self) -> tuple[str, str]:
        t = self.titles.get(self.track()) or ()
        name = t[0] if t else re.sub(r"\.\w+$", "", self.track().rsplit("/", 1)[-1])
        return name, (t[1] if len(t) > 1 else "")

    def state(self) -> dict:
        """What the box is doing, as a Connect player says it (portal/player.py _state). The queue goes only when it changed."""
        title, artist = self.title()
        out = {"track": self.track(), "title": title, "artist": artist, "pos": self.pos(), "playing": self.playing, "idx": self.idx,
               "armed": True, "tap": False}
        if not self._queue_sent:
            out["queue"], self._queue_sent = list(self.queue), True
        return out

    # ── what it is told ──
    async def take(self, state: dict) -> None:
        """Pick up what another player is doing: its queue, its place in it, its position."""
        queue = [q for q in (state.get("queue") or []) if isinstance(q, str)][:MAX_QUEUE] or ([state["track"]] if state.get("track") else [])
        if not queue:
            return
        idx = state.get("idx") if isinstance(state.get("idx"), int) and 0 <= state["idx"] < len(queue) else 0
        if state.get("track") and queue[idx] != state["track"] and state["track"] in queue:
            idx = queue.index(state["track"])
        await self.play_queue(queue, idx, float(state.get("pos") or 0))

    async def play_queue(self, queue: list[str], idx: int = 0, pos: float = 0.0) -> None:
        self.queue, self._queue_sent = list(queue)[:MAX_QUEUE], False
        try:
            self.titles = await self.media.catalog()
        except Exception:                                    # noqa: BLE001 — titles are a nicety
            pass
        await self._start(idx, pos)

    async def _start(self, idx: int, pos: float = 0.0) -> None:
        self.idx, self.offset = idx, max(0.0, pos)
        self._settle_until = self.clock() + SETTLE_S
        await self.player.play(self.media.track_url(self.track(), self.offset, self.towards))
        self.playing, self.started = True, self.clock()
        title, artist = self.title()
        self.log(f"[{self.name}] playing {title}" + (f" by {artist}" if artist else ""))
        show = getattr(self.player, "show", None)
        if show:
            await show(f"{title}" + (f" — {artist}" if artist else ""))

    async def pause(self) -> None:
        if self.playing:
            self.offset, self.playing = self.pos(), False
            self._settle_until = self.clock() + SETTLE_S
            await self.player.stop()

    async def resume(self) -> None:
        if self.queue and not self.playing:
            await self._start(self.idx, self.offset)

    async def next(self) -> None:
        if self.idx + 1 < len(self.queue):
            await self._start(self.idx + 1)
        else:
            await self.pause()
            self.offset = 0.0

    async def prev(self) -> None:
        if not self.queue:
            return
        await self._start(self.idx if self.pos() > 3 else max(0, self.idx - 1))

    async def volume(self, step: float) -> None:
        await self.player.volume(step)

    async def on_idle(self) -> None:
        """The box says it stopped playing. Ours (a pause or a track change) is expected; otherwise the track ended: on to the next."""
        if not self.playing or self.clock() < self._settle_until:
            return
        await self.next()

    async def command(self, action: str, devices: list[dict]) -> None:
        if action == "play":
            await self.resume()
        elif action == "pause":
            await self.pause()
        elif action == "next":
            await self.next()
        elif action == "prev":
            await self.prev()
        elif action.startswith("take:"):
            src = next((d for d in devices if d.get("id") == action[5:]), None)
            if src and src.get("state"):
                await self.take(src["state"])


def search(catalog: dict, query: str, limit: int = 200) -> list[str]:
    """Tracks for a spoken request: every word must be in the title, artist or album. An artist's or album's tracks come in album order."""
    words = [w for w in re.findall(r"[a-z0-9']+", (query or "").lower()) if w not in ("the", "some", "a", "by", "music", "songs", "song", "play", "me")]
    if not words:
        return []
    hits = []
    for path, info in catalog.items():
        title, artist, album = (list(info) + ["", "", ""])[:3]
        hay = f"{title} {artist} {album}".lower()
        if all(re.search(rf"\b{re.escape(w)}", hay) for w in words):
            exact_title = all(re.search(rf"\b{re.escape(w)}", title.lower()) for w in words)
            hits.append((0 if exact_title else 1, artist.lower(), album.lower(), path))
    hits.sort()
    return [h[3] for h in hits[:limit]]


class Connect:
    """Every box with music checks in with the portal as a Connect device: often while it plays (so "Play on" and the remote feel live),
    less often while idle."""

    def __init__(self, portal: str, token: str, post=None, log=print, sleep=asyncio.sleep):
        self.portal, self.token, self.log, self.sleep = portal.rstrip("/"), token, log, sleep
        self.post = post or self._post
        self.boxes: dict[str, tuple[str, BoxMusic]] = {}         # room -> (its name for people, its music)
        self._warned = False

    def _post(self, url: str, body: dict) -> dict:
        import requests                                          # noqa: PLC0415
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
        r.raise_for_status()
        return r.json()

    def add(self, room: str, label: str, music: BoxMusic) -> None:
        self.boxes[room] = (label, music)

    def remove(self, room: str) -> None:
        self.boxes.pop(room, None)

    async def once(self) -> None:
        for room, (label, music) in list(self.boxes.items()):
            body = {"device": device_id(room), "name": label, "room": room_key(room), "state": music.state()}
            try:
                reply = await asyncio.to_thread(self.post, f"{self.portal}/home/music/checkin", body)
                self._warned = False
            except Exception as e:                               # noqa: BLE001 — the portal or the network is away: the box keeps playing
                music._queue_sent = False                         # send the queue again once it is back (a redeployed portal forgot it)
                if not self._warned:
                    self.log(f"(Bilby Connect: the portal is not answering: {type(e).__name__})")
                    self._warned = True
                continue
            for action in (reply or {}).get("commands") or []:
                try:
                    await music.command(str(action), (reply or {}).get("devices") or [])
                except Exception as e:                           # noqa: BLE001 — one bad command must not stop the check-ins
                    self.log(f"[{room}] (music command {action!r} failed: {type(e).__name__}: {e})")

    async def run(self) -> None:
        while True:
            await self.once()
            playing = any(m.playing for _, m in self.boxes.values())
            await self.sleep(2.0 if playing else 4.0)
