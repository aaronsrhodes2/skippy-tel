"""Source-neutral conversation schema.

Every parser (Claude, ChatGPT, Grok, Gemini) converts its export into these
dataclasses; everything downstream (writer, index, distill) only knows these.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    role: str                      # "user" | "assistant" | "system"
    text: str
    created: datetime | None = None
    attachments: list[str] = field(default_factory=list)   # file names only


@dataclass
class Conversation:
    source: str                    # "claude" | "chatgpt" | "grok" | "gemini"
    id: str                        # stable id from the source; dedupe key
    title: str
    started: datetime
    updated: datetime
    messages: list[Message]
    device: str = "unknown"        # exports rarely say; parsers fill when they can
    url: str | None = None
    topics: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"
