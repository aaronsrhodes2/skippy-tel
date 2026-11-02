"""Bilby favorites: the Captain marks the song he is listening to, to review later for DJing (the Captain, 2026-10-31).

From anywhere: the ★ in the music player, purple Skippy (services/purple-tools `bilby_favorite`), or his voice at a pod ("Skippy, favorite this
song", services/satellite). "This song" is worked out here, from what his Bilby Connect players say they are playing (player.py): the one
playing now, the room's own box first when the pod asked, else the one most recently playing. One private file in the Tabletop bucket,
`bilby-favorites.json`, newest first; marking a song again bumps it to the top and counts it. `m3u()` is the list as a playlist Traktor opens
(paths relative to the library, which is how Bilby names tracks).

One pool, pruned as it plays (the Captain, 2026-11-01: "a favorites pool ... prune out songs I am tired of and add in new ones from spotify or
from my private library as it is playing"): `tire()` takes a song out of the pool ("Skippy, I'm tired of this song") and keeps it in
`bilby-tired.json`, so the hub's mixes leave it out even when it is one of his Spotify liked songs (which stay liked on Spotify). Marking it
a favorite again takes it off that list. `played()` is every song he has listened to (past PLAYED_S, on any player): discovery plays what
is not in it.
"""
from __future__ import annotations

import json
import threading
import time

import tabletop

PATH = "bilby-favorites.json"
TIRED = "bilby-tired.json"
PLAYED = "bilby-played.json"
PLAYED_S = 30.0                                # a song heard this long counts as played
MAX_PLAYED = 60000
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
            tired = self.tired()
            if any(t["track"] == track for t in tired):                # a favorite again: no longer one he is tired of
                self._put_tired([t for t in tired if t["track"] != track])
        return entry

    def tired(self) -> list[dict]:
        raw = self.store.get(TIRED)
        try:
            got = json.loads(raw) if raw else []
        except ValueError:
            got = []
        return [t for t in got if isinstance(t, dict) and isinstance(t.get("track"), str)] if isinstance(got, list) else []

    def _put_tired(self, items: list[dict]) -> None:
        self.store.put(TIRED, json.dumps(items[:MAX * 2], separators=(",", ":")).encode(), "application/json")

    def played(self) -> dict[str, list]:
        """{track: [times heard, last heard (epoch s)]}: every song he has listened to past PLAYED_S, on any player (the Connect check-ins of
        his browsers, pods and the stereo, and the car's page). Discovery is what is NOT in it."""
        raw = self.store.get(PLAYED)
        try:
            got = json.loads(raw) if raw else {}
        except ValueError:
            got = {}
        return {k: v for k, v in got.items() if isinstance(k, str) and isinstance(v, list)} if isinstance(got, dict) else {}

    def mark_played(self, track: str) -> None:
        track = str(track or "")[:600]
        if not track:
            return
        with self._lock:
            got = self.played()
            n = int((got.get(track) or [0])[0]) + 1
            got[track] = [n, round(self.clock())]
            if len(got) > MAX_PLAYED:                              # the oldest heard go first
                got = dict(sorted(got.items(), key=lambda kv: -kv[1][1])[:MAX_PLAYED])
            self.store.put(PLAYED, json.dumps(got, separators=(",", ":")).encode(), "application/json")

    def tire(self, state: dict, source: str = "") -> dict:
        """He is tired of this track (a player state): out of the pool, and on the tired list the mixes leave out. Returns the entry,
        with `was_favorite` saying whether it was in his favorites list (a Spotify liked song may not be)."""
        track = str(state.get("track") or "")[:600]
        if not track:
            raise ValueError("nothing is playing")
        was = self.remove(track)
        entry = {"track": track, "title": str(state.get("title") or "")[:200], "artist": str(state.get("artist") or "")[:200],
                 "at": round(self.clock()), "source": source[:20]}
        with self._lock:
            self._put_tired([entry] + [t for t in self.tired() if t["track"] != track])
        return {**entry, "was_favorite": was}

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
            name = f"{f.get('artist') or ''} - {f.get('title') or f['track']}"
            if f["track"].startswith("spotify:"):                # a Spotify track (on the house stereo): no file for Traktor, so a comment line
                lines.append(f"# Spotify: {name} {f['track']}")
                continue
            lines += [f"#EXTINF:-1,{name}", f["track"]]
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
