"""Shifting Prophesy: purple Skippy's shake-down cruise (/shifting-prophesy). The home AI, running as itself in the workshop
on Skippy Core, builds a whole browser game one prompt at a time; Claude Haiku gets the same prompts with every tool, as the
reference. Where purple falls short is what it needs next.

The cruise's code is local to skippy-pc (Platinum §23: local git, no GitHub): each model's build and the referee, which runs
the turns. After each turn the referee's runner uploads each build and the turn's record through the home-token route
(PUT /home/prophesy/<folder>/<path>) into the portal's own bucket (the Tabletop's, TABLETOP_BUCKET), under prophesy/. The
portal reads them at request time with Doom Loop's path and file-type rules (doomloop.py), so a turn shows up with no
portal redeploy. Nothing here talks to an LLM.

    prophesy/haiku/, prophesy/purple/   index.html, version.json (uploaded last), and whatever the build loads
    prophesy/turns/                     index.json (one row per turn, uploaded last) and <NNN>/: turn.json, report.md,
                                        reply.md and actions.txt per arm, and <arm>/shots/*.png

Every page and file is for signed-in people; each build's /version is public (the runner checks it after an upload).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import doomloop
import tabletop
from doomloop import VERSION_FIELDS, clean_path

log = logging.getLogger(__name__)

PREFIX = "prophesy/"         # the cruise's corner of the portal's bucket
ARMS = {                     # purple first: it is the ship under test
    "purple": {"label": "Purple Skippy", "detail": "the workshop's Private brain: Skippy Core's coder (Qwen3.6-35B-A3B), local and free"},
    "haiku": {"label": "Haiku 5.5", "detail": "the reference: claude-haiku-5-5 in Claude Code with every tool"},
}
RECORD = "turns"
FOLDERS = (*ARMS, RECORD)
MAX_FILE = 25 * 1024 * 1024  # one uploaded file, under Cloud Run's 32 MiB request cap
VERSION_TTL = 10.0           # seconds a build's version.json is trusted before its folder is asked again
INDEX_TTL = 10.0             # and the record's index.json
_SAFE_ID = re.compile(r"^[0-9A-Za-z._-]{1,64}$")
_TURN_ID = re.compile(r"^\d{3}$")
TEXT_TYPES = {".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


def bucket():
    """The portal's own private bucket (tabletop.GcsStore: get, put, delete, list), or None when none is configured."""
    return tabletop.store_from_env()


def _key(folder: str, path: str) -> str:
    return f"{PREFIX}{folder}/{path}"


def read(folder: str, path: str) -> bytes | None:
    """A file of one folder, or None (no such file, a path that leaves the folder, no bucket: logged, not raised)."""
    safe = clean_path(path)
    store = bucket()
    if safe is None or store is None or folder not in FOLDERS:
        return None
    try:
        return store.get(_key(folder, safe))
    except Exception as exc:  # noqa: BLE001 — a bucket hiccup is "no file", never a broken page
        log.warning("prophesy: could not read %s/%s: %s", folder, safe, exc)
        return None


def put(folder: str, path: str, data: bytes) -> None:
    """Keep one uploaded file (the runner's upload route). ValueError for a folder or path that isn't allowed;
    tabletop.StorageMissing when there is no bucket to keep it in."""
    safe = clean_path(path)
    if folder not in FOLDERS or safe is None:
        raise ValueError("not a file this experiment keeps")
    store = bucket()
    if store is None:
        raise tabletop.StorageMissing("no bucket is configured (TABLETOP_BUCKET)")
    store.put(_key(folder, safe), data, mimetype_for(safe))
    reset_cache()


def finish(folder: str, keep) -> int:
    """After a build's upload: delete the previous build's files that aren't in this one. Builds only (the record grows).
    Returns how many were deleted."""
    if folder not in ARMS:
        raise ValueError("only a build's folder is replaced")
    wanted = {p for p in keep if isinstance(p, str) and clean_path(p)}
    store = bucket()
    if store is None:
        raise tabletop.StorageMissing("no bucket is configured (TABLETOP_BUCKET)")
    root = f"{PREFIX}{folder}/"
    gone = 0
    for name in store.list(root):
        if name[len(root):] not in wanted:
            store.delete(name)
            gone += 1
    reset_cache()
    return gone


def mimetype_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return TEXT_TYPES.get(ext) or doomloop.mimetype_for(path)


_cache: dict[str, tuple[float, object]] = {}


def reset_cache() -> None:
    _cache.clear()


def _cached(key: str, ttl: float, load):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = load()
    _cache[key] = (now, value)
    return value


def version(arm: str) -> dict:
    """An arm's current build (only the public fields), or {} when it has none. Cached briefly."""
    def load():
        raw = read(arm, "version.json")
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("build"), str) and _SAFE_ID.match(data["build"]):
            return {k: data[k] for k in VERSION_FIELDS if isinstance(data.get(k), str) and len(data[k]) <= 64}
        return {}
    return _cached("version:" + arm, VERSION_TTL, load) if arm in ARMS else {}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v, limit=2000):
    return v[:limit] if isinstance(v, str) else ""


def turns() -> list[dict]:
    """The record's turns, newest first, reduced to the fields the board shows and checked on the way in (the record is
    the referee repo's own output, but it is still a file: nothing from it is trusted to be the right shape)."""
    def load():
        raw = read(RECORD, "index.json")
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            data = {}
        rows = data.get("turns") if isinstance(data, dict) else None
        out = []
        for t in rows if isinstance(rows, list) else []:
            if not isinstance(t, dict) or t.get("kind") != "turn" or not isinstance(t.get("id"), str) or not _TURN_ID.match(t["id"]):
                continue
            arms = {}
            for name in ARMS:
                a = (t.get("arms") or {}).get(name)
                if not isinstance(a, dict):
                    continue
                grade = a.get("grade") if isinstance(a.get("grade"), dict) else {}
                checks = grade.get("checks") if isinstance(grade.get("checks"), dict) else {}
                shots = [s for s in a.get("shots") or [] if isinstance(s, str) and clean_path(s) and s.startswith(t["id"] + "/" + name + "/shots/")
                         and s.endswith(".png")]
                arms[name] = {"outcome": _text(a.get("outcome"), 60), "minutes": _num(a.get("minutes")), "cost_usd": _num(a.get("cost_usd")),
                              "peak_context": _num(a.get("peak_context")), "compactions": _num(a.get("compactions")),
                              "commits": _num(a.get("commits")), "deploy": _text(a.get("deploy"), 80), "live": a.get("live") is True,
                              "grade_status": _text(grade.get("status"), 20),
                              "checks": {k: _num(v) for k, v in checks.items() if isinstance(k, str) and _num(v) is not None},
                              "shots": shots[:8]}
            cap = t.get("captain") if isinstance(t.get("captain"), dict) else None
            captain = None
            if cap:
                captain = {"note": _text(cap.get("note"), 1000),
                           **{name: {"score": _num((cap.get(name) or {}).get("score")), "note": _text((cap.get(name) or {}).get("note"), 1000)}
                              for name in ARMS if isinstance(cap.get(name), dict)}}
            out.append({"id": t["id"], "n": _num(t.get("n")), "prompt": _text(t.get("prompt"), 2000),
                        "finished_at": _text(t.get("finished_at"), 40), "arms": arms, "captain": captain})
        return sorted(out, key=lambda r: r["id"], reverse=True)
    return _cached("index", INDEX_TTL, load)


def totals(rows: list[dict]) -> dict:
    """Each arm's running score: the Captain's points over the scored turns, and the turns each arm won."""
    out = {name: {"points": 0.0, "scored": 0, "wins": 0} for name in ARMS}
    for r in rows:
        cap = r.get("captain") or {}
        scores = {name: (cap.get(name) or {}).get("score") for name in ARMS}
        if any(s is None for s in scores.values()):
            continue
        for name, s in scores.items():
            out[name]["points"] += s
            out[name]["scored"] += 1
        best = max(scores.values())
        winners = [n for n, s in scores.items() if s == best]
        if len(winners) == 1:
            out[winners[0]]["wins"] += 1
    return out


def short_commit(info: dict) -> str:
    """The commit for the badge above a build: it comes from a file, so only a strictly safe id is shown."""
    commit = info.get("commit", "")
    return commit[:7] if isinstance(commit, str) and _SAFE_ID.match(commit) else ""
