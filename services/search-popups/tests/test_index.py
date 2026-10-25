"""The index refreshes incrementally (unchanged files aren't read, known passages aren't embedded again), keeps the archive's passages
when Claude Code drops a transcript, and finds by vector."""
import hashlib
import json
import os

import numpy as np

import corpus
import index as index_mod

DIM = 16


def fake_vec(text: str) -> np.ndarray:
    words = set(text.lower().split())
    v = np.zeros(DIM, np.float32)
    for w in words:
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1
    return v / (np.linalg.norm(v) or 1)


class Embed:
    def __init__(self):
        self.seen = []

    def __call__(self, texts):
        self.seen.append(len(texts))
        return np.stack([fake_vec(t) for t in texts])


def turns_file(path, *messages):
    path.write_text(json.dumps({"title": "chat", "turns": [{"at": 1791698480 + i, "message": m, "events": []}
                                                            for i, m in enumerate(messages)]}), encoding="utf-8")


def sources(tmp_path):
    return {"agent": (tmp_path / "agent", corpus.agent_files, corpus.read_agent),
            "memory": (tmp_path / "mem", corpus.agent_files, corpus.read_agent)}


def test_incremental_refresh(tmp_path):
    (tmp_path / "agent").mkdir()
    (tmp_path / "mem").mkdir()
    f = tmp_path / "agent" / "2026-10-01_a.turns.json"
    turns_file(f, "Tell me about the lighthouse in the Daggerheart campaign please")
    idx = index_mod.Index(tmp_path / "ix" / "s.db", "bge")
    emb = Embed()
    r = idx.refresh(sources(tmp_path), emb)
    assert r["files_read"] == 1 and r["passages_new"] == 1 and emb.seen == [1]
    r = idx.refresh(sources(tmp_path), emb)                       # nothing changed: nothing read, nothing embedded
    assert r["files_read"] == 0 and r["passages_new"] == 0 and emb.seen == [1]
    assert idx.stale(sources(tmp_path)) == 0
    turns_file(f, "Tell me about the lighthouse in the Daggerheart campaign please",
               "And what traps did the rogue find on the lighthouse door?")
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    assert idx.stale(sources(tmp_path)) == 1
    r = idx.refresh(sources(tmp_path), emb)                       # the conversation grew: only its new turn is embedded
    assert r["files_read"] == 1 and r["passages_new"] == 1 and emb.seen == [1, 1]
    assert idx.count() == {"agent": 2}


def test_a_vanished_transcript_keeps_its_memory_passages_but_agent_files_are_dropped(tmp_path):
    (tmp_path / "agent").mkdir()
    (tmp_path / "mem").mkdir()
    a = tmp_path / "agent" / "2026-10-01_a.turns.json"
    m = tmp_path / "mem" / "2026-10-01_m.turns.json"
    turns_file(a, "Something said to the private Skippy that is long enough")
    turns_file(m, "Something the archive keeps forever, long enough to index")
    idx = index_mod.Index(tmp_path / "s.db", "bge")
    idx.refresh(sources(tmp_path), Embed())
    a.unlink()
    m.unlink()
    r = idx.refresh(sources(tmp_path), Embed())
    assert r["passages_dropped"] == 1 and idx.count() == {"memory": 1}


def test_another_model_family_starts_the_index_over(tmp_path):
    (tmp_path / "agent").mkdir()
    (tmp_path / "mem").mkdir()
    turns_file(tmp_path / "agent" / "2026-10-01_a.turns.json", "A turn that is certainly long enough to be indexed")
    index_mod.Index(tmp_path / "s.db", "bge").refresh(sources(tmp_path), Embed())
    assert index_mod.Index(tmp_path / "s.db", "qwen3").count() == {}


def test_extras_and_candidates_rank_dedupe_and_filter(tmp_path):
    (tmp_path / "agent").mkdir()
    (tmp_path / "mem").mkdir()
    turns_file(tmp_path / "agent" / "2026-10-01_a.turns.json",
               "the lighthouse door had a wire trap on it", "the music player should run on the tesla")
    same = corpus.Passage("memory", "archive/grok:1", "chat", "", "turn 1", "Captain: the lighthouse door had a wire trap on it")
    extra = [("memory", "archive/grok:1", 1.0, 3, lambda: [same])]
    idx = index_mod.Index(tmp_path / "s.db", "bge")
    r = idx.refresh(sources(tmp_path), Embed(), extra)
    assert r["passages_new"] == 3
    q = fake_vec("chat\nCaptain: the lighthouse door had a wire trap on it")
    got = idx.candidates(q, 5)
    assert got[0]["text"].endswith("wire trap on it") and len({g["text"] for g in got}) == len(got)
    assert len(got) == 2                                          # the archive copy of the same words is folded into one
    only = idx.candidates(q, 5, {"memory"})
    assert [g["source"] for g in only] == ["memory"]
    assert idx.refresh(sources(tmp_path), Embed(), extra)["files_read"] == 0   # an extra whose stamp is unchanged isn't fetched
