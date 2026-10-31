"""Chat names (the Captain's convention, 2026-10-23; lengthened 2026-10-31): a chat is named like a short sentence, four to six words,
noun-verb-adjective-noun for a simple request and a word or two longer when the request has two parts ("the voice keys are a mess, I want
two modes" -> "voice-needs-simpler-modes"; "make the chat names longer and smarter" -> "chat-names-get-longer-and-smarter"), a compression of
its first prompt, never the first thing said. Skippy Core writes the name; when it can't be reached the name is the prompt's first
content words (up to five), so a chat always has one.

ONE FILE, TWO COPIES: portal/chat_names.py (the portal's chats) and services/claude-bridge/chat_names.py (the agent's
conversations) are the same file (portal/tests/test_chat_names.py fails if they differ). No imports beyond the standard library.
"""
from __future__ import annotations

import re

SYSTEM = ("You name chats. Compress the user's request into four to six lowercase English words joined by hyphens, saying what the chat is "
          "about, shaped like a short sentence: noun-verb-adjective-noun when the request is one simple thing, and one or two words longer "
          "(a second noun, a joining word) when it has two parts. Never fewer than four words, never more than six. Examples: "
          "portal-needs-faster-pictures, skippy-draws-red-barn, voice-needs-simpler-modes, tests-catch-flaky-recovery, "
          "chat-names-get-longer-and-smarter, pod-face-streams-smoothly-from-pc. Answer with the name only.")
_NAME = re.compile(r"(?<![a-z-])\b([a-z]{2,20}(?:-[a-z]{2,20}){3,5})\b(?!-[a-z])")
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
    """Without Skippy Core: the prompt's first content words (up to five, at least four: padded with "chat"), hyphenated."""
    words = [w for w in re.findall(r"[a-z]{2,20}", str(message or "").lower()) if w not in _STOP]
    words = words[:5]
    return "-".join(words + ["chat"] * (4 - len(words)))


def name(message: str, ask) -> str:
    """A chat's name from its first prompt. `ask(system, text) -> str` asks Skippy Core; any failure means the fallback."""
    text = " ".join(str(message or "").split())[:1500]
    try:
        got = from_reply(ask(SYSTEM, text + " /no_think"))       # /no_think: Qwen3's switch for a quick, thought-free answer
    except Exception:  # noqa: BLE001 — Core down, slow, or refusing: a chat still gets a name
        got = None
    return got or fallback(text)
