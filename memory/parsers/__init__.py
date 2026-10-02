"""Export parsers. Adding a source = one module with SOURCE, looks_like(), parse().

Planned, added as the Captain exports each service: chatgpt, gemini_takeout.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Iterator

from memory.parsers import claude, claude_code, grok
from memory.schema import Conversation

PARSERS = {p.SOURCE: p for p in (claude, claude_code, grok)}

# The file inside an export that holds the conversations, per source.
_EXPORT_FILES = ("conversations.json",)


_MAX_JSON = 500_000_000        # a JSON file inside an export bigger than this is not read (json.load holds it all in RAM)


def _recognised(data: Any) -> bool:
    return any(hasattr(p, "looks_like") and p.looks_like(data) for p in PARSERS.values())


def _load_json(path: Path) -> Any:
    """The conversations inside an export. Claude names its file conversations.json; Grok's has another name, so a zip
    or folder without one is searched: the first JSON file a parser recognises."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".json")]
            names.sort(key=lambda n: (Path(n).name not in _EXPORT_FILES, n))
            for name in names:
                if zf.getinfo(name).file_size > _MAX_JSON:
                    continue
                with zf.open(name) as fh:
                    try:
                        data = json.load(fh)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                if Path(name).name in _EXPORT_FILES or _recognised(data):
                    return data
        raise ValueError(f"{path}: no conversations file a parser recognises inside the zip")
    if path.is_dir():
        for name in _EXPORT_FILES:
            if (path / name).exists():
                path = path / name
                break
        else:
            for candidate in sorted(path.rglob("*.json")):
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if _recognised(data):
                    return data
            raise ValueError(f"{path}: no conversations file a parser recognises in the folder")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load(path: Path, source: str | None = None) -> Iterator[Conversation]:
    """Parse an export (zip, folder, or json file). Source is auto-detected
    unless given explicitly."""
    # Claude Code transcripts are a folder tree of JSONL files, not one JSON export.
    if source == claude_code.SOURCE or (
            source is None and path.is_dir() and not any(
                (path / n).exists() for n in _EXPORT_FILES) and claude_code.looks_like_path(path)):
        return claude_code.parse_path(path)
    data = _load_json(path)
    if source:
        parser = PARSERS.get(source)
        if parser is None:
            raise ValueError(f"unknown source {source!r}; known: {', '.join(PARSERS)}")
    else:
        parser = next((p for p in PARSERS.values()
                       if hasattr(p, "looks_like") and p.looks_like(data)), None)
        if parser is None:
            raise ValueError(f"{path}: unrecognised export format; pass --source")
    return parser.parse(data)
