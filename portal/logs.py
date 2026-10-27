"""The portal as the house's log intake (docs/status-design.md, Part 2): every machine ships its important lines here, crash data first.

One record shape, bounded and cleaned at the door (a record can never carry a secret in, or a page of text), kept in a ring in memory (the
newest MAX_RECORDS) for the Status page, and printed as one structured JSON line each so Cloud Logging keeps them for 30 days with no
new service. The forever-archive bucket comes later (it needs a bootstrap apply: skippy-core can do that now).

    {"ts": "2026-10-27T21:04:05Z", "host": "skippy-pc", "source": "docker/skippytel-tts-studio", "level": "error", "kind": "crash",
     "msg": "CUDA out of memory ...", "trace": "chat-1f3a", "fields": {"exit_code": 137}}

Metadata only for chat records: what happened and how long it took, never what was said (a guest's chat must not outlive its 30 days here).
"""
from __future__ import annotations

import collections
import json
import re
import threading
import time
from datetime import datetime, timezone

MAX_RECORDS = 20_000
MAX_BATCH = 500
MAX_MSG = 2_000
HOSTS = ("skippy-pc", "skippy-core", "games-vm", "portal")
LEVELS = ("debug", "info", "warn", "error")
KINDS = ("crash", "error", "warn", "info", "dev", "experiment", "chat")

_SECRETS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "sk-…"),                                            # API keys (Anthropic, OpenAI style)
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "<jwt>"),     # a JWT
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 <redacted>"),
    (re.compile(r"(?i)\b((?:api[_-]?key|apikey|token|secret|password|passwd|pwd|authorization|x-api-key)\s*[=:]\s*)[^\s,;'\"}]{4,}"), r"\1<redacted>"),
    (re.compile(r"\b[0-9a-f]{40,}\b"), "<hex>"),                                               # a long hash or key in hex
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "<google-key>"),
]

_SECRET_NAME = re.compile(r"(?i)token|secret|passw|pwd|api[_-]?key|authorization|credential|cookie")


def redact(text: str) -> str:
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


def _iso(value) -> str:
    """A timestamp as UTC ISO seconds: the record's own when it is one, else now (never trust a far-off clock)."""
    now = time.time()
    if isinstance(value, (int, float)) and not isinstance(value, bool) and now - 7 * 86400 < value < now + 300:
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str):
        try:
            got = datetime.fromisoformat(value.replace("Z", "+00:00"))
            ts = got.timestamp() if got.tzinfo else got.replace(tzinfo=timezone.utc).timestamp()
            if now - 7 * 86400 < ts < now + 300:
                return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(raw, host: str) -> dict | None:
    """One incoming record -> the stored shape, or None when it is junk. Secrets are removed here, before anything is kept or printed."""
    if not isinstance(raw, dict):
        return None
    msg = raw.get("msg")
    if not isinstance(msg, str) or not msg.strip():
        return None
    level = raw.get("level") if raw.get("level") in LEVELS else "info"
    kind = raw.get("kind") if raw.get("kind") in KINDS else ("error" if level == "error" else "warn" if level == "warn" else "info")
    fields = {}
    for k, v in list((raw.get("fields") or {}).items())[:12] if isinstance(raw.get("fields"), dict) else []:
        name = str(k)[:30]
        if _SECRET_NAME.search(name) and v not in (None, "", 0, False):
            fields[name] = "<redacted>"                           # a field NAMED like a secret never keeps its value, whatever it looks like
        elif isinstance(v, (int, float, bool)) or v is None:
            fields[name] = v
        elif isinstance(v, str):
            fields[name] = redact(v)[:200]
    return {"ts": _iso(raw.get("ts")), "host": host, "source": redact(str(raw.get("source") or "unknown"))[:80], "level": level, "kind": kind,
            "msg": redact(msg.strip())[:MAX_MSG], "trace": re.sub(r"[^A-Za-z0-9_.:-]", "", str(raw.get("trace") or ""))[:40], "fields": fields}


class LogStore:
    def __init__(self, emit=None, clock=time.time):
        self._ring: collections.deque = collections.deque(maxlen=MAX_RECORDS)
        self._lock = threading.Lock()
        self.clock = clock
        self.emit = emit if emit is not None else self._print      # tests pass their own; the portal prints one JSON line a record
        self.received: dict[str, float] = {}                       # host -> when it last shipped (a quiet host is a fact worth showing)

    @staticmethod
    def _print(rec: dict) -> None:
        severity = {"error": "ERROR", "warn": "WARNING", "info": "INFO", "debug": "DEBUG"}[rec["level"]]
        print(json.dumps({"severity": severity, "message": f"[{rec['host']}] {rec['source']}: {rec['msg']}", "event": "house-log", **rec}), flush=True)

    def add(self, host: str, batch) -> int:
        """Take a machine's batch (at most MAX_BATCH records); returns how many were kept. `host` is who the door says is calling."""
        if host not in HOSTS:
            raise ValueError("unknown host")
        kept = []
        for raw in (batch if isinstance(batch, list) else [])[:MAX_BATCH]:
            rec = clean(raw, host)
            if rec:
                kept.append(rec)
        with self._lock:
            self._ring.extend(kept)
            self.received[host] = self.clock()
        for rec in kept:
            self.emit(rec)
        return len(kept)

    def note(self, source: str, level: str, msg: str, kind: str = "", trace: str = "", **fields) -> None:
        """The portal's own line (host "portal")."""
        self.add("portal", [{"source": source, "level": level, "msg": msg, "kind": kind or level, "trace": trace, "fields": fields}])

    def query(self, host: str = "", source: str = "", kind: str = "", level: str = "", q: str = "", trace: str = "", limit: int = 200) -> list[dict]:
        """Newest first. `source` matches as a prefix ("docker/" = every container); `q` is a case-insensitive substring of the message."""
        q = q.lower()
        out = []
        with self._lock:
            rows = list(self._ring)
        for rec in reversed(rows):
            if host and rec["host"] != host or kind and rec["kind"] != kind or level and rec["level"] != level or trace and rec["trace"] != trace:
                continue
            if source and not rec["source"].startswith(source):
                continue
            if q and q not in rec["msg"].lower():
                continue
            out.append(rec)
            if len(out) >= max(1, min(limit, 1000)):
                break
        return out

    def activity(self, minutes: int = 15) -> dict[str, dict]:
        """source -> {n, errors, last}: what each source said lately, for the lamps and the container cards."""
        cutoff = datetime.fromtimestamp(self.clock() - minutes * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: dict[str, dict] = {}
        with self._lock:
            rows = list(self._ring)
        for rec in reversed(rows):
            if rec["ts"] < cutoff:
                continue
            a = out.setdefault(f"{rec['host']}|{rec['source']}", {"n": 0, "errors": 0, "last": rec["ts"]})
            a["n"] += 1
            a["errors"] += rec["level"] == "error"
        return out
