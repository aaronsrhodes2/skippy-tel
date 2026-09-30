"""What the portal records about how it's used, and where it goes.

Recorded (and only this: /privacy is rendered from RECORDED and NOT_RECORDED below, so the
page can't drift from the code):
  page    a signed-in person opened a page (endpoint and path, never the query string)
  action  a signed-in person did something: every non-GET request, by endpoint, with the NAMES of
          the form fields and, for a short allow-list, their values (ids and switches, never text)
  ui      a tap on something marked data-tel (a tile, a demo), sent by the page's own script
  chat    a message to Skippy and its reply, the model, the tools used (arguments, never results)
  error   a signed-in person hit an error page

The portal is stateless (one Cloud Run instance), so it only keeps the newest events in memory.
skippy-pc's home reporter pulls them (GET /home/telemetry) into a SQLite database on the Captain's
own disk and the portal drops what has been collected; tools/portal_telemetry.py reads it. If home
is offline for long enough that the buffer fills, the oldest events are lost, and `dropped` counts them.
"""
from __future__ import annotations

import collections
import re
import secrets
import threading
import time

KEEP_DAYS = 180           # how long skippy-pc keeps events (home-reporter prunes; /privacy says so)
MAX_EVENTS = 5000         # what the portal holds while waiting for home to collect

RECORDED = (
    ("Pages you open", "which page (its name and address, without any query string), when, and whether it worked."),
    ("Things you do", "each button or form you submit: what it was and whether it worked. The names of the form "
                      "fields, and for a few (which games to start, a filter, an on/off switch) their values; "
                      "never passwords, tokens or free text."),
    ("Taps on tiles and demos", "which tile or demo you tapped, so the Captain can see what gets used and what doesn't."),
    ("Your chats with Skippy", "what you said, what Skippy answered, which model and tools were used and with what "
                               "arguments, and how long it took. Obvious secrets (API keys, tokens, "
                               "'password: …') are scrubbed first, but don't type secrets into the chat."),
    ("Errors", "when a page you opened failed."),
)
NOT_RECORDED = (
    "Your IP address or browser details.",
    "Anything about visitors who aren't signed in.",
    "What a tool returned to Skippy (join codes, server logs, passwords).",
    "The values of server settings, and any password.",
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
        event = {"ts": round(self.clock(), 3), "user": user, "role": role, "sid": sid, "kind": kind,
                 "name": _short(name, 80), "detail": clean(detail)}
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
    r"AKIA[0-9A-Z]{16}", r"AIza[0-9A-Za-z_\-]{30,}", r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    r"(?i)bearer\s+[A-Za-z0-9._~+/=\-]{16,}",
)]
_LABELLED = re.compile(r"(?i)\b(password|passwd|pwd|passphrase|secret|token|api[_\- ]?key)\b(\s*(?:is|=|:)\s*)(\S+)")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
LIMITS = {"message": 4000, "reply": 8000}       # the chat's own text; everything else is short
DEFAULT_LIMIT = 300


def redact(text: str) -> str:
    """Best effort: scrub keys, bearer tokens, 'password: x' and long token-looking strings."""
    for pattern in _SECRETS:
        text = pattern.sub("[redacted]", text)
    text = _LABELLED.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    return _LONG_TOKEN.sub("[redacted]", text)


def _short(text, limit: int) -> str:
    return redact(str(text))[:limit]


def clean(value, key: str = "", depth: int = 0):
    """JSON-safe, size-capped, redacted. Keys named in LIMITS may be long; other strings are short."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _short(value, LIMITS.get(key, DEFAULT_LIMIT))
    if isinstance(value, dict) and depth < 3:
        return {str(k)[:40]: clean(v, str(k), depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, (list, tuple, set)) and depth < 3:
        return [clean(v, key, depth + 1) for v in list(value)[:30]]
    return _short(value, DEFAULT_LIMIT)
