"""
SkippyTel — Keyword Memory

Persistent store of voice-command keywords shared across all AI tiers and the
Captain's phone. Lives in SQLite (same data-dir pattern as photo_tags.db).

Two categories of keywords:

  "hardcoded" — keywords baked into SkippyChat's KeywordScanner.kt.
                Seeded once on first init; never modified by the AI.
                Documents what the phone client *natively* understands
                without any server round-trip.

  "learned"   — keywords the AI has discovered from conversation patterns
                and stored via the [SKIPPY_LEARN: …] annotation. Loaded
                dynamically into the phone's voice trigger set on each sync.
                These are the Captain's personal shortcuts — built up over time.

Why keywords are personality-agnostic:
  Skippy's personality may swap models (Ollama / Claude / Gemini) but the
  keyboard of shortcuts stays constant. Every incoming "you" — Skippy,
  Nagatha, thinking-cap — gets the full keyword digest injected into context.
  You learn it once; every personality benefits.

Phone sync protocol:
  Phone sends GET /keywords/sync → receives merged hardcoded + learned triggers.
  The phone loads these as dynamic triggers in KeywordScanner.dynamicTriggers.
  Learned keywords are also injected into the LLM system prompt so the AI
  knows what shortcuts exist and can refer to them in conversation.
"""

import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_DB_PATH = os.environ.get("KEYWORD_MEMORY_DB", "/drive-state/keyword_memory.db")

# ── Hardcoded seed — mirrors KeywordScanner.kt exactly ───────────────────────
# Changing these here does NOT change the phone app — they're documentation.
# Column meanings: phrase (lowercase), intent, description, personality_note.

_HARDCODED_SEED = [
    # ── Clear / edit ──
    ("cancel that",     "clear_all",       "Nuke the entire voice draft",           None),
    ("scratch that",    "clear_all",       "Nuke the entire voice draft",           None),
    ("forget it",       "clear_all",       "Nuke the entire voice draft",           None),
    ("never mind",      "drop_paragraph",  "Drop the last paragraph of the draft",  None),
    ("wait wait",       "drop_paragraph",  "Drop the last paragraph of the draft",  None),
    # ── Send ──
    ("send now",        "send_now",        "Send draft immediately, skip timer",     None),
    ("send it",         "send_now",        "Send draft immediately",                 None),
    ("go ahead",        "send_now",        "Send draft immediately",                 None),
    ("ship it",         "send_now",        "Send draft immediately (PLANNING too)",  "Even I have to admit this one is satisfying."),
    # ── Modes ──
    ("let's make a plan",    "planning_mode",  "Enter PLANNING mode — accumulate until ship it",  None),
    ("lets make a plan",     "planning_mode",  "Enter PLANNING mode",                              None),
    ("let's plan it out",    "planning_mode",  "Enter PLANNING mode",                              None),
    ("planning mode",        "planning_mode",  "Enter PLANNING mode",                              None),
    ("solid gold",           "planning_mode",  "Enter PLANNING mode (premium)",                    "Reserved for the Captain's finest ideas."),
    ("done planning",        "vibe_mode",      "Exit PLANNING, return to VIBE mode",               None),
    ("back to normal",       "vibe_mode",      "Exit PLANNING, return to VIBE mode",               None),
    ("let's vibe",           "vibe_mode",      "Exit PLANNING / enter VIBE mode",                  None),
    ("lets vibe",            "vibe_mode",      "Exit PLANNING / enter VIBE mode",                  None),
    ("vibe mode",            "vibe_mode",      "Enter VIBE mode",                                  None),
    # ── Language ──
    ("habla español",   "spanish_mode",    "Activate Spanish recognition",    None),
    ("español",         "spanish_mode",    "Activate Spanish recognition",    None),
    ("espanol",         "spanish_mode",    "Activate Spanish recognition",    None),
    ("spanish mode",    "spanish_mode",    "Activate Spanish recognition",    None),
    ("spanish please",  "spanish_mode",    "Activate Spanish recognition",    None),
    ("english only",    "english_mode",    "Return to English recognition",   None),
    ("back to english", "english_mode",    "Return to English recognition",   None),
    ("english mode",    "english_mode",    "Return to English recognition",   None),
    # ── Volume ──
    ("volume up",           "volume_up",    "Raise media volume one step",  None),
    ("louder",              "volume_up",    "Raise media volume one step",  None),
    ("turn it up",          "volume_up",    "Raise media volume one step",  None),
    ("turn up the volume",  "volume_up",    "Raise media volume one step",  None),
    ("volume down",         "volume_down",  "Lower media volume one step",  None),
    ("quieter",             "volume_down",  "Lower media volume one step",  None),
    ("turn it down",        "volume_down",  "Lower media volume one step",  None),
    ("softer",              "volume_down",  "Lower media volume one step",  None),
    ("lower the volume",    "volume_down",  "Lower media volume one step",  None),
    # ── Bilby ──
    ("what's playing",      "bilby_status",  "Query Bilby for now-playing",   None),
    ("whats playing",       "bilby_status",  "Query Bilby for now-playing",   None),
    ("what is playing",     "bilby_status",  "Query Bilby for now-playing",   None),
    ("what's on",           "bilby_status",  "Query Bilby for now-playing",   None),
    ("whats on",            "bilby_status",  "Query Bilby for now-playing",   None),
    ("what song is this",   "bilby_status",  "Query Bilby for now-playing",   None),
    ("bilby what",          "bilby_status",  "Query Bilby for now-playing",   None),
    ("bilby status",        "bilby_status",  "Query Bilby for now-playing",   None),
    ("now playing",         "bilby_status",  "Query Bilby for now-playing",   None),
    ("bilby next",          "bilby_next",    "Tell Bilby to skip the track",  None),
    ("next track",          "bilby_next",    "Tell Bilby to skip the track",  None),
    ("skip track",          "bilby_next",    "Tell Bilby to skip the track",  None),
    ("skip song",           "bilby_next",    "Tell Bilby to skip the track",  None),
    ("next song",           "bilby_next",    "Tell Bilby to skip the track",  None),
    # ── AI tier escalation ──
    ("put on your thinking cap",  "claude_tier",         "Escalate to Claude Sonnet",           "I find this flattering and insulting simultaneously."),
    ("ask nagatha",               "nagatha_tier",        "Route to Nagatha (Gemini)",            "Nagatha is charming. I tolerate her."),
    ("ask gemini",                "nagatha_tier",        "Route to Nagatha (Gemini, alias)",     None),
    ("trust the awesomeness",     "trust_awesomeness",   "Invoke trust-the-awesomeness (Opus)",  "The big gun. Use respectfully."),
    # ── Services / queries ──
    ("what are our available",   "query_services_all",  "List available services",              None),
    ("list services",            "query_services_all",  "List available services",              None),
    ("available services",       "query_services_all",  "List available services",              None),
    ("what can you do",          "query_services_all",  "List available services",              None),
    # ── SkippyDroid display modes (dispatched from phone → droid) ──
    ("fullscreen",          "droid_fullscreen",  "SkippyDroid: enter FULLSCREEN mode",       None),
    ("go fullscreen",       "droid_fullscreen",  "SkippyDroid: enter FULLSCREEN mode",       None),
    ("hud mode",            "droid_hud",         "SkippyDroid: restore HUD chrome",          None),
    ("back to hud",         "droid_hud",         "SkippyDroid: restore HUD chrome",          None),
    ("clear fullscreen",    "droid_glass_view",  "SkippyDroid: close service, show glass",   None),
    ("close app",           "droid_glass_view",  "SkippyDroid: close service, show glass",   None),
    ("glass view",          "droid_glass_view",  "SkippyDroid: show glass panoramic view",   None),
    ("put on shades",       "droid_shades",      "SkippyDroid: activate shading overlay",    "For when the Captain needs to look cool."),
    ("shades",              "droid_shades",      "SkippyDroid: activate shading overlay",    None),
    ("eyes open",           "droid_eyes_open",   "SkippyDroid: remove shading overlay",      None),
    ("shades off",          "droid_eyes_open",   "SkippyDroid: remove shading overlay",      None),
]


# ── DB helpers ────────────────────────────────────────────────────────────────

@contextmanager
def _conn():
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def init_db():
    """Create tables + seed hardcoded keywords. Idempotent."""
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS keywords (
                phrase          TEXT PRIMARY KEY,
                intent          TEXT NOT NULL,
                description     TEXT NOT NULL,
                personality_note TEXT,
                source          TEXT NOT NULL DEFAULT 'learned',
                created_at      TEXT NOT NULL,
                last_used       TEXT
            )
        """)
        con.commit()
        _seed_hardcoded(con)


def _seed_hardcoded(con: sqlite3.Connection):
    """Insert hardcoded entries; skip if already present."""
    now = _now()
    for phrase, intent, desc, note in _HARDCODED_SEED:
        con.execute("""
            INSERT OR IGNORE INTO keywords
                (phrase, intent, description, personality_note, source, created_at)
            VALUES (?, ?, ?, ?, 'hardcoded', ?)
        """, (phrase.lower(), intent, desc, note, now))
    con.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Public API ────────────────────────────────────────────────────────────────

def get_all() -> list[dict]:
    """Return all keywords ordered by source (hardcoded first) then phrase."""
    with _conn() as con:
        rows = con.execute("""
            SELECT * FROM keywords
            ORDER BY CASE source WHEN 'hardcoded' THEN 0 ELSE 1 END, phrase
        """).fetchall()
    return [dict(r) for r in rows]


def get_learned() -> list[dict]:
    """Return only learned/inferred keywords (not hardcoded)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM keywords WHERE source != 'hardcoded' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def learn(phrase: str, intent: str, description: str,
          personality_note: str | None = None,
          source: str = "learned") -> bool:
    """
    Add or update a keyword. Returns True if inserted, False if updated.

    phrase      — the voice trigger phrase (auto-lowercased + stripped)
    intent      — machine intent ID, e.g. "bilby_next" or "check_coffee"
    description — human-readable description
    personality_note — optional Skippy-voice comment
    source      — 'learned' (from LLM) or 'inferred' (from pattern analysis)
    """
    phrase = phrase.lower().strip()
    if not phrase or not intent:
        return False
    now = _now()
    with _conn() as con:
        existing = con.execute(
            "SELECT phrase FROM keywords WHERE phrase = ?", (phrase,)
        ).fetchone()
        if existing:
            con.execute("""
                UPDATE keywords SET intent=?, description=?, personality_note=?,
                source=?, last_used=?
                WHERE phrase=?
            """, (intent, description, personality_note, source, now, phrase))
            con.commit()
            return False
        else:
            con.execute("""
                INSERT INTO keywords (phrase, intent, description, personality_note,
                                      source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (phrase, intent, description, personality_note, source, now))
            con.commit()
            return True


def forget(phrase: str) -> bool:
    """Remove a learned keyword. Refuses to delete hardcoded keywords."""
    phrase = phrase.lower().strip()
    with _conn() as con:
        row = con.execute("SELECT source FROM keywords WHERE phrase=?", (phrase,)).fetchone()
        if not row:
            return False
        if row["source"] == "hardcoded":
            return False   # never delete the phone's baked-in commands
        con.execute("DELETE FROM keywords WHERE phrase=?", (phrase,))
        con.commit()
    return True


def mark_used(phrase: str):
    """Record that a keyword was triggered. Used for analytics / LRU pruning."""
    phrase = phrase.lower().strip()
    with _conn() as con:
        con.execute("UPDATE keywords SET last_used=? WHERE phrase=?", (_now(), phrase))
        con.commit()


def context_digest(max_learned: int = 30) -> str:
    """
    Return a compact string summarising the keyword set for injection into
    the LLM system prompt. Hardcoded keywords are grouped by intent family;
    learned keywords listed individually (most recent first, capped at
    max_learned).

    Format (plain text, no markdown — Skippy hates formatting):
      Native phone commands: cancel that (clear_all), ship it (send_now), ...
      Your learned shortcuts:
        "code red" → ups_status — Check UPS immediately (Captain's alarm phrase)
        ...
    """
    with _conn() as con:
        hardcoded = con.execute(
            "SELECT phrase, intent FROM keywords WHERE source='hardcoded' ORDER BY intent, phrase"
        ).fetchall()
        learned = con.execute(
            "SELECT phrase, intent, description, personality_note FROM keywords "
            "WHERE source != 'hardcoded' ORDER BY created_at DESC LIMIT ?",
            (max_learned,)
        ).fetchall()

    # Group hardcoded by intent for brevity
    from collections import defaultdict
    by_intent: dict[str, list[str]] = defaultdict(list)
    for row in hardcoded:
        by_intent[row["intent"]].append(row["phrase"])

    hc_lines = []
    for intent, phrases in sorted(by_intent.items()):
        sample = ", ".join(f'"{p}"' for p in phrases[:3])
        if len(phrases) > 3:
            sample += f" (+{len(phrases)-3} more)"
        hc_lines.append(f"  {intent}: {sample}")

    parts = ["Native phone commands (handled before they reach you):"]
    parts += hc_lines

    if learned:
        parts.append("\nYour learned shortcuts (you taught these):")
        for row in learned:
            note = f" — {row['personality_note']}" if row["personality_note"] else ""
            parts.append(f'  "{row["phrase"]}" → {row["intent"]}: {row["description"]}{note}')
    else:
        parts.append("\nNo learned shortcuts yet. You can add them with [SKIPPY_LEARN: …].")

    return "\n".join(parts)


# ── [SKIPPY_LEARN:] annotation parser ─────────────────────────────────────────

_LEARN_RE = re.compile(
    r'\[SKIPPY_LEARN:\s*(\{.*?\})\s*\]',
    re.IGNORECASE | re.DOTALL,
)


def extract_and_learn(text: str) -> tuple[str, list[dict]]:
    """
    Scan `text` for [SKIPPY_LEARN: {"phrase":…, "intent":…, "description":…}]
    annotations. Each match is saved to the DB and stripped from the text.

    Returns (clean_text, list_of_learned_entries).
    """
    learned = []
    clean   = text

    for m in _LEARN_RE.finditer(text):
        try:
            data = json.loads(m.group(1))
            phrase      = str(data.get("phrase", "")).strip()
            intent      = str(data.get("intent", "learned")).strip()
            description = str(data.get("description", phrase)).strip()
            note        = data.get("note") or data.get("personality_note")

            if phrase:
                learn(phrase, intent, description, personality_note=note, source="learned")
                learned.append({"phrase": phrase, "intent": intent})
                log.info("Learned keyword: %r → %s", phrase, intent)
        except (json.JSONDecodeError, AttributeError) as e:
            log.warning("Malformed [SKIPPY_LEARN:] annotation: %s — %s", m.group(0), e)

    # Strip all annotations from the displayed response
    clean = _LEARN_RE.sub("", clean).rstrip()
    return clean, learned
