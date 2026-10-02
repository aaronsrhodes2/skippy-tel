"""Grok data export → Conversation.

Export: grok.com → Settings → Data Controls → export (everything in the xAI account, the in-car chats too when the car's
Grok is signed in). The zip holds one JSON file (seen as `prod-grok-backend.json`) shaped like
  {"conversations": [{"conversation": {"id", "title", "create_time", "modify_time", ...},
                      "responses": [{"response": {"_id", "message", "sender": "human"|"assistant",
                                                  "create_time", ...}}, ...]}, ...]}
with times as ISO strings or Mongo-style {"$date": {"$numberLong": "<ms>"}} / {"$date": "<iso>"}.

Written before a real export reached this repo, so it is tolerant on purpose: a response may sit under "response" or be
the item itself, the text may be "message" or "text", the sender "human"/"user" or "assistant"/"ASSISTANT"/"grok".
Only the words are kept: no tool or search traffic, no attachments' contents.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

from memory.parsers.common import parse_ts
from memory.schema import Conversation, Message

SOURCE = "grok"
_USER = {"human", "user"}
_ASSISTANT = {"assistant", "grok", "model"}


def looks_like(data: Any) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("conversations"), list) or not data["conversations"]:
        return False
    first = data["conversations"][0]
    return isinstance(first, dict) and "responses" in first


def when(value) -> datetime | None:
    """Every time shape Grok's export has used: ISO, epoch seconds or ms, and Mongo's {"$date": ...}."""
    if isinstance(value, dict):
        value = value.get("$date", value.get("$numberLong"))
        if isinstance(value, dict):
            value = value.get("$numberLong")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
    return parse_ts(value)


def _messages(responses) -> list[Message]:
    out = []
    for item in responses if isinstance(responses, list) else []:
        r = item.get("response", item) if isinstance(item, dict) else None
        if not isinstance(r, dict):
            continue
        sender = str(r.get("sender") or r.get("role") or "").lower()
        role = "user" if sender in _USER else "assistant" if sender in _ASSISTANT else None
        text = r.get("message") if isinstance(r.get("message"), str) else r.get("text")
        if role is None or not isinstance(text, str) or not text.strip():
            continue
        out.append(Message(role=role, text=text.strip(), created=when(r.get("create_time") or r.get("created_at"))))
    out.sort(key=lambda m: m.created or datetime.min.replace(tzinfo=timezone.utc))
    return out


def parse(data: dict) -> Iterator[Conversation]:
    for item in data.get("conversations") or []:
        if not isinstance(item, dict):
            continue
        meta = item.get("conversation") if isinstance(item.get("conversation"), dict) else item
        messages = _messages(item.get("responses"))
        cid = str(meta.get("id") or meta.get("_id") or meta.get("conversation_id") or "")
        if not messages or not cid:
            continue
        started = when(meta.get("create_time")) or messages[0].created
        updated = when(meta.get("modify_time")) or messages[-1].created or started
        if started is None:
            continue
        yield Conversation(
            source=SOURCE,
            id=cid,
            title=(str(meta.get("title") or "").strip() or messages[0].text.splitlines()[0][:80] or "Untitled"),
            started=started,
            updated=max(updated or started, messages[-1].created or started),
            messages=messages,
            url=f"https://grok.com/chat/{cid}",
        )
