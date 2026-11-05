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
    said = run(steps[-2].make())
    assert said == "Your alarm is set for 6:30." and steps[-1].last


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
