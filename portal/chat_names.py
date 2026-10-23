"""Chat names (the Captain's convention, 2026-10-23): a chat is named noun-verb-adjective-noun, a compression of its first prompt
("the voice keys are a mess, I want two modes" -> "voice-needs-simpler-modes"), never the first thing said. Skippy Core writes the
name; when it can't be reached the name is the prompt's first content words, so a chat always has one.

ONE FILE, TWO COPIES: portal/chat_names.py (the portal's chats) and services/claude-bridge/chat_names.py (the agent's
conversations) are the same file (portal/tests/test_chat_names.py fails if they differ). No imports beyond the standard library.
"""
from __future__ import annotations

import re

SYSTEM = ("You name chats. Compress the user's request into exactly four lowercase English words joined by hyphens, in the "
          "order noun-verb-adjective-noun, saying what the chat is about. Examples: portal-needs-faster-pictures, "
          "skippy-draws-red-barn, voice-needs-simpler-modes, tests-catch-flaky-recovery. Answer with the name only.")
_NAME = re.compile(r"\b([a-z]{2,20}-[a-z]{2,20}-[a-z]{2,20}-[a-z]{2,20})\b")
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_STOP = set("""a an the and or but if then else of to in on at by for with from into onto about as is are was were be been being am
i me my we our you your he she it they them their this that these those there here what which who whom whose how why when where
can could would should will shall may might must do does did done have has had not no yes so very just also too please lets let
want need like make get got some any all each more most less much many few one two hey hi hello ok okay skippy now new""".split())


def valid(name: str) -> bool:
    return bool(_NAME.fullmatch(name or ""))


def from_reply(text: str) -> str | None:
    """The name in a model's answer (its thinking dropped), or None if it didn't give one in the right shape."""
    found = _NAME.findall(_THINK.sub(" ", str(text or "")).lower())
    return found[-1] if found else None


def fallback(message: str) -> str:
    """Without Skippy Core: the prompt's first four content words, hyphenated (padded with "chat")."""
    words = [w for w in re.findall(r"[a-z]{2,20}", str(message or "").lower()) if w not in _STOP]
    return "-".join((words + ["chat"] * 4)[:4])


def name(message: str, ask) -> str:
    """A chat's name from its first prompt. `ask(system, text) -> str` asks Skippy Core; any failure means the fallback."""
    text = " ".join(str(message or "").split())[:1500]
    try:
        got = from_reply(ask(SYSTEM, text + " /no_think"))       # /no_think: Qwen3's switch for a quick, thought-free answer
    except Exception:  # noqa: BLE001 — Core down, slow, or refusing: a chat still gets a name
        got = None
    return got or fallback(text)
