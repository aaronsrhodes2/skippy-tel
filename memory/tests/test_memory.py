import json
import shutil
import zipfile
from pathlib import Path

import pytest

from memory import cli, distill, index, parsers
from memory.store import Archive, read_front_matter

FIXTURE = Path(__file__).parent / "fixtures" / "claude_conversations.json"


@pytest.fixture
def archive(tmp_path) -> Archive:
    a = Archive(tmp_path / "Skippy Memory")
    a.init()
    return a


def _conversations():
    return list(parsers.load(FIXTURE))


# ── parsing ───────────────────────────────────────────────────────────────────

def test_claude_export_parses_and_skips_empty_conversations():
    convs = _conversations()
    assert [c.source for c in convs] == ["claude", "claude"]
    physics, makefile = convs
    assert physics.title == "Why does time dilate near a black hole?"
    assert [m.role for m in physics.messages] == ["user", "assistant"]
    assert physics.url == "https://claude.ai/chat/0b6f1c9e-1111-4a5b-9c1d-aaaaaaaaaaaa"
    assert makefile.title == "Untitled"


def test_thinking_blocks_are_dropped_and_text_fallback_used():
    physics, makefile = _conversations()
    assert "internal reasoning" not in physics.messages[1].text
    assert physics.messages[1].text.startswith("Gravitational time dilation")
    assert makefile.messages[1].text == "It pushes SkippyTel, zPhone and bilby in one go."
    assert makefile.messages[0].attachments == ["Makefile"]


def test_load_reads_zip_exports(tmp_path):
    zpath = tmp_path / "data-2026-09-28.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(FIXTURE, "data-2026-09-28/conversations.json")
        zf.writestr("data-2026-09-28/users.json", "[]")
    assert len(list(parsers.load(zpath))) == 2


def test_unknown_format_is_rejected(tmp_path):
    bad = tmp_path / "conversations.json"
    bad.write_text(json.dumps({"not": "an export"}))
    with pytest.raises(ValueError):
        parsers.load(bad)


# ── archive ───────────────────────────────────────────────────────────────────

def test_ingest_writes_markdown_with_front_matter(archive):
    result = archive.ingest(_conversations())
    assert (result.new, result.updated, result.skipped) == (2, 0, 0)

    files = sorted((archive.root / "conversations" / "claude").rglob("*.md"))
    assert [f.parent.name for f in files] == ["2026-09", "2026-09"]
    physics = next(f for f in files if "black-hole" in f.name)
    assert physics.name == "2026-09-01-why-does-time-dilate-near-a-black-hole-0b6f1c9e.md"

    meta = read_front_matter(physics)
    assert meta["source"] == "claude"
    assert meta["title"] == "Why does time dilate near a black hole?"
    assert meta["messages"] == 2
    assert meta["topics"] == []
    assert meta["url"].startswith("https://claude.ai/chat/")

    body = physics.read_text(encoding="utf-8")
    assert "**Captain** · 2026-09-01 14:03 UTC" in body
    assert "**Claude**" in body


def test_reingest_is_a_no_op(archive):
    archive.ingest(_conversations())
    before = {p: p.read_text(encoding="utf-8") for p in archive.root.rglob("*.md")}

    again = Archive(archive.root).ingest(_conversations())
    assert (again.new, again.updated, again.skipped) == (0, 0, 2)
    after = {p: p.read_text(encoding="utf-8") for p in archive.root.rglob("*.md")}
    assert before == after


def test_continued_conversation_is_updated_in_place_keeping_tags(archive):
    archive.ingest(_conversations())
    rel = Path(archive.ledger["claude:0b6f1c9e-1111-4a5b-9c1d-aaaaaaaaaaaa"]["path"])
    from memory.store import update_front_matter
    update_front_matter(archive.root / rel, topics=["physics"], summary="Time dilation.")

    data = json.loads(FIXTURE.read_text())
    data[0]["updated_at"] = "2026-09-20T09:00:00Z"
    data[0]["chat_messages"].append({
        "uuid": "m9", "sender": "human", "text": "And near a neutron star?",
        "created_at": "2026-09-20T09:00:00Z",
    })
    result = Archive(archive.root).ingest(parsers.claude.parse(data))
    assert (result.new, result.updated, result.skipped) == (0, 1, 1)

    meta = read_front_matter(archive.root / rel)
    assert meta["messages"] == 3
    assert meta["topics"] == ["physics"]
    assert meta["summary"] == "Time dilation."


def test_init_copies_readme_once(archive):
    readme = archive.root / "README.md"
    assert "Skippy Memory" in readme.read_text(encoding="utf-8")
    readme.write_text("custom", encoding="utf-8")
    archive.init()
    assert readme.read_text(encoding="utf-8") == "custom"


# ── index + distill ───────────────────────────────────────────────────────────

def test_index_and_topic_pages(archive, monkeypatch):
    archive.ingest(_conversations())
    labels = iter([
        {"topics": ["physics"], "summary": "Gravitational time dilation."},
        {"topics": ["skippy-tel", "tooling"], "summary": "What make push-all does."},
    ])
    monkeypatch.setattr(distill, "label", lambda *a, **k: next(labels))
    assert distill.distill(archive, log=lambda *_: None) == 2
    assert distill.distill(archive, log=lambda *_: None) == 0   # already tagged

    index.rebuild(archive)
    idx = (archive.root / "index.md").read_text(encoding="utf-8")
    assert "**claude**: 2 conversations" in idx
    assert "[physics](<topics/physics.md>) (1)" in idx
    assert "Gravitational time dilation." in idx

    topic = (archive.root / "topics" / "physics.md").read_text(encoding="utf-8")
    assert "(<../conversations/claude/2026-09/" in topic


def test_clean_topics():
    assert distill._clean_topics(["Physics", "physics", "  Game  Servers ", "", "a", "b"]) == \
        ["physics", "game servers", "a"]
    assert distill._clean_topics("physics") == []


# ── cli ───────────────────────────────────────────────────────────────────────

def test_cli_inbox_end_to_end(tmp_path, capsys):
    out = tmp_path / "Skippy Memory"
    assert cli.main(["init", "--out", str(out)]) == 0
    shutil.copy(FIXTURE, out / "inbox" / "conversations.json")
    (out / "inbox" / "notes.txt").write_text("ignored")

    assert cli.main(["inbox", "--out", str(out)]) == 0
    assert cli.main(["inbox", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "2 new, 0 updated, 0 unchanged" in printed
    assert "0 new, 0 updated, 2 unchanged" in printed
    assert (out / "index.md").exists()
