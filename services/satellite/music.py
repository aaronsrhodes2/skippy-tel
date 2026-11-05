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

import audiobooks

PORTAL ="https://skippy-portal-1015050363686.us-west1.run.app"
SETTLE_S = 2.5                       # after starting a track the box reports idle for a moment: that is not "the track ended"
MAX_QUEUE = 2000
POOL_S = 60.0                        # his favorites pool (and tired, played) is asked for again after this


def device_id(room: str) -> str:
    return f"skippyroom-{room}"      # 8 to 40 of [A-Za-z0-9_-], what the portal accepts for a Connect device


def room_key(room: str) -> str:
    return f"box-{room}"


class Favorites:
    """The Captain's Bilby favorites, kept on the portal (portal/favorites.py): mark what he is listening to, and fetch the list to play."""

    def __init__(self, portal: str, token: str, request=None):
        self.portal, self.token = portal.rstrip("/"), token
        self.request = request or self._request
        self._pool: tuple[float, tuple] | None = None

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        import requests                                          # noqa: PLC0415
        r = requests.request(method, self.portal + path, json=body, headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}

    def mark(self, room: str, device: str = "") -> str:
        """Mark the song he is listening to now (this room's own box first, or `device`: the house stereo when that is what plays). Returns what
        to say. A Spotify track is kept by its URI (portal/favorites.py writes it into the .m3u as a comment line)."""
        self._pool = None
        code, got = self.request("POST", "/home/music/favorite", {"device": device or device_id(room), "source": "voice"})
        if code == 409:
            return "Nothing is playing, so there is nothing to add to his favorites."
        if code != 200:
            return f"The favorites list did not answer ({code})."
        f = got.get("favorite") or {}
        again = " It was already there; it is back at the top." if int(f.get("count") or 1) > 1 else ""
        return f"Added {f.get('title') or 'the song'}" + (f" by {f['artist']}" if f.get("artist") else "") + " to his favorites." + again

    def tire(self, room: str, device: str = "") -> str:
        """He is tired of the song he is listening to: out of his favorites pool and out of every mix (portal/favorites.py tire). Returns
        what to say."""
        self._pool = None
        code, got = self.request("POST", "/home/music/tired", {"device": device or device_id(room), "source": "voice"})
        if code == 409:
            return "Nothing is playing, so there is nothing to drop."
        if code != 200:
            return f"The favorites list did not answer ({code})."
        t = got.get("tired") or {}
        name = (t.get("title") or "the song") + (f" by {t['artist']}" if t.get("artist") else "")
        return f"Dropped {name}: out of his favorites and out of every mix from now on."

    def pool(self) -> tuple[list[str], set[str], set[str]]:
        """(his favorites, newest first; the tracks he is tired of; every track he has heard) in one call, kept POOL_S (a slow portal never
        holds up "bilby"); the last good answer when the portal doesn't answer."""
        now = time.monotonic()
        if self._pool and now - self._pool[0] < POOL_S:
            return self._pool[1]
        try:
            code, got = self.request("GET", "/home/music/favorites")
        except Exception:                                        # noqa: BLE001 — the portal is away: the last pool, else none
            code, got = 0, {}
        if code != 200:
            return self._pool[1] if self._pool else ([], set(), set())
        out = ([f["track"] for f in (got.get("favorites") or []) if isinstance(f, dict) and f.get("track")],
               {t for t in (got.get("tired") or []) if isinstance(t, str)}, {t for t in (got.get("played") or []) if isinstance(t, str)})
        self._pool = (now, out)
        return out

    def tracks(self) -> list[str]:
        return self.pool()[0]


class BoxMusic:
    """`player` is the box: async play(url), stop(), volume(step). `media` makes track URLs and knows the titles."""

    def __init__(self, player, media, towards: str = "", clock=time.monotonic, log=print, name: str = ""):
        self.player, self.media, self.towards, self.clock, self.log, self.name = player, media, towards, clock, log, name
        self.queue: list[str] = []
        self.idx, self.offset, self.started, self.playing = 0, 0.0, 0.0, False
        self.titles: dict = {}
        self.spotify_titles: dict = {}                       # spotify:track:... -> (title, artist): a queue may mix them with Bilby's paths (spotify.py)
        self.book_titles: dict = {}                          # book:<id> -> (title, author): an audiobook is an item of the queue too (audiobooks.py)
        self._settle_until = 0.0
        self._queue_sent = False
        self.epoch = 0                                       # counts the transport commands (start, pause, resume): the radio's hold carries on only if none came since
        self.switch = None                                   # the house stereo sets (action) -> bool: Connect's "out:<speaker>:on|off" (radio.HouseRadio)
        self.extra = None                                    # the house stereo sets () -> dict: more for Connect's state (its speakers, the hold)
        self.mix = None                                     # the house stereo sets {"get": () -> {"on", "xfade"}, "set": (bool) -> None}: DJ mode, for the Bilby page

    # ── where it is ──
    def pos(self) -> float:
        if self.playing and audiobooks.is_item(self.track()):
            at = getattr(self.player, "book_pos", lambda: None)()        # a book's clock is the engine's: book seconds, at its speed
            if at is not None:
                return round(at, 1)
        return round(self.offset + (self.clock() - self.started if self.playing else 0.0), 1)

    def track(self) -> str:
        return self.queue[self.idx] if 0 <= self.idx < len(self.queue) else ""

    def kind(self) -> str:
        """What the current item is: a song of Bilby's, a Spotify track, or an audiobook."""
        import spotify                                       # noqa: PLC0415
        tr = self.track()
        return "book" if audiobooks.is_item(tr) else "spotify" if spotify.is_uri(tr) else "song"

    def _book_meta(self, item: str) -> tuple[str, str] | None:
        """(title, author) of a book item: what it told us when it started, else the shelf (the engine's library)."""
        if item in self.book_titles:
            return self.book_titles[item]
        engine = getattr(self.player, "books", None)
        book = engine.library.get(audiobooks.item_id(item)) if engine is not None else None
        return (book["title"], book["author"]) if book else None

    def title(self) -> tuple[str, str]:
        tr = self.track()
        t = self.titles.get(tr) or self.spotify_titles.get(tr) or (self._book_meta(tr) if audiobooks.is_item(tr) else None) or ()
        name = t[0] if t else re.sub(r"\.\w+$", "", tr.rsplit("/", 1)[-1])
        return name, (t[1] if len(t) > 1 else "")

    def state(self) -> dict:
        """What the box is doing, as a Connect player says it (portal/player.py _state). The queue goes only when it changed."""
        title, artist = self.title()
        kind = self.kind()
        # a book is never a `track`: the car's hand-over (portal/drive.py) reads the newest track as "Bilby", and book:<id> would be played as a song
        out = {"track": "" if kind == "book" else self.track(), "title": title, "artist": artist, "pos": self.pos(), "playing": self.playing, "idx": self.idx,
               "armed": True, "tap": False, "kind": kind}
        if self.mix:
            out["mix"] = self.mix["get"]()
        if self.extra is not None:
            try:
                out.update(self.extra())
            except Exception:                                # noqa: BLE001 — the track is what matters; the speakers' state is a nicety
                pass
        if not self._queue_sent:
            out["queue"], self._queue_sent = list(self.queue), True
            names = {q: list(self.spotify_titles[q]) for q in self.queue if q in self.spotify_titles}   # names for the queue's Spotify items: the page has no catalog for them
            names.update({q: list(m) for q in self.queue if audiobooks.is_item(q) and (m := self._book_meta(q))})
            if names:
                out["titles"] = names
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
        if not queue or not all(audiobooks.is_item(q) for q in queue):    # a book needs no catalog of songs
            try:
                self.titles = await self.media.catalog()
            except Exception:                                # noqa: BLE001 — titles are a nicety
                pass
        self.queue, self._queue_sent = self.local(list(queue)[:MAX_QUEUE]), False
        await self._start(idx, pos)

    def local(self, items: list[str]) -> list[str]:
        """Local first (the Captain, 2026-11-01: "prefer bilby use local library versions to spotify versions of songs"): a Spotify item
        that is a song in his library (same artist and title, folded like mixes._n: case, accents, "(remastered)", a leading "the") is
        played from Bilby's file; Spotify only for songs he doesn't have. No Spotify call: its name came with it (add_spotify)."""
        import mixes                                         # noqa: PLC0415
        import spotify                                       # noqa: PLC0415
        if not self.titles or not any(spotify.is_uri(i) and i in self.spotify_titles for i in items):
            return items
        if getattr(self, "_local_of", (None,))[0] is not self.titles:
            have: dict = {}
            for path, info in self.titles.items():
                t = list(info) + ["", ""]
                have.setdefault((mixes._n(t[1]), mixes._n(mixes.feat_off(t[0]))), path)
            self._local_of = (self.titles, have)
        have = self._local_of[1]
        out = []
        for i in items:
            name = self.spotify_titles.get(i) if spotify.is_uri(i) else None
            hit = have.get((mixes._n((name[1] or "").split(", ")[0]), mixes._n(mixes.feat_off(name[0])))) if name else None
            out.append(hit or i)
        return out

    def add_spotify(self, tracks: list[dict]) -> list[str]:
        """Spotify tracks (spotify.Client's {uri, title, artist}) as queue items: their URIs, their names kept for title()."""
        for t in tracks:
            if t.get("uri"):
                self.spotify_titles[t["uri"]] = (t.get("title") or "", t.get("artist") or "")
        return [t["uri"] for t in tracks if t.get("uri")]

    async def append(self, items: list[str]) -> None:
        """More after what is queued ("queue ... after this"); starts playing them when nothing is."""
        if not self.queue or not self.playing:
            await self.play_queue(items)
            return
        self.queue = (self.queue + self.local(list(items)))[:MAX_QUEUE]
        self._queue_sent = False

    def adopt(self, uri: str, title: str, artist: str, pos: float = 0.0) -> None:
        """Something else started this player (his phone's Spotify app, on the "Skippy" speaker): show it as its one track, so Connect, "favorite
        this" and "next" know what plays."""
        self.spotify_titles[uri] = (title, artist)
        self.queue, self.idx, self._queue_sent = [uri], 0, False
        self.offset, self.started, self.playing = max(0.0, pos), self.clock(), True

    async def _start(self, idx: int, pos: float = 0.0) -> None:
        import spotify                                       # noqa: PLC0415
        self.epoch += 1
        play_spotify = getattr(self.player, "play_spotify", None)
        play_book = getattr(self.player, "play_book", None)
        while 0 <= idx < len(self.queue) and ((spotify.is_uri(self.queue[idx]) and play_spotify is None)
                                              or (audiobooks.is_item(self.queue[idx]) and (play_book is None or getattr(self.player, "books", None) is None))):
            idx, pos = idx + 1, 0.0                          # a pod's own speaker can't play Spotify or a book (only the PC's outputs can): skip it
        if not 0 <= idx < len(self.queue):
            self.idx, self.offset, self.playing = max(0, len(self.queue) - 1), 0.0, False
            return
        self.idx, self.offset = idx, max(0.0, pos)
        self._settle_until = self.clock() + SETTLE_S
        if audiobooks.is_item(self.track()):
            try:
                await play_book(audiobooks.item_id(self.track()), self.offset)
            except (KeyError, LookupError, OSError) as e:    # the book or its file is gone: the next item at once, never silence
                self.log(f"[{self.name}] (the audiobook {self.track()!r} would not play: {type(e).__name__}: {e})")
                self.playing, self.started = True, self.clock()
                asyncio.get_running_loop().create_task(self.advance())
                return
            engine = self.player.books
            if engine.book is not None:
                self.book_titles[self.track()] = (engine.book["title"], engine.book["author"])
                self._queue_sent = False                     # the page learns the book's name
        elif spotify.is_uri(self.track()):
            if await play_spotify(self.track(), self.offset) is False:   # refused (a 429, no device): his next song at once, never silence
                asyncio.get_running_loop().create_task(self.next())
        else:
            await self.player.play(self.media.track_url(self.track(), self.offset, self.towards))
        self.playing, self.started = True, self.clock()
        title, artist = self.title()
        self.log(f"[{self.name}] playing {title}" + (f" by {artist}" if artist else ""))
        show = getattr(self.player, "show", None)
        if show:
            await show(f"{title}" + (f" — {artist}" if artist else ""))

    async def pause(self) -> None:
        self.epoch += 1
        if self.playing:
            self.offset, self.playing = self.pos(), False
            self._settle_until = self.clock() + SETTLE_S
            await self.player.stop()

    async def resume(self) -> None:
        self.epoch += 1
        if self.queue and not self.playing:
            await self._start(self.idx, self.offset)

    async def next(self) -> None:
        """Skip: the next song, or, in a book, the next chapter (the last chapter goes on to the next item)."""
        if audiobooks.is_item(self.track()) and self.playing:
            move = getattr(self.player, "book_chapter", None)
            if move is not None and await move(+1):
                return
        await self.advance()

    async def advance(self) -> None:
        """On to the next item of the queue (a track ended, a book ended, or a skip)."""
        if self.idx + 1 < len(self.queue):
            await self._start(self.idx + 1)
        else:
            await self.pause()
            self.offset = 0.0

    async def prev(self) -> None:
        if not self.queue:
            return
        if audiobooks.is_item(self.track()) and self.playing:
            move = getattr(self.player, "book_chapter", None)
            if move is not None and await move(-1):
                return
        await self._start(self.idx if self.pos() > 3 else max(0, self.idx - 1))

    async def rewind(self, pos: float) -> None:
        """Back to a place (the sound was lost there: nobody was listening): the item's offset, and a book's own place."""
        pos = max(0.0, float(pos))
        if audiobooks.is_item(self.track()):
            seek = getattr(self.player, "book_seek", None)
            if seek is not None:
                await seek(pos)
        self.offset = pos

    # ── a book as the queue's item ──
    def book_transport(self) -> "BookTransport":
        return BookTransport(self)

    async def book_command(self, action: str) -> bool:
        """The portal's `book:` commands (the Audiobooks app's "Play on" this station): the engine, with this queue's transport."""
        engine = getattr(self.player, "books", None)
        if engine is None:
            return False
        return await audiobooks.run_command(engine, action, transport=self.book_transport())

    async def volume(self, step: float) -> None:
        await self.player.volume(step)

    async def on_idle(self) -> None:
        """The box says it stopped playing. Ours (a pause or a track change) is expected; otherwise the track ended: on to the next."""
        if not self.playing or self.clock() < self._settle_until:
            return
        await self.advance()

    async def command(self, action: str, devices: list[dict]) -> None:
        if action == "play":
            await self.resume()
        elif action == "pause":
            await self.pause()
        elif action == "next":
            await self.next()
        elif action == "prev":
            await self.prev()
        elif action in ("mix:on", "mix:off") and self.mix:
            self.mix["set"](action == "mix:on")
        elif action.startswith("out:") and self.switch is not None:
            self.switch(action)                              # a speaker's switch (the portal's toggles): the house stereo's own
        elif action.startswith("take:"):
            src = next((d for d in devices if d.get("id") == action[5:]), None)
            if src and src.get("state"):
                await self.take(src["state"])


class BookTransport:
    """The audiobook tool's and the portal's start / pause / resume, as the station's transport (audiobooks.run_tool / run_command): the book is the
    queue's item, so "pause" is the station's one pause and a start replaces what was queued (each keeps its own place: a song's offset, a book's store)."""

    def __init__(self, box: BoxMusic):
        self.box = box

    @property
    def engine(self):
        return self.box.player.books

    async def play(self, bid: str) -> dict:
        box = self.box
        if box.track() == audiobooks.item(bid) and box.playing:
            return self.engine.where()
        await box.play_queue([audiobooks.item(bid)])
        return self.engine.where()

    async def pause(self) -> None:
        await self.box.pause()

    async def resume(self) -> dict:
        box = self.box
        if audiobooks.is_item(box.track()) and box.queue:
            if not box.playing:
                await box.resume()
        elif self.engine.book is not None:                   # music is the current item: "read my book" goes back to the loaded one
            await self.play(self.engine.book["id"])
        else:
            raise LookupError("no book chosen")
        return self.engine.where()


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
        self.books: dict[str, object] = {}                       # room -> its audiobooks.BookPlayer (the portal's Audiobooks app plays on a box through Connect)
        self._warned = False

    def _post(self, url: str, body: dict) -> dict:
        import requests                                          # noqa: PLC0415
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
        r.raise_for_status()
        return r.json()

    def add(self, room: str, label: str, music: BoxMusic, book=None) -> None:
        self.boxes[room] = (label, music)
        if book is not None:
            self.books[room] = book

    def remove(self, room: str) -> None:
        self.boxes.pop(room, None)
        self.books.pop(room, None)

    async def once(self) -> None:
        for room, (label, music) in list(self.boxes.items()):
            book = self.books.get(room)
            state = music.state()
            if book is not None:
                state["book"] = audiobooks.book_state(book)
            body = {"device": device_id(room), "name": label, "room": room_key(room), "state": state}
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
                    if str(action).startswith("book:"):
                        own = getattr(music, "book_command", None)           # the station (BoxMusic with an engine) and a pod's tuner run it themselves
                        ok = False
                        if own is not None and (getattr(getattr(music, "player", None), "books", None) is not None or not hasattr(music, "player")):
                            ok = await own(str(action))
                        elif book is not None:
                            ok = await audiobooks.run_command(book, str(action))
                        if not ok:
                            self.log(f"[{room}] (book command refused)")
                        continue
                    await music.command(str(action), (reply or {}).get("devices") or [])
                except Exception as e:                           # noqa: BLE001 — one bad command must not stop the check-ins
                    self.log(f"[{room}] (music command {action!r} failed: {type(e).__name__}: {e})")

    async def run(self) -> None:
        while True:
            await self.once()
            playing = any(m.playing for _, m in self.boxes.values())
            await self.sleep(2.0 if playing else 4.0)
