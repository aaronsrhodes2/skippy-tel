"""SQLite store for DropShip photo tags.

Schema:
    photo_tags(
        file_id      TEXT PRIMARY KEY,   -- Drive file ID
        drive_name   TEXT,               -- original filename
        description  TEXT,               -- LLaVA description (empty = not yet tagged)
        tags         TEXT,               -- reserved for future structured tags
        tagged_at    REAL,               -- unix timestamp of when tagging completed
        drive_created TEXT               -- ISO8601 createdTime from Drive API
    )

The DB lives at /drive-state/photo_tags.db — the same bind-mount
used by drive_token.json, so it survives container restarts.

Thread safety: each call opens + closes its own connection. SQLite's
WAL mode allows concurrent readers + one writer, which is fine for
our workload (one background scanner + occasional search queries).
"""
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DB_PATH = Path("/drive-state/photo_tags.db")
_lock    = threading.Lock()    # serialise DDL + writes


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db() -> None:
    """Create table if it doesn't exist. Safe to call multiple times."""
    with _lock, _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS photo_tags (
                file_id       TEXT PRIMARY KEY,
                drive_name    TEXT,
                description   TEXT DEFAULT '',
                tags          TEXT DEFAULT '',
                tagged_at     REAL DEFAULT 0,
                drive_created TEXT DEFAULT ''
            )
        """)
        con.commit()
        log.info("photo_tags DB ready at %s", _DB_PATH)


def upsert_tag(
    file_id: str,
    drive_name: str,
    description: str,
    drive_created: str = "",
    tags: str = "",
) -> None:
    """Insert or update a tagged photo record."""
    with _lock, _conn() as con:
        con.execute("""
            INSERT INTO photo_tags (file_id, drive_name, description, tags, tagged_at, drive_created)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                drive_name    = excluded.drive_name,
                description   = excluded.description,
                tags          = excluded.tags,
                tagged_at     = excluded.tagged_at,
                drive_created = excluded.drive_created
        """, (file_id, drive_name, description, tags, time.time(), drive_created))
        con.commit()


def mark_seen(file_id: str, drive_name: str, drive_created: str = "") -> None:
    """Record that we know this file exists, even if tagging hasn't run yet."""
    with _lock, _conn() as con:
        con.execute("""
            INSERT OR IGNORE INTO photo_tags (file_id, drive_name, drive_created)
            VALUES (?, ?, ?)
        """, (file_id, drive_name, drive_created))
        con.commit()


def get_tagged_ids() -> set:
    """Return all file_ids that have been fully tagged (non-empty description)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT file_id FROM photo_tags WHERE description != '' AND tagged_at > 0"
        ).fetchall()
    return {r["file_id"] for r in rows}


def get_all_seen_ids() -> set:
    """Return all file_ids we have any record of (tagged or not)."""
    with _conn() as con:
        rows = con.execute("SELECT file_id FROM photo_tags").fetchall()
    return {r["file_id"] for r in rows}


def search(query: str, limit: int = 5) -> list[dict]:
    """
    Simple LIKE search across description + drive_name.
    Returns list of {file_id, drive_name, description, drive_created}.
    For full-text search quality, use space-split tokens — any token match counts.
    """
    tokens = [t.strip() for t in query.lower().split() if len(t.strip()) > 2]
    if not tokens:
        return []

    # Build WHERE clause: each token must appear somewhere in description or name
    conditions = " AND ".join(
        ["(LOWER(description) LIKE ? OR LOWER(drive_name) LIKE ?)"] * len(tokens)
    )
    params = []
    for t in tokens:
        like = f"%{t}%"
        params.extend([like, like])

    sql = f"""
        SELECT file_id, drive_name, description, drive_created, tagged_at
        FROM photo_tags
        WHERE {conditions}
        ORDER BY tagged_at DESC
        LIMIT ?
    """
    params.append(limit)

    with _conn() as con:
        rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_latest(limit: int = 1) -> list[dict]:
    """Return the most recently added photo(s) by drive_created timestamp."""
    with _conn() as con:
        rows = con.execute("""
            SELECT file_id, drive_name, description, drive_created, tagged_at
            FROM photo_tags
            ORDER BY drive_created DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_page(offset: int = 0, limit: int = 50) -> tuple[list[dict], int]:
    """
    Return a page of all known photos ordered by most-recent first.
    Returns (rows, total_count).
    Tagged images sort before untagged ones within the same date bucket.
    """
    with _conn() as con:
        total = con.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0]
        rows = con.execute("""
            SELECT file_id, drive_name, description, drive_created, tagged_at
            FROM photo_tags
            ORDER BY
                CASE WHEN description != '' THEN 0 ELSE 1 END,
                drive_created DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
    return [dict(r) for r in rows], total
