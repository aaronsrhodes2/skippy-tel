"""The quip inventory (filler.FillerBank) and the daily refresher (tools/quip_refresh.py): never the same quip twice for one question, the least
played first, a quip retired after three plays and replaced by a new one in his voice."""
import json
import sys
from pathlib import Path

import filler

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import quip_refresh as qr  # noqa: E402


def bank(tmp_path, n=4, kind="thinking"):
    items = []
    for i in range(n):
        f = f"{kind}/{i:02d}.mp3"
        (tmp_path / kind).mkdir(exist_ok=True)
        (tmp_path / f).write_bytes(b"mp3-%d" % i)
        items.append({"file": f, "text": f"line {i}"})
    (tmp_path / "index.json").write_text(json.dumps({kind: items}), encoding="utf-8")
    t = {"now": 1000.0}
    b = filler.FillerBank(tmp_path, json.loads((tmp_path / "index.json").read_text()), clock=lambda: t["now"])
    return b, t


def test_never_the_same_quip_twice_for_the_same_question(tmp_path):
    b, t = bank(tmp_path)
    said = []
    for _ in range(4):
        t["now"] += 10
        said.append(b.pick("thinking", "What time is it?")[0])
    assert len(set(said)) == 4                                   # four asks, four different quips


def test_the_least_played_goes_first_and_the_inventory_is_kept(tmp_path):
    b, t = bank(tmp_path, n=3)
    for _ in range(6):
        t["now"] += 10
        b.pick("thinking", "")
    assert sorted(b.uses(f"thinking/{i:02d}.mp3") for i in range(3)) == [2, 2, 2]
    saved = json.loads((tmp_path / "plays.json").read_text())
    assert saved["thinking/00.mp3"]["n"] == 2


def test_a_worn_out_quip_is_used_only_when_nothing_else_is_left(tmp_path):
    b, t = bank(tmp_path, n=3)
    b.plays = {"thinking/00.mp3": {"n": 3, "at": 1, "asked": []}}
    said = set()
    for _ in range(2):
        t["now"] += 10
        said.add(b.pick("thinking", "")[0])
    assert said == {"line 1", "line 2"}                          # the worn one waits while fresh ones remain


def test_the_bank_picks_up_the_refreshers_new_index_without_a_restart(tmp_path):
    import os
    b, t = bank(tmp_path, n=1)
    (tmp_path / "thinking" / "new.mp3").write_bytes(b"x")
    idx = {"thinking": [{"file": "thinking/00.mp3", "text": "line 0"}, {"file": "thinking/new.mp3", "text": "fresh"}]}
    (tmp_path / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    os.utime(tmp_path / "index.json", (2e9, 2e9))
    b.plays = {"thinking/00.mp3": {"n": 1, "at": 1, "asked": []}}
    assert b.pick("thinking", "")[0] == "fresh"


def test_the_refresher_retires_worn_quips_and_writes_new_ones_in_his_voice(tmp_path):
    (tmp_path / "thinking").mkdir()
    (tmp_path / "thinking" / "00.mp3").write_bytes(b"a")
    (tmp_path / "thinking" / "01.mp3").write_bytes(b"b")
    (tmp_path / "index.json").write_text(json.dumps({"thinking": [{"file": "thinking/00.mp3", "text": "Old and tired."},
                                                                  {"file": "thinking/01.mp3", "text": "Still fresh."}]}))
    (tmp_path / "plays.json").write_text(json.dumps({"thinking/00.mp3": {"n": 3}}))
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return '["Old and tired.", "The answer is 42.", "Hold on, monkey. Brilliance loading.", "Patience. I am busy being amazing."]'
    rep = qr.refresh(tmp_path, ask, lambda text: b"MP3:" + text.encode(), budget=2, log=lambda *a: None)
    idx = json.loads((tmp_path / "index.json").read_text())["thinking"]
    texts = [e["text"] for e in idx]
    assert "Old and tired." not in texts and "Still fresh." in texts
    assert "Hold on, monkey. Brilliance loading." in texts and "The answer is 42." not in texts     # repeats and answers are refused
    assert (tmp_path / "retired" / "thinking" / "00.mp3").exists()                                # moved, never deleted
    assert json.loads((tmp_path / "retired.json").read_text())["thinking"][0]["text"] == "Old and tired."
    assert rep["retired"] == 1 and sum(rep["written"].values()) == 2 and "Old and tired." in asked[0]


def test_a_dry_run_changes_nothing(tmp_path):
    b, _ = bank(tmp_path)
    before = (tmp_path / "index.json").read_text()
    qr.refresh(tmp_path, lambda p: "[]", lambda t: b"", budget=5, dry=True, log=lambda *a: None)
    assert (tmp_path / "index.json").read_text() == before and not (tmp_path / "retired.json").exists()


def test_factoids_are_kept_only_when_web_sources_support_them_and_carry_no_figures(tmp_path):
    """2026-10-31: the model checking itself passed a false factoid; now each claim is searched and judged against the snippets."""
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if prompt.startswith("A claim, and what a web search found"):
            return "contradicted" if "Great Wall" in prompt else "supported"
        return ('["Fun fact: Venus spins backwards compared to most planets.", "Fun fact: the Great Wall is visible from space.", '
                '"Fun fact: light from the Sun takes eight minutes to reach you.", "Fun fact: it is 3 degrees on Pluto.", '
                '"Fun fact: nobody has searched this one."]')

    def search(q):
        return [] if "nobody" in q else [f"Source about {q}"]
    lines = qr.write_lines(ask, "fact:space", 3, [], [], search=search)
    assert lines == ["Fun fact: Venus spins backwards compared to most planets."]     # a myth, a figure and an unsourced claim never get in
    assert not any("eight minutes" in c or "3 degrees" in c for c in calls[1:])


def test_a_factoid_is_chosen_by_subject():
    assert filler.topic_for("how far away is the moon") == "space"
    assert filler.topic_for("play some Daft Punk") == "music"
    assert filler.topic_for("roll a d20 for me") == "tabletop"
    assert filler.topic_for("hello there") is None
    assert "fact:space" in qr.kinds_of({"thinking": []})
