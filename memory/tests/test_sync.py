"""Grok's export, and the automatic pass the bridge runs (memory/sync.py): changed Claude Code sessions only, inbox exports
imported then moved to inbox/imported/ (never deleted), the list and one conversation read back for "Continue with Skippy"."""
import json
import os
import zipfile
from pathlib import Path

import pytest

from memory import parsers, sync
from memory.store import Archive

FIX = Path(__file__).parent / "fixtures"
GROK = FIX / "grok_export.json"


@pytest.fixture
def archive(tmp_path) -> Archive:
    a = Archive(tmp_path / "Skippy Memory")
    a.init()
    return a


def test_grok_export_keeps_the_words_only_in_order_with_its_times():
    convs = list(parsers.load(GROK))
    assert [c.id[-1] for c in convs] == ["1", "2"]                     # the empty conversation is dropped
    cold, joke = convs
    assert cold.source == "grok" and cold.title == "Range of a Model Y in the cold"
    assert [(m.role, m.text[:8]) for m in cold.messages] == [("user", "How much"), ("assistant", "Roughly ")]
    assert cold.messages[0].created.year == 2026 and cold.url.endswith(cold.id)
    assert joke.title == "Tell me a joke about Bilby"                  # no title: the first line of the first message


def test_a_grok_zip_is_found_by_its_contents_not_its_file_name(tmp_path):
    z = tmp_path / "grok-export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("ttl/30d/export_data/abc/prod-grok-backend.json", GROK.read_text(encoding="utf-8"))
        zf.writestr("ttl/30d/export_data/abc/prod-mc-auth-mgmt-api.json", json.dumps({"user": {"email": "x"}}))
    assert [c.source for c in parsers.load(z)] == ["grok", "grok"]


def test_sync_imports_the_inbox_moves_it_aside_and_a_second_pass_does_nothing(archive):
    (archive.root / "inbox" / "grok.json").write_text(GROK.read_text(encoding="utf-8"), encoding="utf-8")
    (archive.root / "inbox" / "junk.json").write_text("{}", encoding="utf-8")
    first = sync.sync(archive, None, "skippy-pc")
    assert first["exports"]["grok.json"] == "2 new, 0 updated"
    assert first["exports"]["junk.json"].startswith("error:")
    assert (archive.root / "inbox" / "imported" / "grok.json").exists()
    assert (archive.root / "inbox" / "junk.json").exists()               # a failed one stays, with its reason
    assert "junk.json" in sync.load_state(archive)["errors"]
    assert "Range of a Model Y" in (archive.root / "index.md").read_text(encoding="utf-8")
    again = sync.sync(Archive(archive.root), None, "skippy-pc")
    assert "grok.json" not in again["exports"]


def test_only_changed_claude_code_sessions_are_read(archive, tmp_path):
    home = tmp_path / "projects"
    shutil_src = FIX / "claude-code"
    sessions = sorted(shutil_src.rglob("*.jsonl"))
    assert sessions, "fixture sessions"
    for i, s in enumerate(sessions):
        dst = home / "D--proj" / f"s{i}.jsonl"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(s.read_bytes())
        os.utime(dst, (1_000_000 + i, 1_000_000 + i))
    first = sync.sync(archive, home, "skippy-pc")
    assert first["sessions"] >= 1
    assert sync.sync(Archive(archive.root), home, "skippy-pc")["sessions"] == 0        # nothing changed: nothing read


def test_listing_and_read_give_the_newest_turns_of_a_long_one(archive):
    archive.ingest(parsers.load(GROK))
    listed = sync.listing(Archive(archive.root))
    assert [r["source"] for r in listed["conversations"]] == ["grok", "grok"] and listed["total"] == 2
    key = listed["conversations"][1]["key"]
    got = sync.read(Archive(archive.root), key)
    assert got["title"] == "Range of a Model Y in the cold" and "Roughly 20 to 30 percent" in got["text"]
    assert not got["text"].startswith("---") and got["cut"] is False
    short = sync.read(Archive(archive.root), key, max_chars=40)
    assert short["cut"] and short["text"].startswith("…(earlier turns left out)…") and short["text"].endswith("short trips.")
    assert sync.read(Archive(archive.root), "grok:nope") is None
