"""Mood words: "something relaxing" asks for the feeling tags in Traktor's collection (stage8l_mood_tags.py writes them, dj.mood_index reads them)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dj  # noqa: E402
import mixes  # noqa: E402

NML = """<?xml version='1.0' encoding='utf-8'?>
<NML VERSION="20"><COLLECTION ENTRIES="3">
<ENTRY TITLE="A" ARTIST="X"><LOCATION DIR="/:Users/:me/:corrected_music/:Art/:Alb/:" FILE="a.mp3"/><INFO COMMENT2="theme | &#9873;flag | &#9834;calm &#9834;tender"/></ENTRY>
<ENTRY TITLE="B" ARTIST="X"><LOCATION DIR="/:Users/:me/:corrected_music/:Art/:Alb/:" FILE="b.mp3"/><INFO COMMENT2="&#9834;angry &#9834;energetic"/></ENTRY>
<ENTRY TITLE="C" ARTIST="X"><LOCATION DIR="/:Users/:me/:corrected_music/:Art/:Alb/:" FILE="c.mp3"/><INFO COMMENT2="theme"/></ENTRY>
</COLLECTION></NML>"""


def test_mood_phrase_is_only_a_feeling():
    assert mixes.mood_phrase("relaxing music") == "relaxing"
    assert mixes.mood_phrase("some chill music for Khan") == "chill"
    assert mixes.mood_phrase("sad songs") == "sad"
    assert mixes.mood_phrase("heavy metal") is None and mixes.mood_phrase("Pink Floyd") is None and mixes.mood_phrase("dark ambient") is None


def test_mood_index_reads_the_tag_segment_and_caches(tmp_path):
    nml = tmp_path / "c.nml"
    nml.write_text(NML, encoding="utf-8")
    got = dj.mood_index(str(nml), tmp_path / "state")
    assert got == {"Art/Alb/a.mp3": ["calm", "tender"], "Art/Alb/b.mp3": ["angry", "energetic"]}
    assert (tmp_path / "state" / "mood-index.json").exists()
    assert dj.mood_index(str(nml), tmp_path / "state") == got


def test_resolve_keeps_only_tracks_that_feel_it_and_says_so_when_none_do():
    catalog = {p: (p, "X", "Alb", "Rock", 0) for p in ("Art/Alb/a.mp3", "Art/Alb/b.mp3", "Art/Alb/c.mp3")}
    moods = {"Art/Alb/a.mp3": ["calm", "tender"], "Art/Alb/b.mp3": ["angry"]}
    q, note, _o = mixes.resolve({"mood": "relaxing", "shuffle": True}, catalog, moods=moods)
    assert q == ["Art/Alb/a.mp3"] and not note
    q, note, _o = mixes.resolve({"mood": "party", "shuffle": True}, catalog, moods=moods)
    assert len(q) == 3 and "party" in note                      # nobody tagged it: the closest is the shuffle, said in the note
