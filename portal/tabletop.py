"""The Tabletop: the heroes and the cork board of the Captain's Daggerheart campaign, Heroes of Chance, at /tabletop.

The page IS the campaign. Along the top, a card per hero (their sheet, current project and folder). Below, the cork board: one
surface where everything about the game that isn't one hero's is added and shown, as pictures (maps, handouts, generated art,
photos of the table) and notes (clues, quotes, cast cards). Everyone granted "tabletop" pins cards, drags them about, and
edits or removes their own; the Captain (the GM) can change anything.

The GM decides, card by card, who sees what: **shown** (everyone), **fog** (the players see a fog of war where the card is and
nothing of what it is, until it's revealed as the game goes on) or **hidden** (a GM-only campaign note; the players can't see that it
exists). That is enforced here, not on the screen: what a player isn't allowed to see is never sent to them, and its picture is
never served to them.

Where it lives: one private GCS bucket (TABLETOP_BUCKET, made by infra/bootstrap), read and written by the portal's
service account. Cloud Run's disk is gone on every restart, so nothing is kept anywhere else:

    board.json                  {"rev", "cards": [...]}      the cork board
    images/<card>.<ext>         each picture card's file (served back through the portal: the bucket is never public)
    heroes.json, heroes/<id>.json, heroes/<id>/files/<file>, fear.json     (see Heroes below)

The portal runs one instance, so a lock in this process is enough to keep two writers from losing each other's card;
the board is kept in memory once read, so the page's poll for changes ("has rev moved?") never touches the bucket.
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
CAMPAIGN = "Heroes of Chance"             # the one campaign this table plays
BOARD_W, BOARD_H = 6400, 5000         # the surface, in board units; the page fits it to the screen and zooms in
MAX_CARDS = 400
MAX_TEXT = 2000
MAX_IMAGE = 12 * 1024 * 1024          # under Cloud Run's 32 MB request cap with room to spare; the page shrinks big photos first
CARD_W = (80, 1600)                   # a card's width in board units
_ID = re.compile(r"^[0-9a-z]{6,24}$")
# Pictures are told apart by their first bytes, never by the name or type the browser sent.
_MAGIC = ((b"\xff\xd8\xff", "jpg", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
          (b"GIF87a", "gif", "image/gif"), (b"GIF89a", "gif", "image/gif"))
TYPES = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
COLOURS = ("green", "amber", "cyan", "violet", "white", "red")   # a text card's ink: the passthrough palette
VIS = ("shown", "fog", "hidden")      # who sees a card: everyone; a fog patch where it is and nothing of what it is; only the GM
MAX_TITLE = 80


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


class StorageMissing(RuntimeError):
    """The bucket itself doesn't exist (infra/bootstrap hasn't made it): nothing can be read or kept, and it must not look like an empty table."""


class GcsStore:
    """Files in one private bucket. The client is made on first use, so the portal starts without credentials (tests)."""

    def __init__(self, bucket: str, client=None):
        self.bucket_name = bucket
        self._client = client
        self._bucket = None

    def _b(self):
        if self._bucket is None:
            if self._client is None:
                from google.cloud import storage          # imported here: only a deployed portal needs it
                self._client = storage.Client()
            self._bucket = self._client.bucket(self.bucket_name)
        return self._bucket

    def _missing(self, err) -> bool:
        return "bucket does not exist" in str(err).lower()

    def get(self, path: str) -> bytes | None:
        blob = self._b().blob(path)
        try:
            return blob.download_as_bytes()
        except Exception as err:   # noqa: BLE001 — google.api_core.exceptions.NotFound without importing it at module load
            if type(err).__name__ == "NotFound":
                if self._missing(err):                  # a missing BUCKET is not a missing file: say so, don't read it as empty
                    raise StorageMissing(f"The bucket {self.bucket_name} doesn't exist yet.") from err
                return None
            raise

    def put(self, path: str, data: bytes, content_type: str) -> None:
        try:
            self._b().blob(path).upload_from_string(data, content_type=content_type)
        except Exception as err:   # noqa: BLE001
            if type(err).__name__ == "NotFound" and self._missing(err):
                raise StorageMissing(f"The bucket {self.bucket_name} doesn't exist yet.") from err
            raise

    def delete(self, path: str) -> None:
        try:
            self._b().blob(path).delete()
        except Exception as err:   # noqa: BLE001
            if type(err).__name__ != "NotFound":
                raise
            if self._missing(err):
                raise StorageMissing(f"The bucket {self.bucket_name} doesn't exist yet.") from err


    def list(self, prefix: str) -> list[str]:
        """The names of the files under `prefix` (Skippy's chat history uses it to find expired chats)."""
        try:
            self._b()
            return [blob.name for blob in self._client.list_blobs(self.bucket_name, prefix=prefix)]
        except Exception as err:   # noqa: BLE001
            if type(err).__name__ == "NotFound" and self._missing(err):
                raise StorageMissing(f"The bucket {self.bucket_name} doesn't exist yet.") from err
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

    def list(self, prefix):
        return [p for p in self.files if p.startswith(prefix)]


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


class Tabletop:
    """The cork board: one surface, with the cards pinned to it."""

    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._board: dict | None = None           # {"rev", "cards"}, once read

    def reset(self) -> None:
        """Forget what is held in memory, so the next read comes from storage. Called when a write fails: every write changes the
        copy in memory first, and a save that didn't happen must not leave a card that only this process believes in."""
        with self._lock:
            self._board = None

    @property
    def ready(self) -> bool:
        return self.store is not None

    # ── reading ──
    def _read_json(self, path, default):
        raw = self.store.get(path)
        return json.loads(raw) if raw else default

    def board(self) -> dict:
        with self._lock:
            if self._board is None:
                data = self._read_json("board.json", None)
                if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
                    data = {"rev": 0, "cards": []}
                data["rev"] = int(data.get("rev") or 0)
                self._board = data
            return self._board

    @staticmethod
    def _seen(card: dict, gm: bool):
        """What this viewer gets of a card: all of it, just where a fog patch sits, or nothing at all."""
        vis = card.get("vis", "shown")
        if gm or vis == "shown":
            return dict(card)
        if vis == "hidden":
            return None
        return {"id": card["id"], "vis": "fog", "x": card["x"], "y": card["y"], "w": card["w"], "rot": card.get("rot", 0), "z": card.get("z", 0)}

    def view(self, gm: bool = False) -> dict:
        b = self.board()
        cards = [seen for seen in (self._seen(c, gm) for c in b["cards"]) if seen]
        return {"title": CAMPAIGN, "rev": b["rev"], "cards": cards, "w": BOARD_W, "h": BOARD_H}

    def rev(self) -> int:
        return self.board()["rev"]

    def image(self, name: str, gm: bool = False) -> tuple[bytes, str] | None:
        found = re.fullmatch(r"([0-9a-z]{6,24})\.(jpg|png|gif|webp)", name or "")
        if not found:
            return None
        card = next((c for c in self.board()["cards"] if c["id"] == found.group(1) and c.get("img") == name), None)
        if not card or not (gm or card.get("vis", "shown") == "shown"):     # a fogged or hidden picture is not served to a player
            return None
        data = self.store.get(f"images/{name}")
        return (data, TYPES[found.group(2)]) if data else None

    # ── writing (every write: under the lock, the board's rev moves on, the file is saved before we answer) ──
    def _save(self, board: dict):
        board["rev"] += 1
        self.store.put("board.json", json.dumps(board, ensure_ascii=False).encode(), "application/json")

    def _card(self, board: dict, card_id: str, admin: bool = False) -> dict:
        for card in board["cards"]:
            if card["id"] == card_id:
                if not admin and card.get("vis", "shown") == "hidden":     # to a player a hidden card doesn't exist
                    break
                return card
        raise NotFound(card_id)

    @staticmethod
    def _mine(card: dict, who: str, admin: bool):
        if not admin and card.get("vis", "shown") != "shown":
            raise Refused("That one hasn't been revealed yet.")
        if not admin and card.get("by") != who:
            raise Refused("Only the person who added this card (or the GM) can change it.")

    @staticmethod
    def _vis_for(body: dict, admin: bool) -> str:
        """The visibility a new card starts with: the GM's choice; a player's cards are always shown."""
        return body.get("vis") if admin and body.get("vis") in VIS else "shown"

    def _place(self, board: dict, card: dict, body: dict) -> None:
        card["x"] = _num(body.get("x"), 0, BOARD_W - 40, card.get("x", BOARD_W / 2))
        card["y"] = _num(body.get("y"), 0, BOARD_H - 40, card.get("y", BOARD_H / 2))
        card["w"] = _num(body.get("w"), *CARD_W, card.get("w", 360))
        card["rot"] = _num(body.get("rot"), -12, 12, card.get("rot", 0))
        card["z"] = max((c.get("z", 0) for c in board["cards"]), default=0) + 1   # the card you touched comes to the top

    def _new_card(self, board: dict, who: str, name: str, body: dict) -> dict:
        if len(board["cards"]) >= MAX_CARDS:
            raise Refused(f"The board is full ({MAX_CARDS} cards). Take some down to make room.")
        card = {"id": new_id(), "by": who, "name": (name or who.split("@")[0])[:40], "at": int(time.time())}
        self._place(board, card, {"rot": (secrets.randbelow(9) - 4), **body})   # a slight tilt: pinned by hand
        return card

    def add_text(self, who: str, name: str, body: dict, admin: bool = False) -> dict:
        text = str(body.get("text") or "").strip()[:MAX_TEXT]
        if not text:
            raise Refused("A note needs some words.")
        with self._lock:
            board = self.board()
            card = self._new_card(board, who, name, {"w": 360, **body})
            card.update(kind="text", text=text, ink=body.get("ink") if body.get("ink") in COLOURS else "green",
                        title=" ".join(str(body.get("title") or "").split())[:MAX_TITLE], vis=self._vis_for(body, admin))
            board["cards"].append(card)
            self._save(board)
            return dict(card)

    def add_image(self, who: str, name: str, data: bytes, body: dict, admin: bool = False) -> dict:
        if not data:
            raise Refused("No picture came through.")
        if len(data) > MAX_IMAGE:
            raise Refused("That picture is too big (12 MB at most).")
        kind = image_kind(data)
        if not kind:
            raise Refused("Only JPEG, PNG, GIF or WebP pictures.")
        caption = str(body.get("text") or "").strip()[:MAX_TEXT]
        with self._lock:
            board = self.board()
            card = self._new_card(board, who, name, {"w": 520, **body})
            card.update(kind="image", img=f"{card['id']}.{kind[0]}", text=caption, vis=self._vis_for(body, admin),
                        ratio=_num(body.get("ratio"), 0.05, 20, 0.75))   # height / width, so the page lays it out before it loads
            self.store.put(f"images/{card['img']}", data, kind[1])
            board["cards"].append(card)
            self._save(board)
            return dict(card)

    def move(self, card_id: str, who: str, admin: bool, body: dict) -> dict:
        with self._lock:
            board = self.board()
            card = self._card(board, card_id, admin)
            self._mine(card, who, admin)
            self._place(board, card, body)
            self._save(board)
            return dict(card)

    def edit(self, card_id: str, who: str, admin: bool, body: dict) -> dict:
        with self._lock:
            board = self.board()
            card = self._card(board, card_id, admin)
            self._mine(card, who, admin)
            text = str(body.get("text") or "").strip()[:MAX_TEXT]
            if card["kind"] == "text" and not text:
                raise Refused("A note needs some words.")
            card["text"] = text
            if card["kind"] == "text" and "title" in body:
                card["title"] = " ".join(str(body.get("title") or "").split())[:MAX_TITLE]
            if body.get("ink") in COLOURS and card["kind"] == "text":
                card["ink"] = body["ink"]
            self._save(board)
            return dict(card)

    def set_vis(self, card_id: str, vis) -> dict:
        """The GM shows a card, fogs it, or hides it (shown / fog / hidden)."""
        if vis not in VIS:
            raise Refused("A card is shown, fogged or hidden.")
        with self._lock:
            board = self.board()
            card = self._card(board, card_id, True)
            card["vis"] = vis
            self._save(board)
            return dict(card)

    def remove(self, card_id: str, who: str, admin: bool) -> None:
        with self._lock:
            board = self.board()
            card = self._card(board, card_id, admin)
            self._mine(card, who, admin)
            if card.get("img"):
                self.store.delete(f"images/{card['img']}")
            board["cards"] = [c for c in board["cards"] if c["id"] != card_id]
            self._save(board)


# ── Heroes: one interactive sheet per character, every field of the official Daggerheart sheet and its character guide page ──
#
#   heroes.json                       ["<id>", ...]
#   heroes/<id>.json                  {"id", "rev", "player", "sheet": {...}, "files": [...], "history": [...]}
#   heroes/<id>/files/<fid>.<ext>     the hero's folder: the print packet, sheet photos, portraits
#   fear.json                         {"value": n}   the GM's Fear, carried from session to session
#
# The sheet is a fixed shape (SPEC below). A change is a list of (path, value) pairs, each checked against SPEC: a path the
# sheet doesn't have, or a value of the wrong kind, is refused, so the stored sheet can only ever hold the official fields.
# Each change is logged (who, when, what it was) so a paper sheet can be brought level with the web one.

TRAITS = ("agility", "strength", "finesse", "instinct", "presence", "knowledge")
LISTS = {"experiences": 5, "inventory": 12, "inv_weapons": 2, "background": 3, "connections": 3, "domain_cards": 10}
FEAR_MAX = 12
MAX_FILE = 30 * 1024 * 1024
HISTORY_KEEP = 400
GM_REF_MAX = 8                       # the GM's quick reference: a handful of details per hero, never shown to players
FILE_TYPES = {"pdf": "application/pdf", **TYPES}


def _txt(n):
    return ("str", n)


def _int(lo, hi):
    return ("int", lo, hi)


BOOL = ("bool",)
WEAPON = {"name": _txt(80), "trait_range": _txt(80), "damage": _txt(60), "feature": _txt(400)}
TIER2 = (("traits", 3), ("hp", 2), ("stress", 2), ("experiences", 1), ("domain_card", 1), ("evasion", 1))
TIER34 = TIER2 + (("subclass", 1), ("proficiency", 2), ("multiclass", 2))
SPEC: dict[str, tuple] = {
    # the top strip
    "name": _txt(80), "pronouns": _txt(40), "heritage": _txt(100), "subclass": _txt(60), "level": _int(1, 10),
    "class": _txt(40), "domains": _txt(60),
    # defence
    "evasion": _int(0, 40), "armor_score": _int(0, 12), "armor_slots": _int(0, 12),
    **{f"traits.{t}.value": _int(-5, 10) for t in TRAITS}, **{f"traits.{t}.marked": BOOL for t in TRAITS},
    # damage and health
    "thresholds.major": _int(0, 99), "thresholds.severe": _int(0, 99),
    "hp.max": _int(1, 12), "hp.marked": _int(0, 12), "stress.max": _int(1, 12), "stress.marked": _int(0, 12),
    "hope": _int(0, 6), "hope_feature": _txt(800),
    "experiences.#.name": _txt(80), "experiences.#.bonus": _int(-5, 10),
    "gold.handfuls": _int(0, 10), "gold.bags": _int(0, 10), "gold.chest": BOOL,
    "class_feature": _txt(3000),
    # active weapons and armor
    "proficiency": _int(0, 6),
    **{f"weapons.primary.{k}": v for k, v in WEAPON.items()}, **{f"weapons.secondary.{k}": v for k, v in WEAPON.items()},
    "armor.name": _txt(80), "armor.thresholds": _txt(20), "armor.score": _txt(10), "armor.feature": _txt(400),
    "inventory.#": _txt(160),
    **{f"inv_weapons.#.{k}": v for k, v in WEAPON.items()}, "inv_weapons.#.slot": ("pick", "", "primary", "secondary"),
    # the character guide page
    "description.clothes": _txt(160), "description.eyes": _txt(160), "description.body": _txt(160),
    "description.skin": _txt(160), "description.attitude": _txt(160), "starting_choices": _txt(400),
    "background.#.q": _txt(300), "background.#.a": _txt(1500),
    "connections.#.q": _txt(300), "connections.#.a": _txt(1500),
    **{f"levelups.tier2.{k}": _int(0, n) for k, n in TIER2},
    **{f"levelups.tier{t}.{k}": _int(0, n) for t in (3, 4) for k, n in TIER34},
    # beyond the paper: the cards in hand, the hero's downtime project, the player's own notes
    "domain_cards.#": _txt(160),
    "project.name": _txt(100), "project.goal": _txt(800), "project.clock_max": _int(1, 12), "project.clock": _int(0, 12),
    "project.notes": _txt(3000),
    "notes": _txt(6000),
}


def _default(kind):
    if kind[0] == "int":
        return max(0, kind[1])
    return {"str": "", "bool": False, "pick": ""}[kind[0]]


def _expand(path: str) -> list[str]:
    head, sep, tail = path.partition(".#")
    if not sep:
        return [path]
    return [f"{head}.{i}{tail}" for i in range(LISTS[head])]


def _put(obj, path: str, value):
    keys = path.split(".")
    for i, k in enumerate(keys[:-1]):
        nxt = [None] * LISTS[k] if keys[i + 1].isdigit() else {}
        if isinstance(obj, list):
            if obj[int(k)] is None:
                obj[int(k)] = nxt
            obj = obj[int(k)]
        else:
            obj = obj.setdefault(k, nxt)
    if isinstance(obj, list):
        obj[int(keys[-1])] = value
    else:
        obj[keys[-1]] = value


def _get(obj, path: str):
    for k in path.split("."):
        obj = obj[int(k)] if isinstance(obj, list) else obj[k]
    return obj


def blank_sheet() -> dict:
    sheet: dict = {}
    for path, kind in SPEC.items():
        for concrete in _expand(path):
            _put(sheet, concrete, _default(kind))
    sheet["hp"]["max"], sheet["stress"]["max"], sheet["project"]["clock_max"] = 6, 6, 4
    return sheet


def spec_of(path):
    """The kind a sheet path holds, or None when the sheet has no such field (indices checked against the list's size)."""
    if not isinstance(path, str) or len(path) > 80:
        return None
    parts, shape = path.split("."), []
    for i, p in enumerate(parts):
        if p.isdigit():
            if i == 0 or parts[i - 1] not in LISTS or int(p) >= LISTS[parts[i - 1]]:
                return None
            shape.append("#")
        else:
            shape.append(p)
    return SPEC.get(".".join(shape))


def clean(kind, value):
    """The value as the field stores it; Refused when it can't be."""
    if kind[0] == "str":
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise Refused("That field takes text.")
        return str(value)[:kind[1]]
    if kind[0] == "int":
        if isinstance(value, bool):
            raise Refused("That field takes a number.")
        try:
            n = int(float(value))
        except (TypeError, ValueError, OverflowError):
            raise Refused("That field takes a number.") from None
        return max(kind[1], min(kind[2], n))
    if kind[0] == "bool":
        return bool(value)
    if value not in kind[1:]:
        raise Refused("Not one of the choices.")
    return value


def avatar_focus(raw) -> dict:
    """An avatar's crop: {"x", "y"} the point to centre on (percent of the picture) and {"zoom"} 1 to 4. Anything else is the default."""
    raw = raw if isinstance(raw, dict) else {}
    try:
        zoom = float(raw.get("zoom", 1))
    except (TypeError, ValueError):
        zoom = 1.0
    return {"x": clean(_int(0, 100), raw.get("x", 50)), "y": clean(_int(0, 100), raw.get("y", 30)),
            "zoom": round(max(1.0, min(4.0, zoom)), 2) if zoom == zoom else 1.0}


def flatten(obj, prefix="") -> list:
    if isinstance(obj, dict):
        return [pair for k, v in obj.items() for pair in flatten(v, f"{prefix}{k}.")]
    if isinstance(obj, list):
        return [pair for i, v in enumerate(obj) for pair in flatten(v, f"{prefix}{i}.")]
    return [(prefix[:-1], obj)]


class Heroes:
    """The party's sheets, their folders, and the GM's Fear. Shares the Tabletop's store and lock."""

    def __init__(self, table: Tabletop):
        self.t = table
        self._ids: list[str] | None = None
        self._heroes: dict[str, dict] = {}
        self._fear: int | None = None

    @property
    def store(self):
        return self.t.store

    def reset(self) -> None:
        """As Tabletop.reset(): drop the copies in memory (after a failed write) so the next read comes from storage."""
        with self.t._lock:
            self._ids = None
            self._heroes.clear()
            self._fear = None

    # ── the Fear ──
    def fear(self) -> int:
        with self.t._lock:
            if self._fear is None:
                self._fear = int((self.t._read_json("fear.json", {}) or {}).get("value", 0))
            return self._fear

    def set_fear(self, value=None, delta=None) -> int:
        with self.t._lock:
            now = self.fear()
            new = now + clean(_int(-FEAR_MAX, FEAR_MAX), delta) if delta is not None else clean(_int(0, FEAR_MAX), value)
            self._fear = max(0, min(FEAR_MAX, new))
            self.store.put("fear.json", json.dumps({"value": self._fear, "at": int(time.time())}).encode(), "application/json")
            return self._fear

    # ── reading ──
    def ids(self) -> list[str]:
        with self.t._lock:
            if self._ids is None:
                ids = self.t._read_json("heroes.json", [])
                self._ids = [i for i in ids if isinstance(i, str) and _ID.match(i)]
            return list(self._ids)

    def hero(self, hid: str) -> dict:
        if not _ID.match(hid or ""):
            raise NotFound(hid)
        with self.t._lock:
            if hid not in self._heroes:
                if hid not in self.ids():
                    raise NotFound(hid)
                data = self.t._read_json(f"heroes/{hid}.json", None)
                if not isinstance(data, dict):
                    data = {"id": hid, "rev": 0, "player": "", "player_name": "", "files": [], "history": []}
                fresh = blank_sheet()                     # a sheet saved before a field existed gets the field, empty
                for path, value in flatten(data.get("sheet") or {}):
                    kind = spec_of(path)
                    if kind:
                        try:
                            _put(fresh, path, clean(kind, value))
                        except Refused:
                            pass
                data["sheet"] = fresh
                self._heroes[hid] = data
            return self._heroes[hid]

    def summary(self, gm: bool = False) -> list[dict]:
        out = []
        for hid in self.ids():
            h = self.hero(hid)
            s = h["sheet"]
            row = {"id": hid, "name": s["name"], "class": s["class"], "subclass": s["subclass"], "level": s["level"],
                   "player": h.get("player", ""), "player_name": h.get("player_name", ""),
                   "project": s["project"]["name"], "clock": s["project"]["clock"], "clock_max": s["project"]["clock_max"],
                   "avatar": h.get("avatar", ""), "avatar_focus": h.get("avatar_focus") or avatar_focus(None)}
            if gm:
                row["gm_ref"] = list(h.get("gm_ref", []))
            out.append(row)
        return out

    def view(self, hid: str, history: bool = False) -> dict:
        h = self.hero(hid)
        out = {"id": hid, "rev": h["rev"], "player": h.get("player", ""),
               "player_name": h.get("player_name", ""), "avatar": h.get("avatar", ""),
               "avatar_focus": h.get("avatar_focus") or avatar_focus(None),
               "sheet": json.loads(json.dumps(h["sheet"])), "files": [dict(f) for f in h["files"]]}
        if history:                                   # the GM's eyes only: the change log, and the quick reference for storytelling
            out["history"] = list(h["history"][-60:])
            out["gm_ref"] = list(h.get("gm_ref", []))
        return out

    def can_edit(self, hid: str, who: str, gm: bool) -> bool:
        player = (self.hero(hid).get("player") or "").lower()
        return gm or (bool(player) and (who or "").lower() == player)

    def file(self, hid: str, fid: str):
        h = self.hero(hid)
        for f in h["files"]:
            if f["id"] == fid:
                data = self.store.get(f"heroes/{hid}/files/{fid}.{f['ext']}")
                return (data, FILE_TYPES[f["ext"]], f) if data else None
        return None

    # ── writing ──
    def _save_ids(self):
        self.store.put("heroes.json", json.dumps(self._ids).encode(), "application/json")

    def _save(self, h: dict):
        h["rev"] += 1
        h["history"] = h["history"][-HISTORY_KEEP:]
        self.store.put(f"heroes/{h['id']}.json", json.dumps(h, ensure_ascii=False).encode(), "application/json")

    @staticmethod
    def _email(value) -> str:
        email = str(value or "").strip().lower()
        if email and not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,}", email):
            raise Refused("That isn't an email address.")
        return email

    @staticmethod
    def _gm_ref(lines) -> list[str]:
        if isinstance(lines, str):
            lines = lines.splitlines()
        if not isinstance(lines, list):
            raise Refused("The quick reference is a list of details.")
        return [str(x).strip()[:400] for x in lines if str(x).strip()][:GM_REF_MAX]

    def set_gm_ref(self, hid: str, lines) -> None:
        with self.t._lock:
            h = self.hero(hid)
            h["gm_ref"] = self._gm_ref(lines)
            self._save(h)

    def create(self, by: str, name, sheet=None, player="", player_name="", gm_ref=None) -> dict:
        with self.t._lock:
            self.ids()
            hid = new_id()
            h = {"id": hid, "rev": 0, "player": self._email(player), "player_name": str(player_name or "")[:60],
                 "sheet": blank_sheet(), "files": [], "history": [], "gm_ref": self._gm_ref(gm_ref or [])}
            skipped = self._apply(h, by, flatten(sheet if isinstance(sheet, dict) else {}), log=False, strict=False)
            _put(h["sheet"], "name", clean(SPEC["name"], name or h["sheet"]["name"] or "New hero"))
            h["history"].append({"at": int(time.time()), "by": by, "path": "*", "old": None, "new": "created"})
            self._heroes[hid] = h
            self._ids.append(hid)
            self._save(h)
            self._save_ids()
            return {"id": hid, "skipped": skipped}

    def remove(self, hid: str):
        with self.t._lock:
            h = self.hero(hid)
            for f in h["files"]:
                self.store.delete(f"heroes/{hid}/files/{f['id']}.{f['ext']}")
            self.store.delete(f"heroes/{hid}.json")
            self._ids = [i for i in self._ids if i != hid]
            self._heroes.pop(hid, None)
            self._save_ids()

    def set_player(self, hid: str, player, player_name="") -> None:
        with self.t._lock:
            h = self.hero(hid)
            h["player"] = self._email(player)
            h["player_name"] = str(player_name or "")[:60]
            self._save(h)

    def _apply(self, h, by, ops, log=True, strict=True) -> list:
        checked, skipped = [], []
        for path, value in ops:
            kind = spec_of(path)
            if not kind:
                if strict:
                    raise Refused(f"The sheet has no field '{str(path)[:40]}'.")
                skipped.append(str(path))
                continue
            try:
                checked.append((path, clean(kind, value)))
            except Refused:
                if strict:
                    raise
                skipped.append(str(path))
        now = int(time.time())
        for path, value in checked:
            old = _get(h["sheet"], path)
            if old != value:
                _put(h["sheet"], path, value)
                if log:
                    h["history"].append({"at": now, "by": by, "path": path, "old": old, "new": value})
        return skipped

    def update(self, hid: str, by: str, ops) -> dict:
        if not isinstance(ops, list) or not ops or len(ops) > 400 or not all(isinstance(op, dict) for op in ops):
            raise Refused("Nothing to change.")
        with self.t._lock:
            h = self.hero(hid)
            self._apply(h, by, [(op.get("path"), op.get("value")) for op in ops])
            self._save(h)
            return {"rev": h["rev"]}

    def add_file(self, hid: str, by: str, data: bytes, name) -> dict:
        if not data:
            raise Refused("No file came through.")
        if len(data) > MAX_FILE:
            raise Refused("That file is too big (30 MB at most).")
        if data.startswith(b"%PDF-"):
            ext = "pdf"
        else:
            kind = image_kind(data)
            if not kind:
                raise Refused("Only PDFs and JPEG, PNG, GIF or WebP pictures.")
            ext = kind[0]
        label = re.sub(r"[\x00-\x1f/\\]", "", str(name or "").strip())[:100] or f"file.{ext}"
        with self.t._lock:
            h = self.hero(hid)
            if len(h["files"]) >= 60:
                raise Refused("This folder is full (60 files).")
            f = {"id": new_id(), "name": label, "ext": ext, "size": len(data), "by": by, "at": int(time.time())}
            self.store.put(f"heroes/{hid}/files/{f['id']}.{ext}", data, FILE_TYPES[ext])
            h["files"].append(f)
            h["history"].append({"at": f["at"], "by": by, "path": "files", "old": None, "new": label})
            self._save(h)
            return dict(f)

    def remove_file(self, hid: str, fid: str, by: str, gm: bool) -> None:
        with self.t._lock:
            h = self.hero(hid)
            f = next((f for f in h["files"] if f["id"] == fid), None)
            if not f:
                raise NotFound(fid)
            if not gm and f["by"] != by:
                raise Refused("Only the GM or whoever added a file can take it out.")
            self.store.delete(f"heroes/{hid}/files/{fid}.{f['ext']}")
            h["files"] = [x for x in h["files"] if x["id"] != fid]
            if h.get("avatar") == fid:                  # the avatar's file is gone: back to the folder glyph
                h["avatar"] = ""
            h["history"].append({"at": int(time.time()), "by": by, "path": "files", "old": f["name"], "new": None})
            self._save(h)

    def set_avatar(self, hid: str, fid, by: str, focus=None) -> dict:
        """The hero's face on their card: one of the pictures in their folder (not a PDF), or "" to clear it. `focus` is the crop:
        the point to centre on (x, y in percent of the picture) and how far to zoom in (1 to 4); a full-length picture wants its face."""
        fid = str(fid or "")
        crop = avatar_focus(focus)
        with self.t._lock:
            h = self.hero(hid)
            if fid:
                f = next((f for f in h["files"] if f["id"] == fid), None)
                if not f:
                    raise NotFound(fid)
                if f["ext"] == "pdf":
                    raise Refused("An avatar has to be a picture.")
            h["history"].append({"at": int(time.time()), "by": by, "path": "avatar", "old": h.get("avatar") or None, "new": fid or None})
            h["avatar"] = fid
            h["avatar_focus"] = crop
            self._save(h)
            return {"avatar": fid, "avatar_focus": crop}
