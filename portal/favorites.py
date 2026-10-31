"""Bilby favorites: the Captain marks the song he is listening to, to review later for DJing (the Captain, 2026-10-31).

From anywhere: the ★ in the music player, purple Skippy (services/purple-tools `bilby_favorite`), or his voice at a pod ("Skippy, favorite this
song", services/satellite). "This song" is worked out here, from what his Bilby Connect players say they are playing (player.py): the one
playing now, the room's own box first when the pod asked, else the one most recently playing. One private file in the Tabletop bucket,
`bilby-favorites.json`, newest first; marking a song again bumps it to the top and counts it. `m3u()` is the list as a playlist Traktor opens
(paths relative to the library, which is how Bilby names tracks).
"""
from __future__ import annotations

import json
import threading
import time

import tabletop

PATH = "bilby-favorites.json"
MAX = 2000


class Favorites:
    def __init__(self, store=None, clock=time.time):
        self.store = store if store is not None else tabletop.MemoryStore()
        self.clock = clock
        self._lock = threading.Lock()

    def items(self) -> list[dict]:
        raw = self.store.get(PATH)
        try:
            got = json.loads(raw) if raw else []
        except ValueError:
            got = []
        return [f for f in got if isinstance(f, dict) and isinstance(f.get("track"), str)] if isinstance(got, list) else []

    def add(self, state: dict, source: str = "", where: str = "") -> dict:
        """Mark one track (a player state: track, title, artist, pos). Returns the entry."""
        track = str(state.get("track") or "")[:600]
        if not track:
            raise ValueError("nothing is playing")
        with self._lock:
            items = self.items()
            old = next((f for f in items if f["track"] == track), None)
            entry = {"track": track, "title": str(state.get("title") or "")[:200], "artist": str(state.get("artist") or "")[:200],
                     "at": round(self.clock()), "pos": round(float(state.get("pos") or 0), 1), "source": source[:20], "where": where[:40],
                     "count": int((old or {}).get("count", 0)) + 1}
            items = [entry] + [f for f in items if f["track"] != track]
            self.store.put(PATH, json.dumps(items[:MAX], separators=(",", ":")).encode(), "application/json")
        return entry

    def remove(self, track: str) -> bool:
        with self._lock:
            items = self.items()
            kept = [f for f in items if f["track"] != track]
            if len(kept) == len(items):
                return False
            self.store.put(PATH, json.dumps(kept, separators=(",", ":")).encode(), "application/json")
        return True

    def m3u(self) -> str:
        lines = ["#EXTM3U", "#PLAYLIST:Bilby favorites"]
        for f in self.items():
            lines += [f"#EXTINF:-1,{f.get('artist') or ''} - {f.get('title') or f['track']}", f["track"]]
        return "\n".join(lines) + "\n"


def now_playing(devices: list[dict], prefer: str = "") -> dict | None:
    """The state of what the Captain is listening to, from player.listing(): `prefer` (a device id, the pod that asked) when it has a track,
    else the one playing now (newest first), else the one most recently playing anything."""
    with_track = [d for d in devices if (d.get("state") or {}).get("track")]
    for d in with_track:
        if prefer and d.get("id") == prefer:
            return {**d["state"], "device": d["id"], "name": d.get("name", "")}
    for d in with_track:
        if d["state"].get("playing"):
            return {**d["state"], "device": d["id"], "name": d.get("name", "")}
    return {**with_track[0]["state"], "device": with_track[0]["id"], "name": with_track[0].get("name", "")} if with_track else None


def from_env() -> Favorites:
    return Favorites(tabletop.store_from_env())
