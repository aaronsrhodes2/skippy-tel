"""Claude data export → Conversation.

Export: claude.ai → Settings → Privacy → Export data. The emailed zip holds
`conversations.json`: a list of
  {uuid, name, created_at, updated_at,
   chat_messages: [{uuid, sender: "human"|"assistant", text, created_at,
                    content: [{type: "text"|"thinking"|"tool_use"|..., text?}],
                    attachments: [{file_name}], files: [{file_name}]}]}

Only `text` content blocks are kept; thinking and tool traffic are dropped
because they are noise for recall. `text` on the message is the fallback
for older exports that have no `content` list.
"""
from __future__ import annotations

from typing import Any, Iterator

from memory.parsers.common import parse_ts
from memory.schema import Conversation, Message

SOURCE = "claude"
_ROLES = {"human": "user", "assistant": "assistant"}


def looks_like(data: Any) -> bool:
    return (
        isinstance(data, list)
        and bool(data)
        and isinstance(data[0], dict)
        and "chat_messages" in data[0]
    )


def _message_text(msg: dict) -> str:
    blocks = msg.get("content") or []
    parts = [b.get("text", "") for b in blocks
             if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
    if parts:
        return "\n\n".join(parts).strip()
    return (msg.get("text") or "").strip()


def _attachments(msg: dict) -> list[str]:
    names = []
    for key in ("attachments", "files"):
        for item in msg.get(key) or []:
            name = item.get("file_name") if isinstance(item, dict) else None
            if name:
                names.append(name)
    return names


def parse(data: list[dict]) -> Iterator[Conversation]:
    for conv in data:
        messages = []
        for msg in conv.get("chat_messages") or []:
            text = _message_text(msg)
            files = _attachments(msg)
            if not text and not files:
                continue
            messages.append(Message(
                role=_ROLES.get(msg.get("sender"), msg.get("sender") or "unknown"),
                text=text,
                created=parse_ts(msg.get("created_at")),
                attachments=files,
            ))
        if not messages:
            continue

        started = parse_ts(conv.get("created_at")) or messages[0].created
        updated = parse_ts(conv.get("updated_at")) or messages[-1].created or started
        uuid = conv["uuid"]
        yield Conversation(
            source=SOURCE,
            id=uuid,
            title=(conv.get("name") or "").strip() or "Untitled",
            started=started,
            updated=updated,
            messages=messages,
            url=f"https://claude.ai/chat/{uuid}",
        )
