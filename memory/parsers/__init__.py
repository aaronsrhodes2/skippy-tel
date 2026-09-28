"""Export parsers. Adding a source = one module with SOURCE, looks_like(), parse().

Planned, added as the Captain exports each service: chatgpt, grok, gemini_takeout.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Iterator

from memory.parsers import claude
from memory.schema import Conversation

PARSERS = {p.SOURCE: p for p in (claude,)}

# The file inside an export that holds the conversations, per source.
_EXPORT_FILES = ("conversations.json",)


def _load_json(path: Path) -> Any:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if Path(name).name in _EXPORT_FILES:
                    with zf.open(name) as fh:
                        return json.load(fh)
        raise ValueError(f"{path}: no {' / '.join(_EXPORT_FILES)} inside the zip")
    if path.is_dir():
        for name in _EXPORT_FILES:
            if (path / name).exists():
                path = path / name
                break
        else:
            raise ValueError(f"{path}: no {' / '.join(_EXPORT_FILES)} in folder")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load(path: Path, source: str | None = None) -> Iterator[Conversation]:
    """Parse an export (zip, folder, or json file). Source is auto-detected
    unless given explicitly."""
    data = _load_json(path)
    if source:
        parser = PARSERS.get(source)
        if parser is None:
            raise ValueError(f"unknown source {source!r}; known: {', '.join(PARSERS)}")
    else:
        parser = next((p for p in PARSERS.values() if p.looks_like(data)), None)
        if parser is None:
            raise ValueError(f"{path}: unrecognised export format; pass --source")
    return parser.parse(data)
