"""The Tabletop: shared adventure boards for the Captain's tabletop RPG table (Heroes of Chance), at /tabletop.

A board is a big surface people pin cards on: a picture (a map, a handout, a sketch, a photo of the table) or a few lines
of text (a clue, a quote, a note). The Captain makes the boards (one per episode or realm); everyone granted "tabletop"
adds cards, drags them about, and can edit or remove their own. The Captain can change anything.

Where it lives: one private GCS bucket (TABLETOP_BUCKET, made by infra/bootstrap), read and written by the portal's
service account. Cloud Run's disk is gone on every restart, so nothing is kept anywhere else:

    boards.json                 [{"id", "title", "by", "at"}, ...]   the boards, in the order they were made
    boards/<id>.json            {"id", "title", "rev", "cards": [...]}
    images/<board>/<card>.<ext> each picture card's file (served back through the portal: the bucket is never public)

The portal runs one instance, so a lock in this process is enough to keep two writers from losing each other's card;
boards are kept in memory once read, so the page's poll for changes ("has rev moved?") never touches the bucket.
Without TABLETOP_BUCKET (a local run, a portal deployed before bootstrap made the bucket) the page says so and nothing
is stored.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time

log = logging.getLogger(__name__)

BUCKET_ENV = "TABLETOP_BUCKET"
BOARD_W, BOARD_H = 4000, 3000         # the surface, in board units; the page fits it to the screen and zooms in
MAX_CARDS = 400
MAX_TEXT = 2000
MAX_TITLE = 80
MAX_IMAGE = 12 * 1024 * 1024          # under Cloud Run's 32 MB request cap with room to spare; the page shrinks big photos first
CARD_W = (80, 1600)                   # a card's width in board units
_ID = re.compile(r"^[0-9a-z]{6,24}$")
# Pictures are told apart by their first bytes, never by the name or type the browser sent.
_MAGIC = ((b"\xff\xd8\xff", "jpg", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
          (b"GIF87a", "gif", "image/gif"), (b"GIF89a", "gif", "image/gif"))
TYPES = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
COLOURS = ("green", "amber", "cyan", "violet", "white", "red")   # a text card's ink: the passthrough palette


class Refused(ValueError):
    """The request can't be done as asked (bad input, not yours to change, the board is full). The message says why."""


class NotFound(LookupError):
    pass


def new_id() -> str:
    return secrets.token_hex(6)


def image_kind(data: bytes) -> tuple[str, str] | None:
    """(extension, content type) of a picture, from its first bytes; None when it isn't one we take."""
    for magic, ext, ctype in _MAGIC:
        if data.startswith(magic):
            return ext, ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


class GcsStore:
    """Files in one private bucket. The client is made on first use, so the portal starts without credentials (tests)."""

    def __init__(self, bucket: str):
        self.bucket_name = bucket
        self._bucket = None

    def _b(self):
        if self._bucket is None:
            from google.cloud import storage          # imported here: only a deployed portal needs it
            self._bucket = storage.Client().bucket(self.bucket_name)
        return self._bucket

    def get(self, path: str) -> bytes | None:
        blob = self._b().blob(path)
        try:
            return blob.download_as_bytes()
        except Exception as err:   # noqa: BLE001 — google.api_core.exceptions.NotFound without importing it at module load
            if type(err).__name__ == "NotFound":
                return None
            raise

    def put(self, path: str, data: bytes, content_type: str) -> None:
        self._b().blob(path).upload_from_string(data, content_type=content_type)

    def delete(self, path: str) -> None:
        try:
            self._b().blob(path).delete()
        except Exception as err:   # noqa: BLE001
            if type(err).__name__ != "NotFound":
                raise


class MemoryStore:
    """The same three calls in memory: tests, and tests/dev_tabletop-style local runs."""

    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}

    def get(self, path):
        return self.files.get(path, (None,))[0]

    def put(self, path, data, content_type):
        self.files[path] = (bytes(data), content_type)

    def delete(self, path):
        self.files.pop(path, None)


def store_from_env():
    bucket = os.environ.get(BUCKET_ENV, "").strip()
    return GcsStore(bucket) if bucket else None


def _num(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:                       # NaN
        return default
    return max(lo, min(hi, v))


def _title(value) -> str:
    title = " ".join(str(value or "").split())[:MAX_TITLE]
    if not title:
        raise Refused("A board needs a name.")
    return title


class Tabletop:
    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._boards: dict[str, dict] = {}        # id -> board, once read
        self._index: list[dict] | None = None

    @property
    def ready(self) -> bool:
        return self.store is not None

    # ── reading ──
    def _read_json(self, path, default):
        raw = self.store.get(path)
        return json.loads(raw) if raw else default

    def boards(self) -> list[dict]:
        with self._lock:
            if self._index is None:
                index = self._read_json("boards.json", [])
                self._index = [b for b in index if isinstance(b, dict) and _ID.match(str(b.get("id", "")))]
            return [dict(b) for b in self._index]

    def board(self, board_id: str) -> dict:
        if not _ID.match(board_id or ""):
            raise NotFound(board_id)
        with self._lock:
            if board_id not in self._boards:
                if not any(b["id"] == board_id for b in self.boards()):
                    raise NotFound(board_id)
                data = self._read_json(f"boards/{board_id}.json", None)
                if not isinstance(data, dict):
                    title = next(b["title"] for b in self.boards() if b["id"] == board_id)
                    data = {"id": board_id, "title": title, "rev": 0, "cards": []}
                self._boards[board_id] = data
            return self._boards[board_id]

    def view(self, board_id: str) -> dict:
        b = self.board(board_id)
        return {"id": b["id"], "title": b["title"], "rev": b["rev"], "cards": [dict(c) for c in b["cards"]],
                "w": BOARD_W, "h": BOARD_H}

    def rev(self, board_id: str) -> int:
        return self.board(board_id)["rev"]

    def image(self, board_id: str, name: str) -> tuple[bytes, str] | None:
        found = re.fullmatch(r"([0-9a-z]{6,24})\.(jpg|png|gif|webp)", name or "")
        if not found:
            return None
        self.board(board_id)                      # the board must exist (and so must the grant, checked by the route)
        data = self.store.get(f"images/{board_id}/{name}")
        return (data, TYPES[found.group(2)]) if data else None

    # ── writing (every write: under the lock, the board's rev moves on, the file is saved before we answer) ──
    def _save_index(self):
        self.store.put("boards.json", json.dumps(self._index, ensure_ascii=False).encode(), "application/json")

    def _save(self, board: dict):
        board["rev"] += 1
        self.store.put(f"boards/{board['id']}.json", json.dumps(board, ensure_ascii=False).encode(), "application/json")

    def create_board(self, title, by: str) -> dict:
        with self._lock:
            self.boards()
            board = {"id": new_id(), "title": _title(title), "by": by, "at": int(time.time())}
            self._boards[board["id"]] = {"id": board["id"], "title": board["title"], "rev": 0, "cards": []}
            self._save(self._boards[board["id"]])
            self._index.append(board)
            self._save_index()
            return dict(board)

    def rename_board(self, board_id: str, title) -> None:
        with self._lock:
            board = self.board(board_id)
            board["title"] = _title(title)
            for b in self._index:
                if b["id"] == board_id:
                    b["title"] = board["title"]
            self._save(board)
            self._save_index()

    def delete_board(self, board_id: str) -> None:
        with self._lock:
            board = self.board(board_id)
            for card in board["cards"]:
                if card.get("img"):
                    self.store.delete(f"images/{board_id}/{card['img']}")
            self.store.delete(f"boards/{board_id}.json")
            self._index = [b for b in self._index if b["id"] != board_id]
            self._boards.pop(board_id, None)
            self._save_index()

    def _card(self, board: dict, card_id: str) -> dict:
        for card in board["cards"]:
            if card["id"] == card_id:
                return card
        raise NotFound(card_id)

    @staticmethod
    def _mine(card: dict, who: str, admin: bool):
        if not admin and card.get("by") != who:
            raise Refused("Only the person who added this card (or the GM) can change it.")

    def _place(self, board: dict, card: dict, body: dict) -> None:
        card["x"] = _num(body.get("x"), 0, BOARD_W - 40, card.get("x", BOARD_W / 2))
        card["y"] = _num(body.get("y"), 0, BOARD_H - 40, card.get("y", BOARD_H / 2))
        card["w"] = _num(body.get("w"), *CARD_W, card.get("w", 360))
        card["rot"] = _num(body.get("rot"), -12, 12, card.get("rot", 0))
        card["z"] = max((c.get("z", 0) for c in board["cards"]), default=0) + 1   # the card you touched comes to the top

    def _new_card(self, board: dict, who: str, name: str, body: dict) -> dict:
        if len(board["cards"]) >= MAX_CARDS:
            raise Refused(f"This board is full ({MAX_CARDS} cards). Ask the GM for a new one.")
        card = {"id": new_id(), "by": who, "name": (name or who.split("@")[0])[:40], "at": int(time.time())}
        self._place(board, card, {"rot": (secrets.randbelow(9) - 4), **body})   # a slight tilt: pinned by hand
        return card

    def add_text(self, board_id: str, who: str, name: str, body: dict) -> dict:
        text = str(body.get("text") or "").strip()[:MAX_TEXT]
        if not text:
            raise Refused("A note needs some words.")
        with self._lock:
            board = self.board(board_id)
            card = self._new_card(board, who, name, {"w": 360, **body})
            card.update(kind="text", text=text, ink=body.get("ink") if body.get("ink") in COLOURS else "green")
            board["cards"].append(card)
            self._save(board)
            return dict(card)

    def add_image(self, board_id: str, who: str, name: str, data: bytes, body: dict) -> dict:
        if not data:
            raise Refused("No picture came through.")
        if len(data) > MAX_IMAGE:
            raise Refused("That picture is too big (12 MB at most).")
        kind = image_kind(data)
        if not kind:
            raise Refused("Only JPEG, PNG, GIF or WebP pictures.")
        caption = str(body.get("text") or "").strip()[:MAX_TEXT]
        with self._lock:
            board = self.board(board_id)
            card = self._new_card(board, who, name, {"w": 520, **body})
            card.update(kind="image", img=f"{card['id']}.{kind[0]}", text=caption,
                        ratio=_num(body.get("ratio"), 0.05, 20, 0.75))   # height / width, so the page lays it out before it loads
            self.store.put(f"images/{board_id}/{card['img']}", data, kind[1])
            board["cards"].append(card)
            self._save(board)
            return dict(card)

    def move(self, board_id: str, card_id: str, who: str, admin: bool, body: dict) -> dict:
        with self._lock:
            board = self.board(board_id)
            card = self._card(board, card_id)
            self._mine(card, who, admin)
            self._place(board, card, body)
            self._save(board)
            return dict(card)

    def edit(self, board_id: str, card_id: str, who: str, admin: bool, body: dict) -> dict:
        with self._lock:
            board = self.board(board_id)
            card = self._card(board, card_id)
            self._mine(card, who, admin)
            text = str(body.get("text") or "").strip()[:MAX_TEXT]
            if card["kind"] == "text" and not text:
                raise Refused("A note needs some words.")
            card["text"] = text
            if body.get("ink") in COLOURS and card["kind"] == "text":
                card["ink"] = body["ink"]
            self._save(board)
            return dict(card)

    def remove(self, board_id: str, card_id: str, who: str, admin: bool) -> None:
        with self._lock:
            board = self.board(board_id)
            card = self._card(board, card_id)
            self._mine(card, who, admin)
            if card.get("img"):
                self.store.delete(f"images/{board_id}/{card['img']}")
            board["cards"] = [c for c in board["cards"] if c["id"] != card_id]
            self._save(board)
