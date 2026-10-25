"""The three sources become passages with the right text, title, time and place, and a passage's key is stable."""
import json
from datetime import datetime, timezone

import corpus
from memory.schema import Conversation, Message
from memory.store import render


def test_windows_cut_long_text_with_overlap_and_keep_short_text_whole():
    assert corpus.windows("short") == ["short"]
    text = ("A sentence about the lighthouse. " * 120).strip()
    parts = corpus.windows(text, size=500, overlap=100)
    assert len(parts) > 3 and all(len(p) <= 500 for p in parts)
    assert parts[0][-50:] in text and parts[1][:20] in parts[0]     # the overlap: the next window starts inside the last


def test_agent_turns_json_one_passage_per_turn_with_tools_named_not_dumped(tmp_path):
    f = tmp_path / "2026-10-10_abc.turns.json"
    f.write_text(json.dumps({"title": "Fix the steam link", "turns": [
        {"at": 1791698480, "message": "Why does the Steam Link ask for a PIN every time?",
         "events": [{"t": "text", "text": "Checking the pairing settings."},
                    {"t": "tool", "name": "Bash", "detail": "cat " + "x" * 500},
                    {"t": "text", "text": "It forgets the pairing because the config is read-only."}]},
        {"at": 1791698600, "message": "ok", "events": []},
    ]}), encoding="utf-8")
    ps = corpus.read_agent(f, f.name)
    assert len(ps) == 1                                               # "ok" is too short to carry meaning
    p = ps[0]
    assert p.source == "agent" and p.title == "Fix the steam link" and p.anchor == "turn 1" and p.at.startswith("2026-")
    assert "Steam Link ask for a PIN" in p.text and "read-only" in p.text
    assert "Bash: cat " in p.text and "x" * 200 not in p.text          # a tool call is named, its output is not indexed
    assert p.embed_text().startswith("Fix the steam link\n")


def test_agent_markdown_and_the_turns_file_wins(tmp_path):
    (tmp_path / "2026-10-10_abc.turns.json").write_text(json.dumps({"title": "t", "turns": []}), encoding="utf-8")
    (tmp_path / "2026-10-10_abc.claude-s1.md").write_text("ignored", encoding="utf-8")
    md = tmp_path / "2026-10-11_def.claude-s2.md"
    md.write_text("# Private Skippy conversation\n\n---\n\n## The Captain, 2026-10-11 09:00:00\n\nMake the admin card red please, "
                  "and ship it.\n\n### Skippy\n\nDone: the admin card is red and live.\n\n_2026-10-11 09:01:00, done_\n",
                  encoding="utf-8")
    files = corpus.agent_files(tmp_path)
    assert [p.name for p in files] == ["2026-10-10_abc.turns.json", "2026-10-11_def.claude-s2.md"]
    ps = corpus.read_agent(md, md.name)
    assert len(ps) == 1 and ps[0].at == "2026-10-11T09:00" and "Skippy:" in ps[0].text and "_2026" not in ps[0].text
    assert ps[0].title.startswith("Make the admin card red")


def _conv():
    t = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    return Conversation(source="claude-code", id="s1", title="Movies: rename them", started=t, updated=t, device="skippy-pc",
                        messages=[Message("user", "Go through all of these movies and rename them to their real names.", t),
                                  Message("assistant", "Renamed 41 files; three were ambiguous and I left them.", t),
                                  Message("user", "Which three were ambiguous ones, and why did you leave them alone?", t),
                                  Message("assistant", "Two had no year and one was a fan edit.", t)])


def test_memory_archive_markdown_one_passage_per_captain_turn():
    ps = corpus.memory_text(render(_conv()), "claude-code/2026-10/x.md")
    assert [p.anchor for p in ps] == ["turn 1", "turn 2"]
    assert ps[0].title == "Movies: rename them" and ps[0].at == "2026-10-02T12:00"
    assert "Captain: Go through" in ps[0].text and "Claude: Renamed 41" in ps[0].text and "fan edit" in ps[1].text


def test_reading_the_transcript_gives_the_archive_s_own_words(tmp_path, monkeypatch):
    """read_session (what the container does) == read_memory on the archive's file (what the archive holds)."""
    from memory.parsers import claude_code
    monkeypatch.setattr(claude_code, "parse_session", lambda path, device="unknown": _conv())
    via_session = corpus.read_session(tmp_path / "s1.jsonl", "proj/s1.jsonl")
    md = tmp_path / "x.md"
    md.write_text(render(_conv()), encoding="utf-8")
    assert [p.text for p in via_session] == [p.text for p in corpus.read_memory(md, "x.md")]


def test_session_files_leave_out_subagents(tmp_path):
    (tmp_path / "p" / "s1" / "subagents").mkdir(parents=True)
    (tmp_path / "p" / "s1.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "p" / "s1" / "subagents" / "a.jsonl").write_text("", encoding="utf-8")
    assert [p.name for p in corpus.session_files(tmp_path)] == ["s1.jsonl"]


def test_mentat_log_question_and_answer_and_bad_lines_skipped(tmp_path):
    f = tmp_path / "2026-10.jsonl"
    f.write_text("\n".join([
        json.dumps({"ts": "2026-10-23T10:00:00Z", "endpoint": "/ask", "tool": None,
                    "request": {"question": "How far does a baseball go at 45 degrees and 40 m/s?"},
                    "response": {"answer": "About 163 m, ignoring drag."}}),
        "not json",
        json.dumps({"ts": "2026-10-23T11:00:00Z", "endpoint": "/tools/schwarzschild_radius", "tool": "schwarzschild_radius",
                    "request": {"mass_kg": 1.989e30}, "response": {"result": {"radius_m": 2954.0, "steps": ["..."]}}}),
        json.dumps("a bare string"),
    ]), encoding="utf-8")
    ps = corpus.read_mentat(f, f.name)
    assert len(ps) == 2
    assert "baseball" in ps[0].text and "163 m" in ps[0].text and ps[0].anchor == "line 1"
    assert ps[1].title == "Mentat schwarzschild_radius" and "radius_m=2954.0" in ps[1].text and "steps" not in ps[1].text


def test_key_is_stable_and_changes_with_the_text():
    a = corpus.Passage("agent", "d", "t", "", "turn 1", "same words here, long enough to count")
    b = corpus.Passage("agent", "d", "t", "", "turn 1", "same words here, long enough to count")
    c = corpus.Passage("agent", "d", "t", "", "turn 1", "other words here, long enough to count")
    assert a.key == b.key != c.key
