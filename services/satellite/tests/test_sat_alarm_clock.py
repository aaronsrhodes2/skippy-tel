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
    def __init__(self, stereo=()):
        self.rang, self.said, self.quiet, self.waited = [], [], [], []
        self.stereo = list(stereo)              # the PC outputs that are the room's one voice and on now (hub.Room.stereo_kinds)

    def stereo_kinds(self):
        return list(self.stereo)

    async def ring(self, tone, voice, text, quiet=False, wait=None):
        self.rang.append(text)
        self.quiet.append(quiet)
        if quiet and wait is not None:          # what hub.Room.ring does: wait for the stereo's ring, and learn whether it sounded
            self.waited.append(await wait)

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
        return True                                     # something sounded (AlarmClock._play_outs)
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
    said = run(steps[-2].act())                                       # (an act, worked out in turn after plan_tomorrow, not a make started with the routine)
    assert said == "Your alarm is set for 6:30." and steps[-1].last and steps[-2].make is None


def test_plain_alarm_requests_go_to_the_tool_before_the_model():
    assert alarms_mod.pre_route("set an alarm for 7:00 AM every day") == ("set_alarm", {"time": "7:00am", "repeat": "daily"})
    assert alarms_mod.pre_route("Skippy, wake me up at 6:30 every weekday upstairs") == ("set_alarm", {"time": "6:30", "repeat": "weekdays", "where": "upstairs"})
    assert alarms_mod.pre_route("what alarms do I have?") == ("list_alarms", {})
    assert alarms_mod.pre_route("cancel the 7 o'clock alarm") == ("cancel_alarm", {"what": "7 o'clock", "kind": "alarm"})
    assert alarms_mod.pre_route("turn off the alarm") is None and alarms_mod.pre_route("set a timer for 5 minutes") is None
    a = alarms_mod.Alarms(None, clock=lambda: at(2026, 11, 1, 15, 0), wake_place="upstairs")
    tool, args = alarms_mod.pre_route("set an alarm for 7:00 AM every day")
    assert a.run(tool, args).startswith("Alarm set for tomorrow at 7 AM, every day, ringing upstairs")


def test_the_wake_word_stops_a_ringing_alarm_at_once_and_snooze_or_stop_said_next_still_answer(tmp_path):
    # the Captain, 2026-11-01: "Make it so alarms stop as soon as the wake word is invoked"
    now = [at(2026, 11, 2, 6, 59)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "07:00", repeat="weekdays")
    now[0] += 61
    run(c.tick())
    assert c.woke(by="theater") is True and c.ringing is None and c.fired[-1]["ended"] == "stopped"
    assert c.woke() is False                                                          # nothing rings now
    now[0] += 5
    assert c.snooze_after_wake("snooze", by="theater").startswith("Fine. Snoozed")     # "Skippy ... snooze", said within the 30 s
    assert c.snooze_after_wake("snooze") is None                                       # once only
    n = len(played)
    now[0] += 120
    run(c.tick())
    assert len(played) == n                                                           # silent from the word on
    now[0] += 9 * 60 - 120
    run(c.tick())
    assert c.ringing is not None and c.woke() is True
    assert c.snooze_after_wake("stop") == "Alarm off."                                 # "Skippy, stop": already off, said so
    assert c.snooze_after_wake("what's the weather") is None                           # anything else is for the brain


def test_an_alarm_named_alarm_is_not_said_alarm_alarm():
    a = alarms_mod.Alarms(None, clock=lambda: at(2026, 11, 2, 17, 35), log=lambda m: None)
    x = a.add("alarm", "17:35", "test alarm")
    assert "test alarm alarm" not in a.spoken(x) and "Your test alarm." in a.spoken(x)
    assert alarms_mod.timer_name("pasta timer") == "pasta" and alarms_mod.timer_name("five minute") == "five minute"


def test_a_room_renamed_still_rings_what_was_set_in_it(tmp_path):
    """The theater pod moved up to the living room (2026-11-01): its alarms, set as 'theater', ring on it as 'living', and upstairs finds it."""
    now = [at(2026, 11, 2, 6, 0)]
    pod = Pod()
    layout = house_layout.Layout({"floors": {"upstairs": {"rooms": ["living room"]}},
                                  "rooms": {"living room": {"pods": ["living"], "outputs": ["enclave"]}}})
    c, _, _ = make(tmp_path, now, rooms={"living": pod}, env={"SATELLITE_ROOM_ALIASES": "theater=living"})
    c.layout = layout
    here = c.alarms.add("alarm", "06:30", where="", room="theater")
    assert c.place_of(here)[1] == ["living"]
    up = c.alarms.add("alarm", "07:00", where="upstairs", room="theater")
    assert c.place_of(up) == (["enclave"], ["living"])
    assert c.hub_room("living") == "living" and c.hub_room("den") == "den"


# ── the box's own speaker is the last resort (the Captain, 2026-11-04: "we never want to output to the hub speakers unless that house level's
#    stereo is unavailable") ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

UPSTAIRS = house_layout.Layout({"floors": {"upstairs": {"rooms": ["living room"]}},
                                "rooms": {"living room": {"pods": ["living"], "outputs": ["enclave"]}}})


def upstairs_clock(tmp_path, monkeypatch, pod, where="upstairs", at_hhmm=(6, 30), kind="alarm"):
    monkeypatch.setattr(alarm_clock, "LISTEN_AFTER_S", 0)
    now = [at(2026, 11, 2, 6, 0)]
    c, _, played = make(tmp_path, now, rooms={"living": pod})
    c.layout = UPSTAIRS
    a = c.alarms.add(kind, f"{at_hhmm[0]:02d}:{at_hhmm[1]:02d}", "take the pills" if kind == "reminder" else "", where=where, room="living")
    now[0] = at(2026, 11, 2, *at_hhmm)
    return c, now, played, a


def ticks(c, now, count):
    for _ in range(count):
        run(c.tick())
        now[0] += 60


def test_a_box_whose_stereo_is_on_rings_silently_until_the_third_ring(tmp_path, monkeypatch):
    pod = Pod(stereo=["enclave"])
    c, now, played, _ = upstairs_clock(tmp_path, monkeypatch, pod)
    ticks(c, now, 4)
    assert pod.quiet == [True, True, False, False]                       # the stereo rings alone for two minutes, then the box joins
    assert [p[0] for p in played] == [("enclave",)] * 4                  # and the stereo rings every time
    assert pod.waited == [True, True]                                    # a silent box is handed the stereo's ring: it waits for it, and learns it sounded


def test_an_alarm_set_without_a_place_keeps_ringing_the_boxs_stereo_when_the_box_joins(tmp_path, monkeypatch):
    pod = Pod(stereo=["enclave"])
    c, now, played, a = upstairs_clock(tmp_path, monkeypatch, pod, where="", at_hhmm=(18, 0))
    assert c.place_of(a) == ([], ["living"])                             # no announcement output in this vault: before, only the box rang
    ticks(c, now, 4)
    assert [p[0] for p in played] == [("enclave",)] * 4                  # the stereo does not drop out when the box joins at ring 3
    assert pod.quiet == [True, True, False, False]


def test_a_reminder_is_rung_on_the_box_at_its_last_ring(tmp_path, monkeypatch):
    """A reminder rings twice and has no phone alert behind it: the box joins on the second, or a stereo that is on but inaudible loses it."""
    pod = Pod(stereo=["enclave"])
    c, now, played, _ = upstairs_clock(tmp_path, monkeypatch, pod, kind="reminder")
    ticks(c, now, 3)
    assert pod.quiet == [True, False] and [p[0] for p in played] == [("enclave",)] * 2 and c.ringing is None


def test_a_box_whose_stereo_is_off_rings_loud_from_the_first_ring(tmp_path, monkeypatch):
    pod = Pod(stereo=[])
    c, now, played, _ = upstairs_clock(tmp_path, monkeypatch, pod)
    ticks(c, now, 2)
    assert pod.quiet == [False, False] and pod.waited == []              # nothing else can ring: the box does
    assert [p[0] for p in played] == [("enclave",)] * 2                   # the place's own output still gets its try


def test_the_box_is_handed_the_stereos_own_ring_and_what_became_of_it(tmp_path, monkeypatch):
    """hub.Room.ring waits for this task, stays deaf until it is done, and speaks itself when it answers False."""
    events = []
    pod = Pod(stereo=["enclave"])
    c, now, played, _ = upstairs_clock(tmp_path, monkeypatch, pod)

    async def playing(kinds, clips, gain):
        events.append("playing")
        await asyncio.sleep(0.01)
        events.append("played")
        return False                                                     # nothing sounded: the router dropped the clip
    c._play_outs = playing
    real_ring = pod.ring

    async def noting_ring(tone, voice, text, quiet=False, wait=None):
        events.append("box rings")
        await real_ring(tone, voice, text, quiet=quiet, wait=wait)
        events.append("box done")
    pod.ring = noting_ring
    run(c.tick())
    assert pod.waited == [False] and events[-1] == "box done" and events.index("played") < events.index("box done")


class FloorPod(Pod):
    """A pod with a real voice lock, as hub.Room has: the clock takes it for the whole ring and says so (floor=True)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.voice = asyncio.Lock()
        self.floors = []

    async def ring(self, tone, voice, text, quiet=False, wait=None, floor=False):
        self.floors.append((floor, self.voice.locked()))
        await super().ring(tone, voice, text, quiet=quiet, wait=wait)


def test_a_ring_waits_for_the_line_being_said_and_holds_the_floor_while_it_rings(tmp_path, monkeypatch):
    """The Captain, 2026-11-05: at 07:10 the alarm's rings were spoken over the morning routine's lines, because only the box's part of a ring waited for
    the room's voice lock and the Enclave's part did not."""
    pod = FloorPod(stereo=["enclave"])
    c, now, played, _ = upstairs_clock(tmp_path, monkeypatch, pod)
    events = []

    async def play(kinds, clips, gain):
        events.append(("stereo", pod.voice.locked()))
        return True
    c._play_outs = play

    async def go():
        await pod.voice.acquire()                                       # a routine's line is being said
        ring = asyncio.ensure_future(c.tick())
        await asyncio.sleep(0.05)
        over_it = list(events)
        pod.voice.release()                                             # the line ends
        await ring
        return over_it
    assert asyncio.run(go()) == []                                      # nothing rang over the line
    assert events == [("stereo", True)] and pod.floors == [(True, True)] and not pod.voice.locked()       # then the stereo and the box rang holding the floor, and let it go


def test_a_line_that_goes_on_too_long_is_rung_over_and_its_lock_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(alarm_clock, "FLOOR_WAIT_S", 0.05)
    pod = FloorPod(stereo=["enclave"])
    logged = []
    c, now, played, _ = upstairs_clock(tmp_path, monkeypatch, pod)
    c.log = logged.append

    async def go():
        await pod.voice.acquire()                                       # someone talks and talks
        await c.tick()
        return pod.voice.locked()
    still_theirs = asyncio.run(go())
    assert c.ringing is not None and [p[0] for p in played] == [("enclave",)]     # an alarm must wake him: it rang
    assert still_theirs and any("still talking" in m for m in logged)             # and it let go of nothing it never held


def test_the_days_first_alarm_comes_from_the_schedule_not_from_what_is_due(tmp_path):
    """daily.Daily plans the morning briefing around it; a repeating alarm's `due` is next week's once it has rung, and a reminder is never the alarm."""
    now = [at(2026, 11, 5, 6, 0)]                                       # a Thursday
    c, _, _ = make(tmp_path, now)
    c.alarms.add("alarm", "07:00", repeat="mon,wed,thu", where="upstairs")
    c.alarms.add("alarm", "08:30", repeat="tue", where="upstairs")
    c.alarms.add("reminder", "06:00", "take the pills", repeat="daily")
    c.alarms.add("alarm", "09:15", date="2026-11-07", where="upstairs")
    assert c.wake_time("2026-11-05") == "07:00" and c.wake_time("2026-11-03") == "08:30"
    assert c.wake_time("2026-11-06") is None and c.wake_time("2026-11-07") == "09:15" and c.wake_time("garbage") is None
    assert not c.is_ringing()
    now[0] = at(2026, 11, 5, 7, 0)
    run(c.tick())
    assert c.is_ringing() and c.wake_time("2026-11-05") == "07:00"      # it has rung, and today's answer has not moved


def test_a_stereo_that_cannot_be_looked_at_never_keeps_the_alarm_quiet(tmp_path, monkeypatch):
    class Broken(Pod):
        def stereo_kinds(self):
            raise RuntimeError("router gone")
    pod = Broken()
    c, now, played, _ = upstairs_clock(tmp_path, monkeypatch, pod)
    run(c.tick())
    assert pod.quiet == [False] and c.ringing is not None               # the box rings; the place's own output still gets its try
    assert played[-1][0] == ("enclave",)


def test_an_alarm_that_names_a_place_with_no_box_rings_as_before(tmp_path, monkeypatch):
    pod = Pod(stereo=["enclave"])
    c, now, played, a = upstairs_clock(tmp_path, monkeypatch, pod, where="downstairs")
    assert c.place_of(a)[1] == []                                        # downstairs has no box in this layout: only its outputs ring
    run(c.tick())
    assert pod.rang == [] and pod.waited == [] and c.ringing is not None


def test_the_real_play_outs_says_whether_anything_sounded(tmp_path):
    """The answer the silent box waits for: True when a clip started, False when nothing did (no router, nothing open, a dropped clip)."""
    import speaker_out
    now = [at(2026, 11, 2, 6, 0)]
    c = AlarmClock(tmp_path, {}, clock=lambda: now[0], log=lambda m: None, router=Router(open_=("enclave",)), layout=LAYOUT)
    plays = []

    class Out:
        def __init__(self, router, kinds, **kw):
            self.answers = plays

        async def play(self, clip, gain=1.0):
            return self.answers.pop(0)
    real, speaker_out.RoutedOut = speaker_out.RoutedOut, Out
    try:
        plays[:] = [True, True]
        assert run(c._play_outs(["enclave"], [b"RIFFtone", b"mp3"], 0.5)) is True
        plays[:] = [False, False]
        assert run(c._play_outs(["enclave"], [b"RIFFtone", b"mp3"], 0.5)) is False
        plays[:] = [False, True]
        assert run(c._play_outs(["enclave"], [b"RIFFtone", b"mp3"], 0.5)) is True          # the chime dropped, the words sounded
        assert run(c._play_outs(["hdmi"], [b"RIFFtone"], 0.5)) is False                     # nothing of it is open
        c.router = None
        assert run(c._play_outs(["enclave"], [b"RIFFtone"], 0.5)) is False
    finally:
        speaker_out.RoutedOut = real


# ── more than one thing falls due (review of 2026-11-05: tick() let the LAST item due ring and ended the others as "replaced") ──────────────────

def recording_voice(c):
    """The words each ring is voiced with (make() stubs the voice with one fixed clip)."""
    said = []

    async def voice(text):
        said.append(text)
        return b"mp3"
    c._voice = voice
    return said


def test_an_alarm_and_a_reminder_due_in_the_same_minute_ring_as_one_and_the_alarm_is_never_the_one_dropped(tmp_path):
    """A 'take the pills' reminder added after the wake alarm and due at the same minute made the reminder the one that rang: twice, then it gave up, and the
    alarm, ended as 'replaced', never sounded anywhere. Now one ring says both, and it is the alarm's (30 minutes, louder each time, his phone)."""
    for order in (("alarm", "reminder"), ("reminder", "alarm")):                 # alarms.due() returns them in the order they were set
        now = [at(2026, 11, 2, 6, 0)]
        c, posted, played = make(tmp_path / "-".join(order), now)
        said = recording_voice(c)
        for kind in order:
            if kind == "alarm":
                c.alarms.add("alarm", "06:30", where="upstairs")
            else:
                c.alarms.add("reminder", "06:30", "take the pills")
        now[0] = at(2026, 11, 2, 6, 30)
        run(c.tick())
        assert c.ringing["alarm"].kind == "alarm", order
        assert played[-1][0] == ("enclave",) and "This is your alarm." in said[-1] and "Reminder: take the pills." in said[-1], order
        assert sorted(f["kind"] for f in c.fired) == ["alarm", "reminder"] and all(f["ended"] is None for f in c.fired), order     # both are on the portal's record
        for _ in range(3):                                                                        # a reminder rings twice; the alarm goes on
            now[0] += 60
            run(c.tick())
        assert c.ringing is not None and c.ringing["alarm"].kind == "alarm" and len(played) == 4, order
        assert c.stop("phone") == "Alarm off."
        assert sorted((f["kind"], f["ended"]) for f in c.fired) == [("alarm", "stopped"), ("reminder", "stopped")], order


def test_a_reminder_that_falls_due_while_the_alarm_rings_joins_the_ring_and_does_not_end_it(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    said = recording_voice(c)
    c.alarms.add("alarm", "06:30", where="upstairs")
    c.alarms.add("reminder", "06:33", "take the pills")
    c.alarms.items[-1].due = at(2026, 11, 2, 6, 31) + 30                                          # it falls due half a minute into the gap between rings 2 and 3
    gains = []
    for when in (at(2026, 11, 2, 6, 30), at(2026, 11, 2, 6, 31)):                                 # rings 1 and 2: the alarm alone
        now[0] = when
        run(c.tick())
        gains.append(played[-1][2])
    assert len(played) == 2 and "pills" not in " ".join(said)
    now[0] = at(2026, 11, 2, 6, 31) + 30
    run(c.tick())                                                                                 # the reminder falls due: heard at once, in a ring of the alarm
    gains.append(played[-1][2])
    assert len(played) == 3 and c.ringing["alarm"].kind == "alarm" and c.ringing["rings"] == 3 and "Reminder: take the pills." in said[-1]
    assert not any(f["ended"] == "replaced" for f in c.fired)
    for when in (at(2026, 11, 2, 6, 32), at(2026, 11, 2, 6, 32) + 30, at(2026, 11, 2, 6, 33) + 30):
        now[0] = when
        run(c.tick())                                                                             # (06:32:00 is not a ring: the next is a minute after the one just rung)
        if len(played) > len(gains):
            gains.append(played[-1][2])
    assert len(played) == 5 and gains == sorted(gains) and gains[-1] == 1.0                        # still the same alarm, still getting louder
    assert c.ringing["alarm"].kind == "alarm" and c.fired[0]["ended"] is None


def test_two_alarms_due_together_for_two_places_ring_in_both_and_a_snooze_brings_both_back(tmp_path):
    for how in ("voice", "phone"):
        now = [at(2026, 11, 2, 6, 0)]
        c, posted, played = make(tmp_path / how, now)
        c.alarms.add("alarm", "06:30", where="upstairs")
        c.alarms.add("alarm", "06:30", where="downstairs")
        now[0] = at(2026, 11, 2, 6, 30)
        run(c.tick())
        assert played[-1][0] == ("enclave", "hdmi"), how                                          # upstairs, where he sleeps, did not lose to downstairs
        assert all(f["ended"] is None for f in c.fired) and len(c.fired) == 2
        reply = c.answered("snooze", by="theater") if how == "voice" else c.snooze("phone")
        assert "Snoozed" in reply and c.ringing is None, how
        assert sorted(a.where for a in c.alarms.items) == ["downstairs", "upstairs"], how
        now[0] += 9 * 60
        run(c.tick())
        assert played[-1][0] == ("enclave", "hdmi") and c.ringing is not None, how
        assert [f["ended"] for f in c.fired] == ["snoozed", "snoozed", None, None], how


def test_two_alarms_for_the_same_place_are_one_ring_and_one_snooze(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    said = recording_voice(c)
    c.alarms.add("alarm", "06:30", where="upstairs")
    c.alarms.add("alarm", "06:30", "laundry", where="upstairs")
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())
    assert len(played) == 1 and played[-1][0] == ("enclave",) and said[-1].count("This is your alarm") == 1 and "Your laundry alarm." in said[-1]
    c.snooze("phone")
    assert len(c.alarms.items) == 1                                                               # one place, one snoozed copy


def test_the_wake_word_then_snooze_brings_every_alarm_of_the_ring_back_and_not_its_reminder(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "06:30", where="upstairs")
    c.alarms.add("alarm", "06:30", where="downstairs")
    c.alarms.add("reminder", "06:30", "take the pills")
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())
    assert c.woke(by="living") is True and c.ringing is None
    assert sorted(f["ended"] for f in c.fired) == ["stopped"] * 3
    now[0] += 5
    assert c.snooze_after_wake("snooze").startswith("Fine. Snoozed")
    assert sorted((a.kind, a.where) for a in c.alarms.items) == [("alarm", "downstairs"), ("alarm", "upstairs")]     # the reminder was said: it is not asked again
    now[0] += 9 * 60
    run(c.tick())
    assert played[-1][0] == ("enclave", "hdmi") and c.ringing is not None


def test_cancelling_one_of_the_alarms_in_a_ring_on_his_phone_leaves_the_ring_to_the_rest(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "06:30", where="upstairs")
    c.alarms.add("alarm", "06:30", repeat="daily", where="downstairs")                              # a repeating one stays in the list while it rings
    other = c.alarms.items[-1]
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())
    assert played[-1][0] == ("enclave", "hdmi")
    assert c.command({"op": "cancel", "alarm": other.id}) == "Cancelled." and c.ringing is not None
    assert c.ringing["also"] == [] and sorted(f["ended"] or "" for f in c.fired) == ["", "cancelled"]
    now[0] += 60
    run(c.tick())
    assert played[-1][0] == ("enclave",)                                                           # the one he did not cancel rings on, alone
    assert c.command({"op": "cancel", "alarm": c.ringing["alarm"].id}) == "That one is already gone."     # (a one-off that rings is out of the list already)
    c.stop("phone")
    assert [f["ended"] for f in c.fired] == ["stopped", "cancelled"]


def test_an_answer_that_lands_while_a_ring_plays_sends_no_alert_to_his_phone(tmp_path):
    """Ring 4 is the first that tells his phone he hasn't answered; he pressed Stop during it, and the portal still DMed 'you haven't answered' right after."""
    now = [at(2026, 11, 2, 6, 29)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "06:30")
    now[0] = at(2026, 11, 2, 6, 30)
    for _ in range(3):
        run(c.tick())
        now[0] += 60
    assert not [b for p, b in posted if p == "/home/alert"]

    async def slow(kinds, clips, gain):
        await asyncio.sleep(0.3)                                       # the Enclave is saying it
        return True
    c._play_outs = slow

    async def go():
        ring = asyncio.ensure_future(c.tick())                         # 06:33: three minutes unanswered
        await asyncio.sleep(0.05)
        c.stop("phone")
        await ring
    run(go())
    assert c.ringing is None and c.fired[-1]["ended"] == "stopped" and not [b for p, b in posted if p == "/home/alert"]
    c.alarms.add("alarm", "06:34")                                    # (and an unanswered one still tells him)
    now[0] += 60
    for _ in range(4):
        run(c.tick())
        now[0] += 60
    assert [b for p, b in posted if p == "/home/alert"]


# ── the morning briefing and the alarm clock, wired as hub.py wires them (review of 2026-11-05) ──────────────────────────────────────────────────

def clock_and_routines_wired_as_the_hub_does(tmp_path, now):
    """A real AlarmClock and a real Daily on one clock, with the extras hub.py gives Daily: the bedtime alarm line, the day's first alarm, whether one rings."""
    from test_sat_daily import HOME, car_at, make as make_daily
    d, room, link, *_ = make_daily(tmp_path / "daily", now, car=car_at(HOME))
    c, posted, played = make(tmp_path / "clock", now)

    async def alarm_tomorrow():
        return c.tomorrow_line()
    d.extras.update(alarm_tomorrow=alarm_tomorrow, wake_alarm=c.wake_time, alarm_ringing=c.is_ringing)
    return d, room, link, c, played


def test_the_first_wake_up_alarm_of_the_day_is_the_first_in_the_morning_hours_not_the_first_of_any_hour(tmp_path):
    """wake_time took the earliest alarm of the date and only then asked whether it was in 04:30-11:00: a 03:30 one-off for an early drive, or a snooze made at
    23:56 and due at 00:05, hid the 07:00 alarm the morning is planned around, and the briefing fell back to 07:10, after the ring."""
    now = [at(2026, 11, 4, 22, 30)]
    d, room, link, c, played = clock_and_routines_wired_as_the_hub_does(tmp_path, now)
    c.alarms.add("alarm", "07:00", repeat="mon,wed,thu", where="upstairs")
    assert c.wake_time("2026-11-05") == "07:00" and d.morning_time("2026-11-05") == "06:50"
    c.alarms.add("alarm", "03:30", date="2026-11-05")                                                  # an early drive
    assert c.wake_time("2026-11-05") == "07:00" and d.morning_time("2026-11-05") == "06:50"
    now[0] = at(2026, 11, 4, 23, 56)
    c.alarms.add("alarm", in_minutes=9)                                                                # a snooze: due at 00:05
    assert any(alarm_clock.time.strftime("%H:%M", alarm_clock.time.localtime(a.due)) == "00:05" for a in c.alarms.items)
    assert c.wake_time("2026-11-05") == "07:00" and d.morning_time("2026-11-05") == "06:50"
    c.alarms.add("alarm", "12:30", date="2026-11-06")
    assert c.wake_time("2026-11-06") is None                                                           # nothing in the morning hours: the fixed time
    assert c.wake_time("2026-11-07") is None and d.morning_time("2026-11-06") == "07:10"


def test_a_repeating_alarm_set_after_its_time_today_is_not_todays_but_one_that_has_rung_today_still_is(tmp_path):
    """'Wake me at 5:30 every day' said at 06:00 first rings tomorrow; counted for today, it pulled today's briefing back to 05:20: it started at once."""
    now = [at(2026, 11, 5, 6, 0)]                                                                      # a Thursday
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "07:00", repeat="daily", where="upstairs")
    c.alarms.add("alarm", "05:30", repeat="daily", where="upstairs")
    assert c.wake_time("2026-11-05") == "07:00" and c.wake_time("2026-11-06") == "05:30"
    now[0] = at(2026, 11, 6, 5, 29)
    assert c.wake_time("2026-11-06") == "05:30"                                                        # due this morning
    now[0] = at(2026, 11, 6, 5, 30)
    run(c.tick())
    assert c.ringing is not None and c.alarms.items[0].due > now[0]
    assert c.wake_time("2026-11-06") == "05:30"                                                        # it has rung: today's answer does not move


def test_the_whole_bedtime_names_one_briefing_time(tmp_path):
    """The alarm line was made at the routine's start, in parallel, before plan_tomorrow had stored the early event's override: he heard 6:30 from
    plan_tomorrow and then 8:20 from the alarm line, with the truth (the earlier) being 6:30."""
    import re
    from test_sat_daily import finish
    now = [at(2026, 11, 5, 22, 30)]                                                                    # Thursday night; tomorrow is Friday
    d, room, link, c, played = clock_and_routines_wired_as_the_hub_does(tmp_path, now)
    c.alarms.add("alarm", "08:30", repeat="tue,fri", where="upstairs")

    async def first_event():
        return at(2026, 11, 6, 8, 0)                                                                   # 90 minutes before it is 06:30
    d.extras["first_tomorrow"] = first_event

    async def go():
        await room.handle("goodnight")
        await finish(d, 15)
    asyncio.run(go())
    said = " ".join(link.spoken)
    assert re.findall(r"briefing comes at ([0-9:]+ [AP]M)", said) == ["6:30 AM", "6:30 AM"] and "Your alarm is set for 8:30." in said
    assert d.morning_time("2026-11-06") == "06:30"


def briefing_that_could_not_start_in_time(tmp_path, answer, repeat):
    """The briefing's slot (ten minutes before the 07:00 alarm) passes without it starting (the box was off the Wi-Fi, or another routine was running); the
    alarm rings at 07:00 and he answers. When may the briefing start?"""
    from test_sat_daily import finish
    now = [at(2026, 11, 5, 6, 0 if repeat == "daily" else 55)]
    d, room, link, c, played = clock_and_routines_wired_as_the_hub_does(tmp_path, now)
    c.alarms.add("alarm", "07:00", repeat=repeat)
    started = {}

    async def go():
        now[0] = at(2026, 11, 5, 6, 57 if repeat == "daily" else 55)                                   # (the box is back; or the alarm was only just set)
        await d.tick(room)
        started["before the alarm"] = d.busy                                                           # three or five minutes to the ring: no
        now[0] = at(2026, 11, 5, 7, 0)
        await c.tick()
        await d.tick(room)
        started["while it rings"] = d.busy
        if answer == "voice":
            assert "Snoozed" in c.answered("snooze", by="office")
        elif answer == "wake word":
            assert c.woke(by="office") and c.snooze_after_wake("snooze", by="office").startswith("Fine. Snoozed")
        else:
            assert "Snoozed" in c.snooze("phone")
        now[0] += 20
        await d.tick(room)
        started["right after the snooze"] = d.busy
        now[0] = at(2026, 11, 5, 7, 9) + 1
        await c.tick()
        assert c.ringing is not None
        await d.tick(room)
        started["while the snoozed alarm rings"] = d.busy
        assert c.stop("phone") == "Alarm off."
        now[0] = at(2026, 11, 5, 7, 10) + 1                                                            # (a one-off is gone from the list: the fixed 07:10 is the time left)
        await d.tick(room)
        started["once he has stopped it"] = d.busy
        await finish(d, 15)
    asyncio.run(go())
    return started, link


def test_a_snoozed_copy_that_has_rung_does_not_hold_the_briefing_when_its_id_goes_to_a_new_alarm(tmp_path):
    """The clock remembers the snoozed copies it is waiting for by id; ids are handed out again (max + 1) once a one-off is deleted, so a copy that rang must not
    stand for the next alarm that gets its id: the briefing would wait for an alarm that is no snooze, for ever."""
    now = [at(2026, 11, 2, 6, 29)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "06:30")
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())
    assert c.alarms.items == [] and "Snoozed" in c.snooze("phone") and c.is_ringing()                      # waiting for nine minutes
    now[0] += 9 * 60
    run(c.tick())
    assert c.ringing is not None and c.stop("phone") == "Alarm off."                                       # (is_ringing() is not asked between)
    now[0] = at(2026, 11, 3, 5, 0)
    c.alarms.add("alarm", "07:00", repeat="daily")
    assert c.alarms.items[0].id == 1 and not c.is_ringing()                                                # the id of the copy is free again, and this is no snooze


def test_a_briefing_that_missed_its_slot_never_starts_over_the_ring_in_the_snooze_he_asked_for_or_the_minutes_before_an_alarm(tmp_path):
    """It started the instant the alarm was answered, 'snooze' included, with the Bedroom light coming up and the news nine minutes before the alarm he
    asked for; and an alarm set at 06:55 for 07:00 started it at once, so the ring landed inside it (review of 2026-11-05)."""
    for n, (answer, repeat) in enumerate((("voice", "daily"), ("wake word", "daily"), ("phone", "daily"), ("voice", "once"))):
        started, link = briefing_that_could_not_start_in_time(tmp_path / str(n), answer, repeat)
        assert started == {"before the alarm": False, "while it rings": False, "right after the snooze": False,
                           "while the snoozed alarm rings": False, "once he has stopped it": True}, (answer, repeat)
        assert link.spoken[0].startswith("Good morning, Captain. It's 7:10 AM")                        # (a late briefing, when he is up)


class WaitPod(Pod):
    """Remembers whether the clock handed it the stereo's ring to wait for (hub.Room.ring waits for it whenever it is handed one, quiet or not)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.handed = []

    async def ring(self, tone, voice, text, quiet=False, wait=None):
        self.handed.append(wait is not None)
        await super().ring(tone, voice, text, quiet=quiet, wait=wait)


def test_a_pod_whose_room_has_no_stereo_on_does_not_wait_for_another_rooms_stereo(tmp_path, monkeypatch):
    """A reminder set in the office, with the vault's SATELLITE_ANNOUNCE_OUT=enclave, rings the Enclave upstairs AND the office pod. The pod was handed the Enclave's
    ring to wait for, and rang only after the chime and words upstairs were over (5-10 s late, deaf meanwhile), though the Enclave is no speaker of its room."""
    monkeypatch.setattr(alarm_clock, "LISTEN_AFTER_S", 0)
    office, living = WaitPod(), WaitPod(stereo=["enclave"])
    layout = house_layout.Layout({"floors": {"upstairs": {"rooms": ["living room"]}, "downstairs": {"rooms": ["office"]}},
                                  "rooms": {"living room": {"pods": ["living"], "outputs": ["enclave"]}, "office": {"pods": ["office"]}}})
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now, rooms={"office": office, "living": living}, env={"SATELLITE_ANNOUNCE_OUT": "enclave"})
    c.layout = layout
    c.alarms.add("reminder", "06:30", "call the dentist", room="office")                      # no place: the room that set it, and the announcement outputs
    c.alarms.add("alarm", "06:30", where="upstairs", room="living")
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())
    assert c.ringing["alarm"].kind == "alarm" and played[-1][0] == ("enclave",)
    assert office.handed == [False] and office.rang and living.handed == [True]               # the office rings at once; the living room's box still follows its own Enclave


class TalkingPod(FloorPod):
    """A pod that says an announcement the way hub.Room does: under its voice lock, which waits for whoever holds it, with no bound."""

    async def announce(self, text):
        async with self.voice:
            self.said.append(text)


def test_a_due_timer_never_holds_up_a_due_alarm_and_is_still_said_when_the_room_is_free(tmp_path, monkeypatch):
    """The timers were said first, in tick(): a long reply holding the room's voice kept the timer's one line, and so the alarm behind it, waiting for as long
    as it talked (the floor wait of a ring did not start until the line was said)."""
    monkeypatch.setattr(alarm_clock, "FLOOR_WAIT_S", 0.6)
    pod = TalkingPod()
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now, rooms={"kitchen": pod})
    c.timers_for("kitchen").add(60, "pasta")
    c.alarms.add("alarm", "06:30", where="upstairs")
    now[0] = at(2026, 11, 2, 6, 30)
    rang, state = [], {}

    async def play(kinds, clips, gain):
        rang.append(time.monotonic() - t0)
        return True
    c._play_outs = play

    async def go():
        await pod.voice.acquire()                                      # a long reply is being said, for 2.6 s
        ticking = asyncio.ensure_future(c.tick())
        await asyncio.sleep(2.0)
        state["tick_done"] = ticking.done()
        await asyncio.sleep(0.6)
        pod.voice.release()
        await ticking
        await asyncio.sleep(0.2)                                       # the timer's line, queued behind the reply, is said now
    t0 = time.monotonic()
    run(go())
    assert len(rang) == 1 and 0.5 < rang[0] < 0.95                    # the ring waited its floor (0.6 s) and nothing else: not the timer's line (another 0.6 s), not the reply
    assert state["tick_done"]                                         # and the clock was not kept waiting for the room, for its next ring
    assert pod.said == ["Your pasta timer is done."] and not c._bg    # the timer was not lost
