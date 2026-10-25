"""Shifting Prophesy: two AI models build the same browser game from the same spec, one prompt at a time (/shifting-prophesy).

The experiment lives in three repos: each model's build (aaronsrhodes2/shifting-prophesy-haiku, -purple) and the referee's
record of every turn (aaronsrhodes2/shifting-prophesy-referee, which also runs the turns on skippy-pc). Each repo's CI
uploads into its own folder of one GCS bucket (PROPHESY_BUCKET): haiku/ and purple/ hold the newest build of each game,
turns/ holds the record. The portal reads them at request time, as the Doom Loop dev channel does (doomloop.py: the storage,
path and file-type rules are its own), so a push shows up with no portal redeploy. Nothing here talks to an LLM.

    haiku/, purple/   index.html, version.json (written last, like Doom Loop's), and whatever the build loads
    turns/            index.json (one row per turn, written last) and turns/<NNN>/: turn.json, report.md, reply.md and
                      actions.txt per arm, and <arm>/shots/*.png

Every page and file is for signed-in people; each build's /version is public (the referee's runner reads it).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import doomloop
from doomloop import VERSION_FIELDS, clean_path

log = logging.getLogger(__name__)

BUCKET_ENV = "PROPHESY_BUCKET"
ARMS = {
    "haiku": {"label": "Haiku 5.5", "detail": "claude-haiku-5-5 through the Anthropic API",
              "repo": "https://github.com/aaronsrhodes2/shifting-prophesy-haiku"},
    "purple": {"label": "Purple Skippy", "detail": "Skippy Core's coder (Qwen3.6-35B-A3B), local and free",
               "repo": "https://github.com/aaronsrhodes2/shifting-prophesy-purple"},
}
RECORD = "turns"
VERSION_TTL = 10.0           # seconds a build's version.json is trusted before its folder is asked again
INDEX_TTL = 10.0             # and the record's index.json
_SAFE_ID = re.compile(r"^[0-9A-Za-z._-]{1,64}$")
_TURN_ID = re.compile(r"^\d{3}$")
TEXT_TYPES = {".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


def get_store(folder: str):
    """One folder of the experiment's bucket (an arm's build or the record), or None when no bucket is configured."""
    bucket = os.environ.get(BUCKET_ENV, "").strip()
    if not bucket or folder not in (*ARMS, RECORD):
        return None
    return doomloop.GcsStore(bucket, folder + "/")


def read(folder: str, path: str) -> bytes | None:
    safe = clean_path(path)
    store = get_store(folder)
    if safe is None or store is None:
        return None
    return store.read(safe)


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
