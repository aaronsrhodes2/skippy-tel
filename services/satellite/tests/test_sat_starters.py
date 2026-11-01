"""Conversation starters: the daily gatherer keeps only checked, sourced, figure-free openers; the hub offers one only when the room is idle and
someone is around, a few a day at most, and backs off after a no."""
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

import hub
import housemem
import starters
from hub import FOLLOWUP, IDLE, Room
from timers import Timers

TOOLS = Path(__file__).resolve().parents[3] / "tools"


def refresher():
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location("starter_refresh", TOOLS / "starter_refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stamp(date, hm):
    return time.mktime(time.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M"))


def cand(cid="c1", topic="space", importance=3, kind="science", now=0.0, **k):
    return {"id": cid, "kind": kind, "topic": topic, "opener": f"Hey monkey, {cid} happened. Want to talk about it?", "headline": f"Headline {cid}",
            "importance": importance, "found": now, "expires": now + 3 * 86400, "sources": [{"title": f"Headline {cid}", "source": "example.com", "content": "It happened."}], **k}


def store(tmp_path, items, now):
    s = starters.Starters(tmp_path / "st", clock=lambda: now[0])
    starters.write_json(s.candidates_path, {"items": items})
    return s


# ── the store's rules ──
def test_hours_cap_gap_and_backing_off(tmp_path):
    now = [stamp("2026-10-31", "08:00")]
    s = store(tmp_path, [cand("a", now=now[0]), cand("b", now=now[0]), cand("c", now=now[0]), cand("d", now=now[0])], now)
    assert s.why_not() == "hours" and s.next() is None
    now[0] = stamp("2026-10-31", "10:00")
    first = s.next()
    s.mark_offered(first, "office")
    assert s.why_not() == "too soon"
    now[0] += starters.GAP_S + 1
    second = s.next()
    assert second["id"] != first["id"]
    s.mark_offered(second, "office")
    s.answer(second["id"], "no", "not now")
    assert s.why_not() == "backed off"                                 # a no: done for the day
    now[0] = stamp("2026-11-01", "10:00")
    assert s.why_not() is None and s.next()["id"] not in (first["id"], second["id"])


def test_three_a_day_at_most(tmp_path):
    now = [stamp("2026-10-31", "09:30")]
    s = store(tmp_path, [cand(str(i), now=now[0]) for i in range(5)], now)
    for _ in range(starters.MAX_PER_DAY):
        s.mark_offered(s.next(), "office")
        now[0] += starters.GAP_S + 1
    assert s.why_not() == "enough today"


def test_never_mutes_the_subject_and_two_ignored_end_the_day(tmp_path):
    now = [stamp("2026-10-31", "10:00")]
    s = store(tmp_path, [cand("a", "space", 5, now=now[0]), cand("b", "space", 4, now=now[0]), cand("c", "ai", 1, now=now[0])], now)
    a = s.next()
    s.mark_offered(a, "office")
    s.answer(a["id"], "never", "never talk about space")
    now[0] = stamp("2026-11-01", "10:00")
    assert s.next()["id"] == "c"                                       # space muted for a month
    s.mark_offered(s.next(), "office")
    s.answer("c", "ignored")
    starters.write_json(s.candidates_path, {"items": [cand("d", "ai", now=now[0]), cand("e", "ai", now=now[0])]})
    now[0] += starters.GAP_S + 1
    s.mark_offered(s.next(), "office")
    s.answer("d", "ignored")
    assert s.why_not() == "backed off"


def test_expired_and_already_offered_are_skipped_and_world_wins_a_tie(tmp_path):
    now = [stamp("2026-10-31", "10:00")]
    old = cand("old", now=now[0] - 2 * 86400)
    s = store(tmp_path, [old, cand("sci", now=now[0]), cand("w", kind="world", topic="world", now=now[0])], now)
    assert s.next()["id"] == "w"


def test_usual_hours_need_enough_days(tmp_path):
    for d in ("2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28"):
        housemem.HouseLog(tmp_path, clock=lambda d=d: stamp(d, "19:10")).event("wake", room="office")
    now = stamp("2026-10-31", "12:00")
    assert starters.usual_hours(tmp_path, clock=lambda: now) is None          # four days: not enough to say
    housemem.HouseLog(tmp_path, clock=lambda: stamp("2026-10-29", "19:40")).event("command", room="office", words="x")
    assert starters.usual_hours(tmp_path, clock=lambda: now) == {18, 19, 20}


# ── the room ──
class Link:
    hub_follows_up = True

    def __init__(self):
        self.spoken, self.room, self.released_n = [], None, 0

    async def heard(self, t): pass
    async def released(self): self.released_n += 1
    async def woke(self, by): pass
    async def show(self, t): pass
    async def voice_started(self): pass
    async def voice_stopped(self): pass
    async def idle_face(self): pass

    async def speak(self, said, voice, follow_up=False):
        self.spoken.append(said)

    announce = speak


class Gate:
    def tts(self, text): return b"mp3"
    def summary(self, text): return text


class Brain:
    def __init__(self):
        self.actions, self.heard, self.offers = [], [], []

    def offer(self, text, note): self.offers.append((text, note))

    def reply(self, heard):
        self.heard.append(heard)
        return "More, monkey."


def room_with(tmp_path, now, items):
    s = store(tmp_path, items, now)
    link, brain = Link(), Brain()
    room = Room("office", link, Gate(), brain, Timers(), log=lambda m: None, starters=s, hlog=housemem.HouseLog(tmp_path / "home", clock=lambda: now[0]))
    hub.one_stream = lambda mp3, **k: mp3
    return room, link, brain, s


def present(room, ago_s):
    room._voice_at = time.monotonic() - ago_s


def test_offered_only_when_someone_was_around_and_it_is_quiet(tmp_path):
    now = [stamp("2026-10-31", "11:00")]
    room, link, brain, s = room_with(tmp_path, now, [cand("a", now=now[0])])
    asyncio.run(room.maybe_start())
    assert link.spoken == []                                            # nobody heard all day
    present(room, 10)
    room._starter_check = 0
    asyncio.run(room.maybe_start())
    assert link.spoken == []                                            # someone is talking right now: never over them
    present(room, 300)
    room._starter_check = 0
    asyncio.run(room.maybe_start())
    assert link.spoken == [cand("a")["opener"]] and room.mode == FOLLOWUP and room.pending_starter["id"] == "a"
    assert brain.offers and "It happened." in brain.offers[0][1]
    assert s.state()["offers"][0]["room"] == "office"


def test_never_over_music_or_a_book_or_a_conversation(tmp_path):
    now = [stamp("2026-10-31", "11:00")]
    room, link, brain, s = room_with(tmp_path, now, [cand("a", now=now[0])])
    present(room, 300)
    room.music = type("M", (), {"playing": True})()
    asyncio.run(room.maybe_start())
    room.music, room.book, room._starter_check = None, type("Bk", (), {"playing": True})(), 0
    asyncio.run(room.maybe_start())
    room.book, room._starter_check, room.mode = None, 0, FOLLOWUP
    asyncio.run(room.maybe_start())
    room.mode, room._starter_check, room._turn_at = IDLE, 0, time.monotonic() - 30
    asyncio.run(room.maybe_start())
    assert link.spoken == []


def test_not_now_backs_off_and_yes_goes_to_the_brain(tmp_path):
    now = [stamp("2026-10-31", "11:00")]
    room, link, brain, s = room_with(tmp_path, now, [cand("a", now=now[0]), cand("b", now=now[0])])
    present(room, 300)
    asyncio.run(room.maybe_start())
    asyncio.run(room.handle("not now"))
    assert brain.heard == [] and link.spoken[-1] == "Okay, no problem, Captain." and room.mode == IDLE and link.released_n == 1   # short, no brain
    assert s.state()["offers"][-1]["answer"] == "no" and s.why_not() == "backed off"
    now[0] = stamp("2026-11-01", "11:00")
    room._starter_check = 0
    present(room, 300)
    room._turn_at, room._last_said = None, ("", None)                  # a day later: nothing said lately
    asyncio.run(room.maybe_start())
    asyncio.run(room.handle("yes"))
    assert brain.heard == ["yes. Tell me more about that."] and link.spoken[-1] == "More, monkey."
    assert s.state()["offers"][-1]["answer"] == "yes"
    log = [json.loads(x) for x in (tmp_path / "home" / "log" / "2026-11-01.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["kind"] for e in log] == ["starter", "starter_answer", "command"]


def test_no_answer_is_recorded_as_ignored(tmp_path):
    now = [stamp("2026-10-31", "11:00")]
    room, link, brain, s = room_with(tmp_path, now, [cand("a", now=now[0])])
    present(room, 300)
    asyncio.run(room.maybe_start())
    room.t = room.deadline + 1

    async def tick():
        room._tick()
    asyncio.run(tick())
    assert room.mode == IDLE and room.pending_starter is None and s.state()["offers"][-1]["answer"] == "ignored"


# ── the gatherer ──
def test_gatherer_keeps_only_supported_figure_free_openers(tmp_path):
    sr = refresher()
    found = [{"title": "Probe finds water on moon", "content": "A probe found water.", "url": "https://x/1", "source": "x", "date": ""},
             {"title": "Opinion: space is big", "content": "Op-ed.", "url": "https://x/2", "source": "x", "date": ""},
             {"title": "Third", "content": "c", "url": "https://x/3", "source": "x", "date": ""}]

    def search(q, when):
        return list(found)
    answers = {
        "space": ['{"pick": 1, "importance": 4, "opener": "Hey monkey, a probe found water on the moon. Thirsty?"}', "supported"],
        "physics": ['{"pick": 1, "importance": 4, "opener": "Hey monkey, a probe found water four hundred kilometers down. Neat?"}'],
        "ai": ['{"pick": 1, "importance": 3, "opener": "Hey monkey, a probe found water and aliens. Neat?"}', "unclear"],
        "music": ['{"pick": 0}'],
    }
    order = []

    def ask(prompt):
        key = next(k for k in answers if order and order[-1] == k)
        return answers[key].pop(0)
    subjects = {k: ("science", k, k) for k in answers}

    def ask_tracking(prompt):
        if "Today's headlines" in prompt:
            order.append(next(k for k in answers if answers[k] and k not in order))
        return ask(prompt)
    got = sr.gather(ask_tracking, subjects, {"place": "", "city": "", "state": ""}, search=search, log=lambda m: None)
    assert [c["subject"] for c in got] == ["space"] and got[0]["headline"] == "Probe finds water on moon" and len(got[0]["sources"]) == 3


def test_refresh_writes_candidates_and_drops_offered_and_expired(tmp_path):
    sr = refresher()
    now = [stamp("2026-10-31", "06:45")]
    folder = tmp_path / "st"
    s = starters.Starters(folder, clock=lambda: now[0])
    starters.write_json(s.candidates_path, {"items": [cand("old", now=now[0] - 4 * 86400), cand("keep", now=now[0]), cand("said", now=now[0])]})
    s.mark_offered(cand("said"), "office")
    sr.refresh(folder, lambda p: '{"pick": 0}', {}, search=lambda q, w: [], clock=lambda: now[0], log=lambda m: None)
    assert [c["id"] for c in s.candidates()] == ["keep"]


def test_his_own_words_heard_back_are_not_a_command(tmp_path):
    now = [stamp("2026-10-31", "14:24")]
    room, link, brain, s = room_with(tmp_path, now, [])
    asyncio.run(room.say("News about what? Politics, science, your games, something near home? Pick one, monkey."))
    asyncio.run(room.handle("Politics, science, your games, something near home. Pick one, monkey."))
    assert brain.heard == [] and room.mode == FOLLOWUP                  # the projector's late echo: ignored, still listening
    asyncio.run(room.handle("politics"))
    assert brain.heard == ["politics"]


def test_held_back_when_his_voice_is_down_or_he_just_spoke(tmp_path):
    now = [stamp("2026-10-31", "11:00")]
    room, link, brain, s = room_with(tmp_path, now, [cand("a", now=now[0])])
    present(room, 300)

    class Down:
        def tts(self, text): raise hub.GateError("the voice gate is not answering")
        def summary(self, text): return text
    room.gate, room.local_voice = Down(), None
    asyncio.run(room.maybe_start())
    assert link.spoken == [] and s.state()["offers"] == [] and room.mode == IDLE   # nothing spent
    room.gate, room._starter_check = Gate(), 0
    room._last_said = ("Good morning, Captain.", time.monotonic() - 20)             # a routine just spoke
    asyncio.run(room.maybe_start())
    assert link.spoken == []
    room._last_said, room._starter_check = ("Good morning, Captain.", time.monotonic() - 400), 0
    asyncio.run(room.maybe_start())
    assert link.spoken == [cand("a")["opener"]]


def test_a_long_reply_is_voiced_piece_by_piece_and_a_busy_refusal_is_retried(tmp_path):
    now = [stamp("2026-10-31", "11:00")]
    room, link, brain, s = room_with(tmp_path, now, [])
    calls, busy = [], [True]

    class Studio:
        def tts(self, text):
            calls.append(text)
            if busy[0]:
                busy[0] = False
                raise hub.GateError("text to speech refused (503)")
            return b"mp3"
        def summary(self, text): return text
    room.gate = Studio()
    voice = asyncio.run(room._voice("A sentence about the news. " * 40))
    assert voice[0] == "mp3" and len(calls) >= 3 and calls[0] == calls[1]          # retried, then every piece in turn


def test_a_busy_studio_gets_a_stocked_quip_instead_of_silence(tmp_path):
    now = [stamp("2026-10-31", "11:00")]
    room, link, brain, s = room_with(tmp_path, now, [])
    busy = [True]

    class Studio:
        def tts(self, text):
            if busy[0]:
                busy[0] = False
                raise hub.GateError("text to speech refused (429)")
            return b"mp3"
        def summary(self, text): return text

    class Bank:
        def has(self, kind): return kind == "fact:space"
        def pick(self, kind, command=""): return (f"a {kind} line", b"q")
    room.gate, room.filler = Studio(), Bank()
    assert asyncio.run(room._voice("A new moon was found around a distant planet."))[0] == "mp3"
    assert link.spoken == ["a fact:space line"]


def test_a_restart_remembers_who_was_around_from_the_house_log(tmp_path):
    now = [stamp("2026-10-31", "11:00")]
    room, link, brain, s = room_with(tmp_path, now, [cand("a", now=now[0])])
    s.log_root = tmp_path / "home"
    housemem.HouseLog(s.log_root, clock=lambda: now[0] - 600).event("command", room="office", words="you are a jerk")
    asyncio.run(room.maybe_start())                                     # no voice heard since the restart, but he spoke ten minutes ago
    assert link.spoken == [cand("a")["opener"]]
    assert s.last_seen() is not None


def test_an_unanswered_starter_left_by_the_watchdog_is_ignored_after_a_while(tmp_path):
    now = [stamp("2026-10-31", "11:00")]
    room, link, brain, s = room_with(tmp_path, now, [cand("a", now=now[0]), cand("b", now=now[0])])
    present(room, 300)
    asyncio.run(room.maybe_start())
    room._resume(IDLE)                                                  # the no-audio watchdog: back to rest, the starter still pending
    room._starter_at -= hub.STARTER_ANSWER_S + 1
    room._starter_check = 0
    asyncio.run(room.maybe_start())
    assert room.pending_starter is None and s.state()["offers"][0]["answer"] == "ignored"
