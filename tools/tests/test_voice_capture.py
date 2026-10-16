"""tools/voice_capture.py: the one-shot ElevenLabs capture of Skippy's lines, as teacher data for a local voice.

Credits cannot be re-spent, so the safety rails are what is tested: a dry run that never touches the network, no spending without an explicit
ceiling, priority order, resumability, a hard stop when the balance is gone, and a key that never reaches the output. The lines file is checked
too: unique ids, the persona's tag vocabulary only, never more than two tags in a row, and every tag covered by the first tier.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import voice_capture as vc

LINES = vc.DEFAULT_LINES
KEY = "xi-secret-key-should-never-print"


def lines():
    return vc.load_lines(LINES)


def args(tmp_path, *extra):
    return ["--lines", str(LINES), "--out", str(tmp_path / "out"), "--env-file", str(tmp_path / "none.env"),
            "--voice-id", "voice123", "--model", "eleven_v3", "--pause", "0", *extra]


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.delenv("ELEVENLABS_MODEL", raising=False)


# ── the lines file ──────────────────────────────────────────────────────────

def test_the_lines_are_well_formed():
    rows = lines()
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "ids must be unique"
    for r in rows:
        assert r["tier"] in (1, 2, 3) and r["group"] and r["text"].strip()
        assert len(r["text"]) <= vc.MAX_CHARS, r["id"]


def test_only_the_persona_tags_are_used_and_never_stacked_past_two():
    allowed = set(vc.TAGS)
    for r in lines():
        found = vc.TAG_RE.findall(r["text"])
        assert set(found) <= allowed, (r["id"], found)
        assert not re.search(r"(\[[a-z ]+\]\s*){3,}", r["text"]), r["id"]


def test_the_first_tier_covers_every_tag_caps_pause_and_cut():
    tier1 = [r for r in lines() if r["tier"] == 1]
    for tag in vc.TAGS:
        assert sum(f"[{tag}]" in r["text"] for r in tier1) >= 3, tag
    joined = " ".join(r["text"] for r in tier1)
    assert "..." in joined and "—" in joined and re.search(r"\b[A-Z]{4,}\b", joined)


def test_the_whole_plan_fits_the_balance_with_room_for_two_more_voices():
    total = vc.plan(lines(), set(), 3, 10**9, 1.0)["credits"]
    assert total <= 6000, "the full Skippy set must leave credits for Nagatha, Bilby and a reserve"


# ── pure helpers ────────────────────────────────────────────────────────────

def test_strip_tags_leaves_the_words():
    assert vc.strip_tags("[sighs] Fine. [chuckles]  Heh.") == "Fine. Heh."
    assert vc.strip_tags("No tags here.") == "No tags here."


def test_the_holdout_split_is_deterministic_and_roughly_a_tenth():
    ids = [r["id"] for r in lines()]
    first = [vc.split_for(i) for i in ids]
    assert first == [vc.split_for(i) for i in ids]
    held = first.count("holdout")
    assert 1 <= held <= len(ids) // 4


def test_plan_keeps_priority_order_tier_filter_and_the_ceiling():
    rows = [{"id": "a", "tier": 1, "text": "x" * 100}, {"id": "b", "tier": 2, "text": "y" * 100},
            {"id": "c", "tier": 1, "text": "z" * 100}]
    p = vc.plan(rows, set(), 1, 10**6, 1.0)
    assert [r["id"] for r in p["todo"]] == ["a", "c"] and p["credits"] == 200
    p = vc.plan(rows, set(), 2, 250, 1.0)                     # tier 1 first, then tier 2; the ceiling stops it
    assert [r["id"] for r in p["todo"]] == ["a", "c"] and p["credits"] == 200
    p = vc.plan(rows, {"a"}, 2, 10**6, 0.5)                   # done lines are skipped; 0.5 credit per character
    assert [r["id"] for r in p["todo"]] == ["c", "b"] and p["credits"] == 100


# ── the run ─────────────────────────────────────────────────────────────────

def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the network was touched")
    monkeypatch.setattr(vc, "render", boom)


def test_a_dry_run_never_touches_the_network_or_the_disk(tmp_path, monkeypatch, capsys):
    no_network(monkeypatch)
    assert vc.main(args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "credits" in out and KEY not in out
    assert not (tmp_path / "out").exists()


def test_spending_needs_an_explicit_ceiling(tmp_path, monkeypatch):
    no_network(monkeypatch)
    with pytest.raises(SystemExit):
        vc.main(args(tmp_path, "--go"))


def test_a_real_run_writes_audio_and_a_manifest_and_resumes(tmp_path, monkeypatch, capsys):
    calls = []

    def fake(key, voice, model, stability, text, fmt, timeout=120):
        calls.append(text)
        assert key == KEY and voice == "voice123" and model == "eleven_v3" and stability == 0.05
        return b"ID3" + text.encode()

    monkeypatch.setattr(vc, "render", fake)
    assert vc.main(args(tmp_path, "--go", "--max-credits", "400", "--tier", "1")) == 0
    out = tmp_path / "out"
    rows = [json.loads(x) for x in (out / "manifest.jsonl").read_text().splitlines()]
    assert rows and len(rows) == len(calls) == len(list((out / "audio").glob("*.mp3")))
    row = rows[0]
    assert {"id", "text_with_tags", "plain_text", "audio", "chars", "tier", "group", "split", "model", "voice_id", "stability",
            "approx_seconds", "created_utc"} <= set(row)
    assert "[" not in row["plain_text"] and row["split"] in ("train", "holdout")
    assert sum(r["chars"] for r in rows) <= 400
    assert KEY not in capsys.readouterr().out
    assert vc.main(args(tmp_path, "--go", "--max-credits", "400", "--tier", "1")) == 0     # a second run adds new lines, never repeats one
    assert len(set(calls)) == len(calls) > len(rows)


def test_an_empty_balance_stops_the_run_and_keeps_what_was_made(tmp_path, monkeypatch):
    n = {"i": 0}

    def fake(key, voice, model, stability, text, fmt, timeout=120):
        n["i"] += 1
        if n["i"] == 3:
            raise vc.Fatal("ElevenLabs refused: out of credits or key not allowed (HTTP 402)")
        return b"ID3audio"

    monkeypatch.setattr(vc, "render", fake)
    assert vc.main(args(tmp_path, "--go", "--max-credits", "5000")) == 1
    out = tmp_path / "out"
    assert len(list((out / "audio").glob("*.mp3"))) == 2
    assert len((out / "manifest.jsonl").read_text().splitlines()) == 2


def test_a_rejected_line_is_skipped_and_logged_not_fatal(tmp_path, monkeypatch):
    seen = []

    def fake(key, voice, model, stability, text, fmt, timeout=120):
        seen.append(text)
        if len(seen) == 2:
            raise vc.Skip("HTTP 422")
        return b"ID3audio"

    monkeypatch.setattr(vc, "render", fake)
    assert vc.main(args(tmp_path, "--go", "--max-credits", "300", "--tier", "1")) == 0
    out = tmp_path / "out"
    assert (out / "failed.jsonl").exists()
    assert len(list((out / "audio").glob("*.mp3"))) == len(seen) - 1


def test_a_model_without_v3_tags_is_warned_about(tmp_path, monkeypatch, capsys):
    no_network(monkeypatch)
    argv = [a for a in args(tmp_path)]
    argv[argv.index("eleven_v3")] = "eleven_turbo_v2_5"
    assert vc.main(argv) == 0
    assert "WARNING" in capsys.readouterr().out
