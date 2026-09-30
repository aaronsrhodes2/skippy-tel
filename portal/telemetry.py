"""What the portal records about how it's used, and where it goes.

Recorded (and only this: /privacy is rendered from RECORDED, NOT_RECORDED and ALSO below, so the
page can't drift from the code):
  page    a signed-in person opened a page (its path, never the query string)
  action  a signed-in person did something: every non-GET request, by endpoint, with the NAMES of
          the form fields and, for a short allow-list (telemetry_web.SAFE_VALUES), their values
          (ids and switches, never text)
  ui      a tap on something marked data-tel (a tile, a demo), sent by the page's own script
  chat    a message to Skippy and its reply, the model, the tools used (arguments, never results)
  error   a signed-in person hit an error page
Every event carries who did it (their Google email, their role, a random id for that sign-in) and when.

The portal is stateless (one Cloud Run instance), so it only keeps the newest events in memory.
skippy-pc's home reporter pulls them (GET /home/telemetry) into a SQLite table (site_events, beside the
phone UI's clicks) on the Captain's own disk, and the portal drops what has been collected. If home
is offline for long enough that the buffer fills, the oldest events are lost, and `dropped` counts them.

This module is the recorder and the rules (no Flask in it); telemetry_web.py wires it into the portal.
"""
from __future__ import annotations

import collections
import math
import re
import secrets
import threading
import time

KEEP_DAYS = 180           # how long skippy-pc keeps events (flask-api prunes; /privacy says so)
MAX_EVENTS = 5000         # what the portal holds while waiting for home to collect
MAX_PULL = 500            # most events one pull returns

RECORDED = (
    ("Who you are", "your Google email address, whether you are the Captain, a core user or a friend, and a random "
                    "code for each sign-in so a visit reads as one visit. It is attached to everything below."),
    ("Pages you open", "which page (its address, without any query string), when, whether it loaded and how long it took."),
    ("Things you do", "each button or form you submit: which one it was and whether it worked. For the form "
                      "fields, their names; and their values only for a short fixed list (which games you ticked, "
                      "an idle-minutes number, which experiment or which music control, a yes/no flag). "
                      "Never passwords, tokens or anything you typed."),
    ("Taps on tiles and demos", "which tile or demo you tapped and which page it was on, so the Captain can see "
                                "what gets used and what doesn't."),
    ("Your chats with Skippy", "what you said, what Skippy answered, which model and tools were used and with what "
                               "arguments, and how long it took. Obvious secrets (API keys, tokens, "
                               "'password: …') are scrubbed first, but don't type secrets into the chat."),
    ("Errors", "when a page you opened failed."),
)
NOT_RECORDED = (
    "Your IP address or browser details.",
    "Anything about visitors who aren't signed in, or who signed in with Google but aren't on the list.",
    "What a tool returned to Skippy (server logs, invite codes): only what Skippy says about it in his reply is "
    "kept, with invite codes and passwords scrubbed.",
    "The values of server settings, and any password.",
)
ALSO = (        # not part of this recording, but true, and you should know
    "Google Cloud Run, which hosts the portal, keeps its own ordinary request logs, which can include your IP "
    "address. This recording doesn't copy them.",
    "To answer you, Skippy sends your message and the conversation so far to Anthropic (Claude). That happens "
    "whether or not it is recorded.",
    "Signing in goes through Google, which tells the portal your email address and name.",
)


class Telemetry:
    def __init__(self, enabled: bool = True, max_events: int = MAX_EVENTS, clock=time.time):
        self.enabled, self.clock, self._max = enabled, clock, max_events
        self._lock = threading.Lock()
        self._events: collections.deque = collections.deque()
        self._seq = 0
        self.boot = secrets.token_hex(4)      # a restarted portal restarts seq; the collector tells boots apart
        self.dropped = 0
        self.collected_at: float | None = None

    def record(self, kind: str, name: str, user: str = "", role: str = "", sid: str = "", **detail) -> dict | None:
        if not self.enabled:
            return None
        # who: an email (not a secret, and a long one must not be mistaken for a token), so only cut to length
        event = {"ts": round(self.clock(), 3), "user": str(user)[:120], "role": str(role)[:12],
                 "sid": str(sid)[:16], "kind": kind, "name": _short(name, 80), "detail": clean(detail)}
        with self._lock:
            self._seq += 1
            event["seq"], event["boot"] = self._seq, self.boot
            self._events.append(event)
            while len(self._events) > self._max:
                self._events.popleft()
                self.dropped += 1
        return event

    def since(self, boot: str, after: int, limit: int = 500) -> dict:
        """Events home hasn't collected yet. `after` is the last seq it has stored *for this boot*; that
        also acknowledges everything up to it, which is then dropped from memory."""
        limit = max(1, min(int(limit), MAX_PULL))
        with self._lock:
            if boot == self.boot:
                while self._events and self._events[0]["seq"] <= after:
                    self._events.popleft()
            else:
                after = 0                      # it's talking about a previous portal process: start from the top
            pending = [e for e in self._events if e["seq"] > after]
            self.collected_at = self.clock()
            return {"boot": self.boot, "events": pending[:limit], "more": len(pending) > limit}

    def recent(self, n: int = 20) -> list[dict]:
        with self._lock:
            return list(self._events)[-n:][::-1]

    def status(self) -> dict:
        with self._lock:
            kinds = collections.Counter(e["kind"] for e in self._events)
            return {"enabled": self.enabled, "waiting": len(self._events), "dropped": self.dropped,
                    "kinds": dict(kinds), "last_collected_s": None if self.collected_at is None
                    else int(self.clock() - self.collected_at)}


# ── keeping secrets and bulk out of what's stored ────────────────────────────

_SECRETS = [re.compile(p) for p in (
    r"sk-ant-[A-Za-z0-9_\-]{16,}", r"sk-[A-Za-z0-9]{20,}", r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
    r"AKIA[0-9A-Z]{16}", r"AIza[0-9A-Za-z_\-]{30,}", r"xox[baprs]-[A-Za-z0-9\-]{10,}", r"ya29\.[A-Za-z0-9_\-]{20,}",
    r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",                         # a JWT
    r"[MN][A-Za-z0-9]{23,}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}",                            # a Discord bot token
    r"(?i)bearer\s+[A-Za-z0-9._~+/=\-]{16,}",
)]
# "password: x", "password is x", "the valheim password is x", "password for valheim is x", "set password to x",
# VALHEIM_PASSWORD=x, {"token": "x"}, api_key = 'a b'. The label may sit inside a NAME_LIKE_THIS (a word boundary
# does not split a_password), which is why the label is preceded by optional dash/underscore/dot-joined parts.
_LABEL = r"(?:pass(?:word|wd|phrase|code)|pwd|pw|secret|token|api[_\-]?key|apikey|credential)s?"
_LABELLED = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_.\-])*" + _LABEL + r"(?:[_.\-][A-Za-z0-9]+)*[\"']?)"
    r"((?:\s+(?:for|of|on|at)\s+(?:the\s+)?[\w\-]+)?\s*(?:[:=]|\bis\b|\bto\b)\s*)"
    r"(\"[^\"\n]*\"|'[^'\n]*'|\S+)")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
_SENSITIVE_KEY = re.compile(r"(?i)pass|secret|token|api[_\-]?key|authorization|cookie|credential")
LIMITS = {"message": 4000, "reply": 8000}       # the chat's own text; everything else is short
DEFAULT_LIMIT = 300


def redact(text: str) -> str:
    """Best effort: scrub keys, bearer tokens, 'password: x' and long token-looking strings."""
    for pattern in _SECRETS:
        text = pattern.sub("[redacted]", text)
    text = _LABELLED.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    return _LONG_TOKEN.sub("[redacted]", text)


def scrub_values(text: str, values) -> str:
    """Take exact secret values (an invite code a tool returned, that Skippy then repeated) out of `text`."""
    for value in sorted({str(v) for v in values if v is not None}, key=len, reverse=True):
        if len(value) >= 4:
            text = text.replace(value, "[redacted]")
    return text


def secret_values(results) -> set[str]:
    """The values in tool results that are codes or passwords, so a reply that repeats one can be scrubbed.
    `results` are the tools' JSON outputs; they are read here and never stored."""
    import json
    found: set[str] = set()

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, (str, int)) and not isinstance(node, bool) and (_SENSITIVE_KEY.search(key) or
                                                                             re.search(r"(?i)code", key)):
            found.add(str(node))

    for raw in results:
        try:
            walk(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return found


def _short(text, limit: int) -> str:
    return redact(str(text))[:limit]


def clean(value, key: str = "", depth: int = 0):
    """JSON-safe, size-capped, redacted. Keys named in LIMITS may be long; other strings are short.
    Anything under a key that sounds secret (password, token, cookie…) is dropped whatever it holds."""
    if value is None or value is False or value == "":
        return value
    if _SENSITIVE_KEY.search(key):
        return "[redacted]"
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else None     # NaN is not JSON
    if isinstance(value, str):
        return _short(value, LIMITS.get(key, DEFAULT_LIMIT))
    if isinstance(value, dict) and depth < 5:
        return {str(k)[:40]: clean(v, str(k), depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, (list, tuple, set)) and depth < 5:
        return [clean(v, key, depth + 1) for v in list(value)[:30]]
    return _short(value, DEFAULT_LIMIT)
