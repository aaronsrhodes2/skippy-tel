"""The pop-up registry (docs/popups.md): an on-demand model joins Skippy by ONE entry and its own container, never by editing shared code.

Entries live in services/popups/registry.json (the plumbing's own test pop-up, `echo`) and in services/<family>-popups/popups.json (one
file per family, so families never edit the same file). Each entry becomes, with no other change: a capability the home reporter
advertises while the service's /health says ready, a patient job (kind "popup") on its machine, a chat tool for whoever its audience
allows, and a `SKIPPY_POPUP: <name> <json>` line the private Skippy may write.

The image carries the files under popups_src/ (portal/Dockerfile); tests and dev read them from the repo. A bad entry is skipped with a
log line, never a crash: tests/test_popups.py fails on one in the repo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOTS = (HERE / "popups_src", HERE.parent)          # the image's copy, then the repo (tests, dev)

NAME = re.compile(r"[a-z][a-z0-9_]{2,39}")
URL_ENV = re.compile(r"[A-Z][A-Z0-9_]{1,60}_URL")
ENDPOINT = re.compile(r"/[A-Za-z0-9_./-]{0,100}")
# Names the portal or the reporter already use for something else: a pop-up can't take them (a tool or capability would collide).
RESERVED = {"server_status", "start_game", "stop_server", "server_logs", "restart_server", "ask_mentat", "now_playing", "make_music",
            "music_result", "make_image", "make_image_hq", "image_result", "popup_result", "workshop", "music_gate", "video_gate",
            "voice_gate", "popup", "doctor_deep", "host_op", "memory_list", "memory_get", "mcp_list", "mcp_test"}
# What a pop-up may hand back: the patient queue keeps exactly these (patient.TYPES), the chat's card shows each (_chat.html).
OUTPUT_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
                "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/flac": "flac",
                "model/gltf-binary": "glb", "video/mp4": "mp4", "application/json": "json", "text/plain": "txt"}
# What a pop-up may take in: a file the person attached to the message (attachments.py checks it), or the agent named.
INPUT_TYPES = {"image/png", "image/jpeg", "image/webp", "audio/mpeg", "audio/wav", "audio/ogg", "audio/flac"}
MAX_TIMEOUT_S = 7200                                 # the slowest machine's longest job (the reporter's IMAGE_HQ_TIMEOUT)


def matches(ctype: str, patterns) -> bool:
    """`audio/wav` matches `audio/wav` or `audio/*`."""
    ctype = (ctype or "").lower()
    return any(p == ctype or (p.endswith("/*") and ctype.startswith(p[:-1])) for p in patterns or ())


def card_kind(ctype: str) -> str:
    """How the chat shows a result of this type: image, audio, video, model (a 3D viewer) or text (a JSON or text block)."""
    ctype = (ctype or "").lower()
    if ctype == "model/gltf-binary":
        return "model"
    for head in ("image", "audio", "video"):
        if ctype.startswith(head + "/"):
            return head
    return "text"


def check(entry) -> str | None:
    """Why this entry can't be used, or None."""
    if not isinstance(entry, dict):
        return "not an object"
    name = entry.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return f"name {name!r}: lower-case letters, digits and _, 3 to 40"
    if name in RESERVED:
        return f"name {name!r} is already taken"
    if not isinstance(entry.get("family"), str) or not entry["family"].strip():
        return "family missing"
    if entry.get("box") not in ("pc", "core"):
        return "box must be pc or core"
    if entry.get("tier") not in ("quick", "quality"):
        return "tier must be quick or quality"
    if not isinstance(entry.get("url_env"), str) or not URL_ENV.fullmatch(entry["url_env"]):
        return "url_env must be an env var name ending in _URL"
    if "url" in entry and not (isinstance(entry["url"], str) and re.fullmatch(r"https?://[A-Za-z0-9.:_-]+", entry["url"])):
        return "url must be http(s)://host:port"
    if not isinstance(entry.get("endpoint"), str) or not ENDPOINT.fullmatch(entry["endpoint"]):
        return "endpoint must be a path"
    inputs = entry.get("inputs")
    if not isinstance(inputs, dict) or inputs.get("type") != "object" or not isinstance(inputs.get("properties", {}), dict):
        return "inputs must be a JSON schema of type object"
    if "attachment" in inputs.get("properties", {}):
        return "inputs may not name a property `attachment` (the plumbing adds it for media_in)"
    outputs = entry.get("outputs")
    if not isinstance(outputs, list) or not outputs or not all(isinstance(o, str) for o in outputs):
        return "outputs must be a list of types"
    for o in outputs:
        if o not in OUTPUT_TYPES and not (o.endswith("/*") and any(t.startswith(o[:-1]) for t in OUTPUT_TYPES)):
            return f"output {o!r} isn't a type the chat can keep and show"
    media_in = entry.get("media_in", [])
    if not isinstance(media_in, list) or not all(isinstance(m, str) and any(matches(t, [m]) for t in INPUT_TYPES) for m in media_in):
        return "media_in must list picture or audio types"
    est = entry.get("est_seconds")
    if not isinstance(est, int) or isinstance(est, bool) or not 1 <= est <= MAX_TIMEOUT_S:
        return f"est_seconds must be a whole number, 1 to {MAX_TIMEOUT_S}"
    mem = entry.get("vram_gb", entry.get("mem_gb"))
    if not isinstance(mem, (int, float)) or isinstance(mem, bool) or not 0 <= mem <= 128:
        return "vram_gb (skippy-pc) or mem_gb (skippy-core) must be a number of GB"
    if entry.get("audience") not in ("admin", "everyone"):
        return "audience must be admin or everyone"
    if not isinstance(entry.get("description"), str) or not 10 <= len(entry["description"]) <= 1000:
        return "description: what the brains read, 10 to 1000 characters"
    for flag in ("chat", "media_required"):
        if flag in entry and not isinstance(entry[flag], bool):
            return f"{flag} must be true or false"
    if entry.get("media_required") and not media_in:
        return "media_required needs media_in"
    return None


def files(root: Path) -> list[Path]:
    return [p for p in [root / "services" / "popups" / "registry.json", *sorted((root / "services").glob("*-popups/popups.json"))]
            if p.is_file()]


def _root() -> Path:
    return next((r for r in ROOTS if (r / "services" / "popups" / "registry.json").is_file()), HERE.parent)


_cache: dict = {"key": None, "entries": {}, "problems": []}


def load(root: Path | None = None) -> dict[str, dict]:
    """Every usable entry by name (first one wins on a duplicate). Re-read only when a file changed."""
    root = root or _root()
    paths = files(root)
    key = (str(root), tuple((str(p), p.stat().st_mtime_ns) for p in paths))
    if _cache["key"] == key:
        return _cache["entries"]
    entries, problems = {}, []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8")).get("popups")
        except (OSError, ValueError, AttributeError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        for entry in raw if isinstance(raw, list) else []:
            why = check(entry)
            if why is None and entry["name"] in entries:
                why = f"{entry['name']!r} is registered twice"
            if why:
                problems.append(f"{path.parent.name}/{path.name}: {why}")
                continue
            entries[entry["name"]] = {**entry, "mem_gb": entry.get("mem_gb", entry.get("vram_gb", 0)),
                                      "chat": entry.get("chat", True), "media_in": entry.get("media_in", [])}
    for why in problems:
        print(json.dumps({"event": "popup_registry_problem", "why": why[:300]}), flush=True)
    _cache.update(key=key, entries=entries, problems=problems)
    return entries


def problems(root: Path | None = None) -> list[str]:
    load(root)
    return list(_cache["problems"])


def get(name: str) -> dict | None:
    return load().get(name) if isinstance(name, str) else None


def names() -> tuple[str, ...]:
    return tuple(load())
