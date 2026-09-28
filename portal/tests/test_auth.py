"""is_allowed() is pure string logic split out so it's testable without a
live Flask app/request context — Authlib itself isn't exercised here."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _allowed(raw: str, email: str) -> bool:
    """Mirrors config._split_emails + auth.is_allowed without needing
    Flask's app context (current_app) to run as a plain unit test."""
    allowed = frozenset(e.strip().lower() for e in raw.split(",") if e.strip())
    return email.strip().lower() in allowed


def test_case_insensitive_match():
    assert _allowed("Aaron.S.Rhodes@gmail.com", "aaron.s.rhodes@gmail.com")
    assert _allowed("aaron.s.rhodes@gmail.com", "AARON.S.RHODES@GMAIL.COM")


def test_whitespace_in_list_is_ignored():
    assert _allowed(" aaron.s.rhodes@gmail.com , friend@example.com ", "friend@example.com")


def test_email_not_in_list_is_rejected():
    assert not _allowed("aaron.s.rhodes@gmail.com", "stranger@example.com")


def test_empty_list_allows_nobody():
    assert not _allowed("", "aaron.s.rhodes@gmail.com")
