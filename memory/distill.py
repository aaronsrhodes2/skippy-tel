"""Tag each conversation with topics + a one-line summary using the local LLM.

Runs against the same Ollama SkippyTel already uses (default tier,
qwen2.5:7b), so no conversation text leaves the Captain's hardware. Only
conversations without topics are processed unless `redo=True`.

Stdlib HTTP on purpose: the ingest tool runs on the host (PC or Mac),
outside the flask-api container, so it can't import app.clients.ollama.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from memory.store import Archive, slugify, update_front_matter

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
MAX_CHARS = 6000
MAX_TOPICS = 3

_PROMPT = """You label conversations for a personal archive.
Return JSON only: {{"topics": [...], "summary": "..."}}
- topics: 1 to {max_topics} short lowercase labels (1-2 words each). Reuse an
  existing label when it fits. Existing labels: {known}
- summary: one sentence, max 25 words, saying what was discussed or decided.

Title: {title}
Conversation:
{body}
"""


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    body = text.split("---\n", 2)[-1]
    if len(body) <= MAX_CHARS:
        return body
    half = MAX_CHARS // 2
    return body[:half] + "\n[...]\n" + body[-half:]


def _clean_topics(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        topic = re.sub(r"\s+", " ", str(item).strip().lower())
        if topic and slugify(topic) != "untitled" and topic not in out:
            out.append(topic)
    return out[:MAX_TOPICS]


def label(title: str, body: str, known: list[str], url: str, model: str) -> dict:
    prompt = _PROMPT.format(max_topics=MAX_TOPICS, known=", ".join(known) or "(none yet)",
                            title=title, body=body)
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "format": "json",
                         "stream": False, "options": {"temperature": 0}}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        answer = json.loads(json.load(resp)["response"])
    return {"topics": _clean_topics(answer.get("topics")),
            "summary": str(answer.get("summary", "")).strip()}


def distill(archive: Archive, url: str = DEFAULT_URL, model: str = DEFAULT_MODEL,
            redo: bool = False, log=print) -> int:
    entries = archive.entries()
    known = sorted({t for _, meta in entries for t in meta.get("topics") or []})
    done = 0
    for rel, meta in entries:
        if meta.get("topics") and not redo:
            continue
        path = archive.root / rel
        try:
            result = label(meta.get("title", ""), _body(path), known, url, model)
        except (OSError, ValueError, KeyError) as exc:
            log(f"  distill failed for {rel}: {exc}")
            continue
        update_front_matter(path, **result)
        known = sorted(set(known) | set(result["topics"]))
        done += 1
        log(f"  {rel.name}: {', '.join(result['topics'])}")
    return done
