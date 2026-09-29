"""conversation_store.py — persistent chat log + SSE fanout for the web portal.

Every message that flows through /intent/unmatched (both the user turn and the
assistant reply) is recorded here.  The portal's SSE stream fans them out to all
connected browser tabs in real time.

Schema
------
messages table keeps the last _MAX_ROWS rows.  image_b64 is stored but never
returned in list payloads — the portal fetches /portal/image/<id> on demand.
"""
import json
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

# Mounted volume inside Docker — same parent as keyword_memory.db
DB_PATH   = Path("/drive-state/portal_chat.db")
_MAX_ROWS = 500          # prune older rows on insert

_lock        = threading.Lock()
_subscribers: list[queue.Queue] = []


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           INTEGER NOT NULL,
    role         TEXT    NOT NULL,   -- 'user' | 'assistant'
    text         TEXT    NOT NULL,
    tier         TEXT,               -- 'local' | 'cloud' | 'nagatha' | …
    narration    TEXT,               -- italic dim-green header above reply
    image_b64    TEXT,               -- base64 PNG (stored; not in list payloads)
    image_prompt TEXT,
    source       TEXT                -- 'phone' | 'portal'
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(_DDL)
        conn.commit()


# ── Write ─────────────────────────────────────────────────────────────────────

def record(
    role:         str,
    text:         str,
    tier:         Optional[str] = None,
    narration:    Optional[str] = None,
    image_b64:    Optional[str] = None,
    image_prompt: Optional[str] = None,
    source:       Optional[str] = None,
) -> int:
    """Insert a message and fan out to SSE subscribers.  Returns the new row id."""
    ts = int(time.time() * 1000)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO messages (ts, role, text, tier, narration, image_b64, image_prompt, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, role, text, tier, narration, image_b64, image_prompt, source),
        )
        msg_id = cur.lastrowid
        # Prune — keep only the most recent _MAX_ROWS rows
        conn.execute(
            f"DELETE FROM messages WHERE id NOT IN "
            f"(SELECT id FROM messages ORDER BY id DESC LIMIT {_MAX_ROWS})"
        )
        conn.commit()

    # Fan out a lightweight payload (no image_b64)
    msg = {
        "id":          msg_id,
        "ts":          ts,
        "role":        role,
        "text":        text,
        "tier":        tier,
        "narration":   narration,
        "hasImage":    bool(image_b64),
        "imagePrompt": image_prompt,
        "source":      source,
    }
    _fanout(json.dumps(msg))
    return msg_id


# ── Read ──────────────────────────────────────────────────────────────────────

def get_recent(limit: int = 100) -> list[dict]:
    """Return the last `limit` messages (oldest first), without image_b64."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, ts, role, text, tier, narration, image_b64, image_prompt, source "
            "FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id":          r[0],
            "ts":          r[1],
            "role":        r[2],
            "text":        r[3],
            "tier":        r[4],
            "narration":   r[5],
            "hasImage":    bool(r[6]),
            "imagePrompt": r[7],
            "source":      r[8],
        }
        for r in reversed(rows)
    ]


def get_image_b64(msg_id: int) -> Optional[str]:
    """Return the raw base64 image string for a specific message, or None."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT image_b64 FROM messages WHERE id = ?", (msg_id,)
        ).fetchone()
    return row[0] if row else None


# ── SSE fanout ────────────────────────────────────────────────────────────────

def subscribe() -> queue.Queue:
    """Register a new SSE listener.  Returns a Queue that receives JSON strings."""
    q: queue.Queue = queue.Queue(maxsize=64)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    """Remove a disconnected SSE listener."""
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def _fanout(payload: str) -> None:
    """Push a JSON string to all live SSE listeners; drop the dead ones."""
    dead: list[queue.Queue] = []
    with _lock:
        for q in _subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                _subscribers.remove(q)
            except ValueError:
                pass
