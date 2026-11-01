"""The house's alarm clock (alarm_clock.py): a morning alarm rings upstairs, louder each minute until answered, his phone after a few
minutes; Stop from the phone or a word to any pod wins everywhere; it outlives a restart; DST and midnight; the bedtime line."""
import asyncio
import json
import time

import alarm_clock
import alarms as alarms_mod
import house_layout
from alarm_clock import AlarmClock

LAYOUT = house_layout.Layout({
    "floors": {"downstairs": {"rooms": ["theater"]}, "upstairs": {"rooms": ["living room", "bedroom"]}},
    "rooms": {"theater": {"pods": ["theater"], "outputs": ["hdmi"]}, "living room": {"outputs": ["enclave"]}, "bedroom": {"pods": ["bedroom"]}},
})


class Router:
    def __init__(self, open_=("enclave", "hdmi")):
        self.open = set(open_)

    def available(self, k):
        return k in self.open


class Hub:
    def __init__(self, rooms=()):
        self.rooms = dict(rooms)


class Pod:
    def __init__(self):
        self.rang, self.said = [], []

    async def ring(self, tone, voice, text):
        self.rang.append(text)

    async def announce(self, text):
        self.said.append(text)


def make(tmp_path, now, router=None, rooms=(), env=None):
    posted = []

    def post(path, body):
        posted.append((path, body))
        return {"sent": True} if path == "/home/alert" else {"commands": [], "ack": []}
    c = AlarmClock(tmp_path, env or {}, clock=lambda: now[0], log=lambda m: None, router=router or Router(), post=post, layout=LAYOUT)
    c.hub = Hub(rooms)
    played = []

    async def play(kinds, clips, gain):
        played.append((tuple(kinds), len(clips), gain))
    c._play_outs = play

    async def voice(text):
        return b"mp3"
    c._voice = voice
    return c, posted, played


def at(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def run(coro):
    return asyncio.run(coro)


def test_a_morning_alarm_set_in_the_theater_rings_upstairs_louder_each_minute_and_his_phone_hears_it(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    pod = Pod()
    c, posted, played = make(tmp_path, now, rooms={"theater": pod})
    _, run_tool = c.tools("theater")
    assert "ringing upstairs" in run_tool("set_alarm", {"time": "06:30"})
    a = c.alarms.items[0]
    assert a.where == "upstairs" and a.room == "theater" and c.place_of(a) == (["enclave"], [])
    assert run_tool("set_alarm", {"time": "18:00"}).endswith("PM.") and c.alarms.items[1].where == ""     # an evening alarm rings where it was set
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())
    assert c.ringing and played[-1][0] == ("enclave",) and pod.rang == []            # upstairs only: the theater pod is downstairs
    gains = [played[-1][2]]
    for minute in range(1, 6):
        now[0] += 60
        run(c.tick())
        gains.append(played[-1][2])
    assert gains == sorted(gains) and gains[0] < gains[-1] == 1.0                    # louder each ring, up to full
    alerts = [b["text"] for p, b in posted if p == "/home/alert"]
    assert len(alerts) == 1 and "haven't answered" in alerts[0] and alarm_clock.PORTAL_PAGE in alerts[0]
    assert c.view()["ringing"]["hhmm"] == "06:30" and c.view()["fired"][-1]["ended"] is None


def test_stop_from_the_phone_wins_everywhere_and_a_word_to_a_pod_then_finds_nothing_ringing(tmp_path):
    now = [at(2026, 11, 2, 6, 59)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "07:00", repeat="weekdays")
    now[0] += 61
    run(c.tick())
    assert c.ringing is not None
    c.post = lambda path, body: {"commands": [{"id": "c1", "op": "stop"}], "ack": []}
    run(c.sync())
    assert c.ringing is None and c.fired[-1]["ended"] == "stopped" and c.fired[-1]["by"] == "phone" and c.done == [{"id": "c1", "said": "Alarm off."}]
    run(c.sync())                                                                     # the same command again: done once
    assert len(c.done) == 1
    assert c.answered("stop") is None                                                 # nothing rings now: the brain hears it
    n = len(played)
    now[0] += 120
    run(c.tick())
    assert len(played) == n                                                           # no more rings
    assert c.alarms.items[0].due == at(2026, 11, 3, 7, 0)                             # tomorrow's weekday


def test_snooze_by_voice_rings_again_in_nine_minutes_in_the_same_place(tmp_path):
    now = [at(2026, 11, 2, 6, 29)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "06:30", where="everywhere")
    now[0] += 61
    run(c.tick())
    assert "Snoozed" in c.answered("snooze", by="theater") and c.ringing is None
    s = c.alarms.items[0]
    assert s.where == "everywhere" and round(s.due - now[0]) == 9 * 60
    now[0] += 9 * 60 + 1
    run(c.tick())
    assert c.ringing is not None and played[-1][0] == ("enclave", "hdmi")


def test_an_unanswered_alarm_gives_up_after_half_an_hour_and_says_so(tmp_path):
    now = [at(2026, 11, 2, 6, 29)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "06:30")
    now[0] += 61
    for _ in range(32):
        run(c.tick())
        now[0] += 60
    texts = [b["text"] for p, b in posted if p == "/home/alert"]
    assert c.ringing is None and c.fired[-1]["ended"] == "gave_up" and "nobody answered" in texts[-1] and len(texts) == 3


def test_the_alarms_outlive_a_restart_and_the_old_per_room_files_become_the_houses(tmp_path):
    now = [at(2026, 11, 2, 5, 0)]
    (tmp_path / "alarms").mkdir()
    old = alarms_mod.Alarms(tmp_path / "alarms" / "theater.json", clock=lambda: now[0])
    old.add("alarm", "06:30", repeat="daily")
    c, _, _ = make(tmp_path, now)
    assert [(a.hhmm, a.room) for a in c.alarms.items] == [("06:30", "theater")] and (tmp_path / "alarms" / "theater.merged").exists()
    c.alarms.add("alarm", "07:15")
    again, _, _ = make(tmp_path, now)
    assert sorted(a.hhmm for a in again.alarms.items) == ["06:30", "07:15"]
    assert json.loads((tmp_path / "alarms" / "house.json").read_text())[1]["where"] == "upstairs"


def test_a_daily_alarm_keeps_its_clock_time_across_dst_and_midnight():
    t = at(2026, 10, 30, 22, 0)
    hits = []
    for _ in range(400):                                                              # over a year: every DST change of this machine's zone
        t = alarms_mod.next_at("07:00", alarms_mod.DAYS, t)
        hits.append(t)
        lt = time.localtime(t)
        assert (lt.tm_hour, lt.tm_min) == (7, 0)
    assert len({time.strftime("%Y-%m-%d", time.localtime(x)) for x in hits}) == 400   # one a day, none skipped or doubled
    late = at(2026, 11, 2, 23, 59)
    assert time.strftime("%d %H:%M", time.localtime(alarms_mod.next_at("00:01", [], late))) == "03 00:01"


def test_bedtime_says_the_alarm_or_that_there_is_none_and_warns_when_upstairs_is_off(tmp_path):
    now = [at(2026, 11, 1, 22, 30)]
    c, posted, _ = make(tmp_path, now, router=Router(open_=("hdmi",)))
    assert c.tomorrow_line() == "No alarm is set for tomorrow."
    c.alarms.add("alarm", "06:30", repeat="weekdays")
    line = c.tomorrow_line()
    assert line.startswith("Your alarm is set for 6:30.") and "upstairs speaker isn't connected" in line
    c.router.open.add("enclave")
    assert c.tomorrow_line() == "Your alarm is set for 6:30."


def test_the_evening_check_messages_his_phone_when_the_alarm_could_not_be_heard(tmp_path):
    now = [at(2026, 11, 1, 21, 0)]
    c, posted, _ = make(tmp_path, now, router=Router(open_=()))
    c.alarms.add("alarm", "06:30")
    run(c.evening_check())
    assert posted == []
    now[0] = at(2026, 11, 1, 21, 31)
    run(c.evening_check())
    run(c.evening_check())
    texts = [b["text"] for p, b in posted if p == "/home/alert"]
    assert len(texts) == 1 and "06:30" in texts[0]


def test_a_timer_whose_pod_is_away_is_still_said_upstairs(tmp_path):
    now = [at(2026, 11, 2, 12, 0)]
    c, posted, played = make(tmp_path, now, env={"SATELLITE_ANNOUNCE_OUT": "enclave", "SATELLITE_OUT_ENCLAVE": "tcp://x"})
    c.timers_for("theater").add(60, "pasta")
    now[0] += 61
    import speaker_out
    speaker_out_kinds = speaker_out.announce_kinds
    speaker_out.announce_kinds = lambda env: ["enclave"]
    try:
        run(c.tick())
    finally:
        speaker_out.announce_kinds = speaker_out_kinds
    assert played and played[-1][0] == ("enclave",)


def test_a_pod_room_with_the_house_clock_answers_stop_without_the_brain(tmp_path, monkeypatch):
    import hub
    from hub import Room
    from test_sat_starters import Brain, Gate, Link
    monkeypatch.setattr(hub, "one_stream", lambda mp3, **k: mp3)
    now = [at(2026, 11, 2, 6, 29)]
    c, _, _ = make(tmp_path, now)
    link, brain = Link(), Brain()
    r = Room("theater", link, Gate(), brain, c.timers_for("theater"), log=lambda m: None)
    r.alarms, r.clock = c.alarms, c
    c.alarms.add("alarm", "06:30")
    now[0] += 61
    run(c.tick())
    run(r.timers_due())                                                               # the room no longer rings it itself
    run(r.handle("okay I'm up"))
    assert brain.heard == [] and link.spoken[-1] == "Alarm off." and c.ringing is None


def test_the_bedtime_routine_says_tomorrows_alarm_before_its_goodnight(tmp_path):
    from test_sat_daily import make as make_daily
    now = [at(2026, 11, 1, 22, 30)]
    d, room, *_ = make_daily(tmp_path, now)
    c, _, _ = make(tmp_path / "clock", now)
    c.alarms.add("alarm", "06:30")

    async def line():
        return c.tomorrow_line()
    d.extras["alarm_tomorrow"] = line
    steps = d.bedtime_steps(room)
    said = run(steps[-2].make())
    assert said == "Your alarm is set for 6:30." and steps[-1].last
