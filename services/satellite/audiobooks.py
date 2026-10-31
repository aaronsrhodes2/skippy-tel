"""The Captain's audiobooks, read by voice: "Skippy, read my book".

Audible's own files are DRM-protected and this does not touch that: it plays DRM-free files he puts in a library folder himself
(SATELLITE_AUDIOBOOKS, default D:\\Aaron\\audiobooks; m4b/m4a/mp3/ogg/opus/flac/aac). One folder or file per book; chapters come from the file's
chapter markers (ffprobe), else one chapter per file of a track-per-chapter folder. A .aax/.aaxc/.aa file is noticed and skipped, never opened.

* Library      scans the folder (ffprobe, cached by file size and time), finds a book by loose title or author ("the dragon one").
* Store        per-book state in state/audiobooks.json: place, speed, last played, finished; written atomically, at most a few seconds old.
* BookPlayer   one room's book: a clock that knows where in the book it is, and the outputs it plays on: the pod's speaker (a URL from the hub's
               media server, like Bilby's tracks) and skippy-pc's own outputs (book_out.py: HDMI, the Bluetooth stereo), started with the same
               delays as Skippy's voice. Pause, resume (a few seconds back after a long pause), chapters, +-30 s, speed, a sleep timer, ducking.
* book_tools   the `audiobook` tool for Skippy's brain.

The place is kept in book seconds, so it survives a restart however the book is cut into files.
"""
from __future__ import annotations

import asyncio
import bisect
import difflib
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

AUDIO_EXT = {".m4b", ".m4a", ".mp3", ".ogg", ".opus", ".flac", ".aac"}
PROTECTED_EXT = {".aax", ".aaxc", ".aa"}                       # Audible's own: noticed and skipped, never opened
SAVE_EVERY_S = 5.0
REWIND_AFTER_PAUSE_S = 3.0                                     # a pause of half a minute or more picks up a few seconds earlier
REWIND_THRESHOLD_S = 30.0
PART_S = 900                                                   # a file with no chapter markers is cut into quarter-hour parts for skipping
DUCK_GAIN = 0.2
STOP_WORDS = {"the", "a", "an", "my", "book", "books", "one", "read", "play", "audiobook", "audio", "that", "about", "by", "with", "of", "me", "to", "from", "story"}


def default_dir() -> Path:
    return Path(r"D:\Aaron\audiobooks")


def say_time(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, m = divmod(seconds // 60, 60)
    if h:
        return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minute{'s' if m != 1 else ''}" if m else "")
    if m:
        return f"{m} minute{'s' if m != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "book"


# ── reading the files ──
def ffprobe(path: str) -> dict:
    """{duration, tags, chapters:[{title,start,end}]} for one audio file, or {} when ffprobe cannot read it."""
    try:
        raw = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_chapters", path],
                             capture_output=True, timeout=60).stdout
        data = json.loads(raw or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    fmt = data.get("format") or {}
    tags = {k.lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    chapters = []
    for c in data.get("chapters") or []:
        try:
            chapters.append({"title": str((c.get("tags") or {}).get("title") or "").strip(), "start": float(c["start_time"]), "end": float(c["end_time"])})
        except (KeyError, ValueError, TypeError):
            continue
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    return {"duration": duration, "tags": tags, "chapters": chapters}


def _clean_title(stem: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"^\s*(?:\d+\s*[-_.)]?\s*)", "", stem.replace("_", " "))).strip() or stem


def _file_sig(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


class Library:
    def __init__(self, root: Path, cache: Path | None = None, probe=ffprobe, clock=time.time, log=print):
        self.root, self.cache_path, self.probe, self.clock, self.log = Path(root), cache, probe, clock, log
        self.books: dict[str, dict] = {}
        self.skipped_protected = 0
        self.scanned_at = 0.0
        self._cache: dict = {}
        if cache is not None:
            try:
                self._cache = json.loads(Path(cache).read_text(encoding="utf-8")).get("books", {})
            except (OSError, ValueError):
                self._cache = {}

    # ── scanning ──
    def refresh(self, force: bool = False, max_age_s: float = 300.0) -> int:
        """Rescan the folder (blocking: run it in a thread). How many books there are."""
        if not force and self.books and self.clock() - self.scanned_at < max_age_s:
            return len(self.books)
        found, self.skipped_protected = {}, 0
        if self.root.is_dir():
            self._walk(self.root, found)
        self.books, self.scanned_at = found, self.clock()
        if self.cache_path is not None:
            try:
                tmp = Path(str(self.cache_path) + ".tmp")
                tmp.write_text(json.dumps({"version": 1, "books": found}), encoding="utf-8")
                os.replace(tmp, self.cache_path)
            except OSError:
                pass
        return len(found)

    def _walk(self, folder: Path, found: dict) -> None:
        try:
            entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
        except OSError:
            return
        files = [p for p in entries if p.is_file() and p.suffix.lower() in AUDIO_EXT]
        self.skipped_protected += sum(1 for p in entries if p.is_file() and p.suffix.lower() in PROTECTED_EXT)
        if folder == self.root:
            for f in files:                                    # loose files straight in the library: one book each
                self._add([f], f.stem, found)
        elif files:
            self._add(files, folder.name, found)
        for d in entries:
            if d.is_dir():
                self._walk(d, found)

    def _add(self, files: list[Path], name: str, found: dict) -> None:
        rel = files[0].name if (len(files) == 1 and files[0].parent == self.root) else files[0].parent.relative_to(self.root).as_posix()
        bid = slug(rel)
        sig = hashlib.sha1("|".join(f"{f.name}:{_file_sig(f)}" for f in files).encode()).hexdigest()
        cached = self._cache.get(bid)
        if cached and cached.get("sig") == sig and all(Path(f["path"]).exists() for f in cached.get("files", [])):
            found[bid] = cached
            return
        book = self._build(bid, files, name, sig)
        if book:
            found[bid] = book

    def _build(self, bid: str, files: list[Path], name: str, sig: str) -> dict | None:
        infos = [(f, self.probe(str(f))) for f in files]
        infos = [(f, i) for f, i in infos if i and i.get("duration", 0) > 1]
        if not infos:
            return None
        first = infos[0][1].get("tags", {})
        title = first.get("album") or first.get("title") or ""
        author = first.get("album_artist") or first.get("artist") or first.get("author") or first.get("composer") or ""
        if len(infos) == 1:
            title = first.get("title") or first.get("album") or ""
        m = re.match(r"^(.+?)\s+-\s+(.+)$", name)
        if not title:
            title = m.group(2) if m else name
        if not author and m:
            author = m.group(1)
        parent = infos[0][0].parent
        if not author and parent != self.root and parent.parent != self.root:
            author = parent.parent.name                        # Author/Title/files
        flist, chapters, at = [], [], 0.0
        for f, info in infos:
            dur = info["duration"]
            flist.append({"path": str(f), "dur": dur, "book_start": at})
            marks = info.get("chapters") or []
            if len(marks) >= 2:
                for c in marks:
                    chapters.append({"title": c["title"] or f"Chapter {len(chapters) + 1}", "start": at + c["start"], "end": at + min(c["end"], dur)})
            elif len(infos) > 1:
                chapters.append({"title": info["tags"].get("title") or _clean_title(f.stem), "start": at, "end": at + dur})
            else:                                              # one file, no markers: quarter-hour parts so "next chapter" still means something
                n = max(1, round(dur / PART_S))
                for k in range(n):
                    chapters.append({"title": f"Part {k + 1}", "start": dur * k / n, "end": dur * (k + 1) / n})
            at += dur
        return {"id": bid, "title": title.strip(), "author": author.strip(), "folder": name, "files": flist, "chapters": chapters, "duration": at, "sig": sig}

    # ── finding ──
    def get(self, bid: str | None) -> dict | None:
        return self.books.get(bid or "")

    def find(self, query: str) -> tuple[dict | None, list[dict]]:
        """(the one book that clearly matches, else None; the books that match at all, best first). Loose: any word of the title, author or folder,
        a typo or two forgiven ("the dragon one")."""
        words = [w for w in re.findall(r"[a-z0-9']+", (query or "").lower()) if w not in STOP_WORDS]
        if not words:
            return None, []
        scored = []
        for b in self.books.values():
            hay = re.findall(r"[a-z0-9']+", f"{b['title']} {b['author']} {b.get('folder', '')}".lower())
            hit = 0
            for w in words:
                if any(h.startswith(w) or (len(w) >= 4 and w in h) for h in hay) or (len(w) >= 4 and difflib.get_close_matches(w, hay, 1, 0.82)):
                    hit += 1
            if hit:
                scored.append((hit / len(words), b))
        scored.sort(key=lambda x: (-x[0], x[1]["title"].lower()))
        if not scored:
            return None, []
        top = scored[0][0]
        best = [b for s, b in scored if s == top]
        return (best[0] if len(best) == 1 and top >= 0.99 else None), [b for _, b in scored]


# ── what is remembered ──
class Store:
    """state/audiobooks.json: {current, books:{id:{pos, chapter, speed, last_played, finished}}}. Written atomically, at most every few seconds."""

    def __init__(self, path: Path, wall=time.time):
        self.path, self.wall = Path(path), wall
        self.data = {"version": 1, "current": None, "books": {}}
        self._saved = 0.0
        self._dirty = False
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("books"), dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    @property
    def current(self) -> str | None:
        return self.data.get("current")

    def progress(self, bid: str) -> dict:
        return dict(self.data["books"].get(bid) or {"pos": 0.0, "chapter": 0, "speed": 1.0, "last_played": 0, "finished": False})

    def put(self, bid: str, *, current: bool = True, **fields) -> None:
        p = self.progress(bid)
        p.update(fields)
        self.data["books"][bid] = p
        if current:
            self.data["current"] = bid
        self._dirty = True

    def last_unfinished(self, ids) -> str | None:
        ids = [i for i in ids if not self.progress(i).get("finished") and self.progress(i).get("last_played")]
        return max(ids, key=lambda i: self.progress(i)["last_played"]) if ids else None

    def save(self, force: bool = False, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if not self._dirty or (not force and now - self._saved < SAVE_EVERY_S):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(str(self.path) + ".tmp")
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self._saved, self._dirty = now, False
        except OSError:
            pass


# ── playing ──
class BookPlayer:
    """One room's audiobook. `pod` is the box's link (async play(url), stop()) or None; `media` makes the pod's URLs; `pc_outs` are book_out.PcBookOut."""

    def __init__(self, library: Library, store: Store, pod=None, media=None, towards: str = "", pc_outs=(), clock=time.monotonic, wall=time.time,
                 yield_to=None, log=print, name: str = ""):
        self.library, self.store, self.pod, self.media, self.towards = library, store, pod, media, towards
        self.pc_outs, self.clock, self.wall, self.yield_to, self.log, self.name = list(pc_outs), clock, wall, yield_to, log, name
        self.book: dict | None = None
        self.offset, self.started, self.playing, self.speed = 0.0, 0.0, False, 1.0
        self.paused_at = 0.0
        self.sleep_at: float | None = None
        self.sleep_chapter_end: float | None = None            # the book second at which the "end of this chapter" sleep stops it
        self.ducked_until = 0.0
        self._file_idx = 0
        self._ticker: asyncio.Task | None = None

    # ── where it is ──
    def pos(self) -> float:
        if self.book is None:
            return 0.0
        p = self.offset + ((self.clock() - self.started) * self.speed if self.playing else 0.0)
        return round(min(max(0.0, p), self.book["duration"]), 1)

    def chapter_index(self, pos: float | None = None) -> int:
        chs = self.book["chapters"]
        starts = [c["start"] for c in chs]
        return max(0, bisect.bisect_right(starts, self.pos() if pos is None else pos) - 1)

    def where(self) -> dict | None:
        if self.book is None:
            return None
        pos, i = self.pos(), self.chapter_index()
        ch = self.book["chapters"][i]
        return {"title": self.book["title"], "author": self.book["author"], "chapter": i + 1, "chapters": len(self.book["chapters"]),
                "chapter_title": ch["title"], "into_chapter": pos - ch["start"], "left": self.book["duration"] - pos, "pos": pos,
                "playing": self.playing, "speed": self.speed}

    def _file_at(self, pos: float) -> int:
        files = self.book["files"]
        return max(0, bisect.bisect_right([f["book_start"] for f in files], pos) - 1)

    # ── playing ──
    def load(self, bid: str, restart: bool = False) -> dict:
        """Choose a book (not playing yet): its remembered place, or the start when `restart` or when it was finished."""
        book = self.library.get(bid)
        if book is None:
            raise KeyError(bid)
        prog = self.store.progress(bid)
        self.book = book
        self.speed = float(prog.get("speed") or 1.0)
        self.offset = 0.0 if restart or prog.get("finished") else min(float(prog.get("pos") or 0.0), max(0.0, book["duration"] - 1))
        self.playing = False
        return book

    async def play(self, bid: str | None = None, restart: bool = False) -> dict:
        if bid and (self.book is None or self.book["id"] != bid or restart):
            self.load(bid, restart)
        elif self.book is not None and not self.playing and self.store.progress(self.book["id"]).get("finished"):
            self.load(self.book["id"])                         # he finished it: start again
        if self.book is None:
            raise LookupError("no book chosen")
        if self.playing:
            return self.where()
        await self._begin(self.offset)
        return self.where()

    async def _begin(self, pos: float) -> None:
        if self.yield_to is not None:
            await self.yield_to()                              # Bilby's music in this room makes way
        self._file_idx = self._file_at(pos)
        f = self.book["files"][self._file_idx]
        if not Path(f["path"]).exists():
            raise FileNotFoundError(Path(f["path"]).name)
        off = max(0.0, pos - f["book_start"])
        await self._stop_outputs()
        self.offset, self.started, self.playing = pos, self.clock(), True
        if self.pod is not None and self.media is not None:
            await self.pod.play(self.media.book_url(f["path"], off, self.speed, self.towards))
        for out in self.pc_outs:
            await asyncio.to_thread(out.start, f["path"], off, self.speed)
        self._apply_gain()
        self._remember(force=True, finished=False)
        self.log(f"[{self.name}] book: {self.book['title']} from {say_time(pos)}")
        if self._ticker is None or self._ticker.done():
            self._ticker = asyncio.ensure_future(self._run())

    async def _stop_outputs(self) -> None:
        if self.pod is not None:
            try:
                await self.pod.stop()
            except Exception:                                  # noqa: BLE001 — a box that went away: the PC outputs still stop
                pass
        for out in self.pc_outs:
            await asyncio.to_thread(out.stop)

    async def pause(self) -> None:
        if self.book is None or not self.playing:
            return
        self.offset, self.playing, self.paused_at = self.pos(), False, self.clock()
        self.sleep_at = self.sleep_chapter_end = None
        await self._stop_outputs()
        self._remember(force=True)

    async def resume(self) -> dict:
        if self.book is None:
            raise LookupError("no book chosen")
        if not self.playing:
            back = REWIND_AFTER_PAUSE_S if self.paused_at and self.clock() - self.paused_at >= REWIND_THRESHOLD_S else 0.0
            await self._begin(max(0.0, self.offset - back))
        return self.where()

    async def seek(self, pos: float) -> dict:
        pos = min(max(0.0, pos), max(0.0, self.book["duration"] - 1))
        if self.playing:
            await self._begin(pos)
        else:
            self.offset = pos
            self._remember(force=True)
        return self.where()

    async def skip(self, seconds: float) -> dict:
        return await self.seek(self.pos() + seconds)

    async def next_chapter(self) -> dict | None:
        i = self.chapter_index()
        if i + 1 >= len(self.book["chapters"]):
            return None
        return await self.seek(self.book["chapters"][i + 1]["start"])

    async def previous_chapter(self) -> dict:
        i = self.chapter_index()
        ch = self.book["chapters"][i]
        target = ch["start"] if (self.pos() - ch["start"] > 5 or i == 0) else self.book["chapters"][i - 1]["start"]
        return await self.seek(target)

    async def set_speed(self, speed: float) -> float:
        self.speed = round(min(2.0, max(0.5, speed)), 2)
        if self.playing:
            await self._begin(self.pos())
        else:
            self._remember(force=True)
        return self.speed

    def set_sleep(self, minutes: float | None = None, end_of_chapter: bool = False) -> None:
        self.sleep_at = self.clock() + minutes * 60 if minutes else None
        self.sleep_chapter_end = self.book["chapters"][self.chapter_index()]["end"] if end_of_chapter and self.book else None

    # ── ducking: Skippy's voice (or the wake word) lowers the book on the PC outputs; the pod lowers its own media under an announcement ──
    def duck(self, on: bool, hold_s: float = 30.0) -> None:
        self.ducked_until = self.clock() + hold_s if on else 0.0
        self._apply_gain()

    def _apply_gain(self) -> None:
        g = DUCK_GAIN if self.clock() < self.ducked_until else 1.0
        for out in self.pc_outs:
            out.gain = g

    # ── the clock ──
    def _remember(self, force: bool = False, finished: bool | None = None) -> None:
        if self.book is None:
            return
        pos = self.pos()
        fields = {"pos": pos, "chapter": self.chapter_index(pos), "speed": self.speed, "last_played": int(self.wall())}
        if finished is not None:
            fields["finished"] = finished
        self.store.put(self.book["id"], **fields)
        self.store.save(force=force, now=self.clock())

    async def _run(self) -> None:
        try:
            while self.playing:
                await asyncio.sleep(1.0)
                await self.tick()
        except asyncio.CancelledError:
            raise

    async def tick(self) -> None:
        """Once a second while playing: keep the place, move to the next file, honour the sleep timer, let go of the ducking."""
        if not self.playing or self.book is None:
            return
        self._apply_gain()
        pos = self.pos()
        self._remember()
        now = self.clock()
        if self.sleep_at is not None and now >= self.sleep_at:
            self.log(f"[{self.name}] book: sleep timer, stopping")
            await self.pause()
            return
        if self.sleep_chapter_end is not None and pos >= self.sleep_chapter_end - 0.6:
            self.sleep_chapter_end = None
            await self.pause()
            return
        f = self.book["files"][self._file_idx]
        if pos >= f["book_start"] + f["dur"] - 0.3:
            if self._file_idx + 1 < len(self.book["files"]):
                await self._begin(self.book["files"][self._file_idx + 1]["book_start"])
            else:
                self.offset, self.playing = self.book["duration"], False
                await self._stop_outputs()
                self._remember(force=True, finished=True)
                self.log(f"[{self.name}] book: finished {self.book['title']}")

    async def shutdown(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
        await self.pause()


# ── the brain's tool ──
BOOK_TOOLS = [
    {"name": "audiobook",
     "description": ("ALWAYS call this for anything about his audiobook: you cannot see the book or know where he is, so never answer from memory or pretend. "
                     "The Captain's audiobooks (DRM-free files in his library), read aloud by the hub in THIS room, on the pod's speaker and the PC's outputs. "
                     "'read my book' / 'continue' / 'play <a few words of the title or author>' is play; with no title it continues the book he was on. "
                     "While a book is playing, 'stop' and 'pause' mean pause. where is 'where was I' / 'what is this'. sleep is 'stop in 20 minutes' "
                     "(or end_of_chapter). You never read the book yourself and never invent its text: the tool does the reading."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["play", "pause", "resume", "next_chapter", "previous_chapter", "back", "forward", "where", "sleep", "speed", "list"]},
         "title": {"type": "string", "description": "a few words of the title or author, as he said them (play)"},
         "seconds": {"type": "integer", "description": "how far for back / forward; 30 when he did not say"},
         "minutes": {"type": "number", "description": "for sleep: stop in this many minutes; 0 cancels the timer"},
         "end_of_chapter": {"type": "boolean", "description": "for sleep: stop when this chapter ends"},
         "speed": {"type": "number", "description": "for speed: 1.0 is normal, 1.25 a quarter faster (0.5 to 2.0)"}},
         "required": ["action"]}},
]

BOOK_STYLE = "(Done. Reply with one short dry line of your own about it; never read, quote or invent the book.) "


def _titles(books: list[dict], n: int = 4) -> str:
    return "; ".join(f"{b['title']}" + (f" by {b['author']}" if b["author"] else "") for b in books[:n])


async def run_tool(player: BookPlayer, args: dict) -> str:
    """The audiobook tool, on the hub's loop. Returns what the brain should say, as a plain sentence with a hint at the style."""
    lib = player.library
    action = str(args.get("action") or "")
    await asyncio.to_thread(lib.refresh)
    if not lib.books:
        extra = f" ({lib.skipped_protected} Audible-protected file(s) are there, which I cannot play)" if lib.skipped_protected else ""
        return f"There are no audiobooks in his library folder yet{extra}. He puts DRM-free m4b, mp3 or similar files in it, one folder or file per book."
    try:
        if action == "list":
            return "His audiobooks: " + _titles(sorted(lib.books.values(), key=lambda b: b["title"].lower()), 8) + f" ({len(lib.books)} in all)."
        if action == "play":
            title = str(args.get("title") or "").strip()
            if title:
                best, many = lib.find(title)
                if best is None:
                    return (f"Nothing in his audiobooks matches {title!r}." if not many else
                            f"Several could be it: {_titles(many)}. Ask which, in one short line.")
                bid = best["id"]
            else:
                bid = player.book["id"] if player.book else None
                if not bid or player.store.progress(bid).get("finished"):
                    bid = player.store.current if player.store.current in lib.books and not player.store.progress(player.store.current).get("finished") else None
                bid = bid or player.store.last_unfinished(lib.books)
                bid = bid or (next(iter(lib.books)) if len(lib.books) == 1 else None)       # one book on the shelf: that one
                if not bid:
                    return f"He has not started a book yet. His books: {_titles(sorted(lib.books.values(), key=lambda b: b['title'].lower()))}. Ask which one."
            w = await player.play(bid)
            return (f"{BOOK_STYLE}Playing {w['title']}" + (f" by {w['author']}" if w["author"] else "") +
                    f", chapter {w['chapter']} of {w['chapters']}, {w['chapter_title']}, at {say_time(w['pos'])} in.")
        if player.book is None:
            bid = player.store.current if player.store.current in lib.books else None
            if bid is None:
                return "No book is chosen yet; ask which one he wants."
            player.load(bid)
        if action == "pause":
            await player.pause()
            w = player.where()
            return f"{BOOK_STYLE}Paused {w['title']} at chapter {w['chapter']}, {say_time(w['into_chapter'])} in; it keeps his place."
        if action == "resume":
            w = await player.resume()
            return f"{BOOK_STYLE}Continuing {w['title']}, chapter {w['chapter']}, {w['chapter_title']}."
        if action == "next_chapter":
            w = await player.next_chapter()
            return (f"{BOOK_STYLE}Chapter {w['chapter']} of {w['chapters']}: {w['chapter_title']}." if w else "That was the last chapter.")
        if action == "previous_chapter":
            w = await player.previous_chapter()
            return f"{BOOK_STYLE}Chapter {w['chapter']}: {w['chapter_title']}, from its start."
        if action in ("back", "forward"):
            n = int(args.get("seconds") or 30)
            w = await player.skip(-n if action == "back" else n)
            return f"{BOOK_STYLE}Went {action} {say_time(abs(n))}; now chapter {w['chapter']}, {say_time(w['into_chapter'])} in."
        if action == "where":
            w = player.where()
            return (f"{w['title']}" + (f" by {w['author']}" if w["author"] else "") + f": chapter {w['chapter']} of {w['chapters']}, {w['chapter_title']}, "
                    f"{say_time(w['into_chapter'])} in; {say_time(w['left'])} left in the book; {'playing' if w['playing'] else 'paused'}.")
        if action == "sleep":
            minutes = float(args.get("minutes") or 0)
            if args.get("end_of_chapter"):
                player.set_sleep(None, True)
                return f"{BOOK_STYLE}It will stop at the end of this chapter."
            player.set_sleep(minutes if minutes > 0 else None)
            return f"{BOOK_STYLE}" + (f"It will stop in {say_time(minutes * 60)}." if minutes > 0 else "The sleep timer is off.")
        if action == "speed":
            s = await player.set_speed(float(args.get("speed") or 1.0))
            return f"{BOOK_STYLE}Speed is {s:g} times normal."
    except KeyError:
        return "I lost track of that book."
    except FileNotFoundError as e:
        return f"The file {e} is gone from his audiobooks folder."
    except (LookupError, ValueError, TypeError) as e:
        return f"That did not work: {e}"
    return "I cannot do that with a book."


def book_tools(room_of, loop):
    """(tools, run) for Brain(extra=...), like hub.music_tools: the brain runs in a worker thread, the book on the hub's loop."""
    def run(name: str, args: dict) -> str:
        room = room_of()
        player = getattr(room, "book", None)
        if player is None:
            return "This room has no way to play books."
        try:
            out = asyncio.run_coroutine_threadsafe(run_tool(player, args if isinstance(args, dict) else {}), loop).result(60)
        except Exception as e:                                 # noqa: BLE001 — said aloud, not raised
            return f"The book did not answer ({type(e).__name__})."
        if (args or {}).get("action") in ("play", "resume"):
            room.last_media = "book"
        return out
    return BOOK_TOOLS, run


def combine(*pairs):
    """Merge several (tools, run) pairs (None allowed) into one for Brain(extra=...)."""
    pairs = [p for p in pairs if p]
    if not pairs:
        return None
    tools = [t for ts, _ in pairs for t in ts]

    def run(name: str, args: dict) -> str:
        for ts, fn in pairs:
            if name in {t["name"] for t in ts}:
                return fn(name, args)
        return "I have no such tool."
    return tools, run


def build_player(env: dict, room: str, link=None, log=print) -> BookPlayer | None:
    """A room's BookPlayer from the vault settings, or None when the library folder setting is switched off (SATELLITE_AUDIOBOOKS=off)."""
    import book_out                                           # noqa: PLC0415
    raw = (env.get("SATELLITE_AUDIOBOOKS") or "").strip()
    if raw.lower() in ("off", "0", "no"):
        return None
    state = Path(__file__).resolve().parents[2] / "state"
    lib = _libraries.setdefault(raw, Library(Path(raw) if raw else default_dir(), state / "audiobooks-library.json", log=log))
    store = _stores.setdefault(str(state), Store(state / "audiobooks.json"))
    import speaker_out                                        # noqa: PLC0415
    pod_on = speaker_out.parse_outputs(env, room, log)[0]
    has_pod = pod_on and link is not None and "player" in getattr(link, "keys", {})

    async def yield_to() -> None:
        m = getattr(link, "music", None)                       # Bilby's music on the same box (set once the box is connected)
        if m is not None and getattr(m, "playing", False):
            await m.pause()
    return BookPlayer(lib, store, pod=link if has_pod else None, media=getattr(link, "media", None) if has_pod else None,
                      towards=getattr(link, "box_host", ""), pc_outs=book_out.build_outputs(env, room, log), yield_to=yield_to, log=log, name=room)


_libraries: dict = {}
_stores: dict = {}
