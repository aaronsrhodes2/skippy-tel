"""Round 2 of the review of the house hub (2026-11-05): what the first round's fixes to the alarm clock, the morning routine and the calendar left open.

These ran against the code of round 1 (HEAD 10e0486) first, with the real alarm clock, the real routines and the real calendar reader (google_home) and only the
speakers faked, and failed on: cancelling the alarm that leads a ring, a reminder that rides an alarm's ring (heard on every ring; lost when the ring ended
before it was heard), a timer's line holding the clock, a test run marking the day, and the calendar asked about the wrong morning after midnight. The few that
passed on it (a lone alarm's cancelling, a reminder heard or cancelled, a failing timer line) pin what the fixes must leave as it was."""
import asyncio
import time
import urllib.parse
from datetime import date, datetime

import alarm_clock
import google_home
import speaker_out
from test_sat_alarm_clock import TalkingPod, at, make, recording_voice, run


def slow_enclave(c, seconds=2.0):
    """The Enclave takes `seconds` to say a ring, so that an answer can land in the middle of the first chime (an answer cuts the ring short: it costs no time)."""
    async def slow(kinds, clips, gain):
        await asyncio.sleep(seconds)
        return True
    c._play_outs = slow


def alarm_and_reminder(tmp_path, repeat_reminder="once"):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    said = recording_voice(c)
    c.alarms.add("alarm", "06:30", where="upstairs")
    c.alarms.add("reminder", "06:30", "take the pills", repeat=repeat_reminder)
    now[0] = at(2026, 11, 2, 6, 30)
    return c, now, posted, played, said


# ── cancelling the alarm that leads a ring ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_r2_cancelling_the_alarm_that_leads_a_ring_ends_only_that_alarm_and_the_next_one_leads_on(tmp_path):
    """Two daily alarms fall due together (id 1 upstairs, id 2 downstairs) and ring as one. Cancel on alarm 1 ended the ring and wrote 'cancelled by the phone'
    on alarm 2's record, though nobody cancelled alarm 2: it is still due to wake the downstairs."""
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    said = recording_voice(c)
    c.alarms.add("alarm", "06:30", repeat="daily", where="upstairs")
    c.alarms.add("alarm", "06:30", "laundry", repeat="daily", where="downstairs")
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())
    assert c.ringing["alarm"].id == 1 and played[-1][0] == ("enclave", "hdmi")
    assert c.command({"op": "cancel", "alarm": 1}) == "Cancelled."
    assert c.ringing is not None and c.ringing["alarm"].id == 2 and c.ringing["also"] == []
    assert [(f["id"], f["ended"], f["by"]) for f in c.fired] == [(1, "cancelled", "phone"), (2, None, None)]
    assert c.view()["ringing"]["id"] == 2
    now[0] += 60
    run(c.tick())
    assert played[-1][0] == ("hdmi",) and "laundry" in said[-1]                          # downstairs rings on, alone, a minute later and louder
    assert c.stop("phone") == "Alarm off."
    assert [(f["id"], f["ended"]) for f in c.fired] == [(1, "cancelled"), (2, "stopped")]


def test_r2_cancelling_the_only_alarm_of_a_ring_still_ends_the_ring(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "06:30", repeat="daily", where="upstairs")
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())
    assert c.command({"op": "cancel", "alarm": 1}) == "Cancelled." and c.ringing is None
    assert [(f["ended"], f["by"]) for f in c.fired] == [("cancelled", "phone")]


def test_r2_with_the_cancelled_alarm_gone_a_reminder_left_in_the_ring_leads_on_for_its_own_two_rings(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    said = recording_voice(c)
    c.alarms.add("alarm", "06:30", repeat="daily", where="upstairs")
    c.alarms.add("reminder", "06:30", "take the pills")
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())                                                                         # ring 1: the alarm's, with the reminder's words
    assert "Reminder: take the pills." in said[-1]
    assert c.command({"op": "cancel", "alarm": 1}) == "Cancelled."
    assert c.ringing["alarm"].kind == "reminder" and c.ringing["also"] == []
    now[0] += 60
    run(c.tick())                                                                         # its second and last ring, as the reminder alone
    assert said[-1] == "Reminder: take the pills." and len(said) == 2
    now[0] += 60
    run(c.tick())
    assert c.ringing is None and len(said) == 2
    assert [(f["kind"], f["ended"]) for f in c.fired] == [("alarm", "cancelled"), ("reminder", "gave_up")]


def test_r2_cancelling_the_leading_alarm_while_its_ring_is_being_rung_leaves_the_reminder_exactly_its_two_rings(tmp_path):
    """The cancel comes from the portal's sync, while the chime plays: the ring in the air still counts for the reminder that leads on."""
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    said = recording_voice(c)
    c.alarms.add("alarm", "06:30", repeat="daily", where="upstairs")
    c.alarms.add("reminder", "06:30", "take the pills")
    slow_enclave(c, 0.8)                                                                  # (the ring is not cut short here: it is rung through)
    now[0] = at(2026, 11, 2, 6, 30)

    async def go():
        ring = asyncio.ensure_future(c.tick())
        await asyncio.sleep(0.1)
        assert c.command({"op": "cancel", "alarm": 1}) == "Cancelled."
        await ring
    run(go())
    assert c.ringing["alarm"].kind == "reminder" and c.ringing["heard"][c.ringing["key"]] == 1       # (the cancelled alarm's own ring is counted too since round 3: every member rung is "heard")
    now[0] += 60
    run(c.tick())                                                                         # its second ring
    assert said[-1] == "Reminder: take the pills." and len(said) == 2
    now[0] += 60
    run(c.tick())
    assert c.ringing is None and len(said) == 2                                           # said twice in all, not three times


def test_r2_the_ring_that_goes_on_after_its_lead_is_cancelled_still_tells_his_phone_when_it_was_due_to(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    c.alarms.add("alarm", "06:30", repeat="daily", where="upstairs")
    c.alarms.add("alarm", "06:30", "laundry", repeat="daily", where="downstairs")
    now[0] = at(2026, 11, 2, 6, 30)
    for _ in range(3):                                                                    # 06:30, 06:31, 06:32: unanswered
        run(c.tick())
        now[0] += 60
    assert not [b for p, b in posted if p == "/home/alert"]
    assert c.command({"op": "cancel", "alarm": 1}) == "Cancelled."
    run(c.tick())                                                                         # 06:33: three minutes, as the ring's clock has it
    texts = [b["text"] for p, b in posted if p == "/home/alert"]
    assert len(texts) == 1 and "(laundry)" in texts[0] and "haven't answered" in texts[0]


# ── a reminder that rides an alarm's ring ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_r2_a_reminder_that_could_not_be_set_again_is_logged_and_is_not_marked_as_put_back(tmp_path):
    import alarms as alarms_mod
    c, now, posted, played, said = alarm_and_reminder(tmp_path)
    logged = []
    c.log = logged.append
    slow_enclave(c)

    async def go():
        ring = asyncio.ensure_future(c.tick())
        await asyncio.sleep(0.1)
        while len(c.alarms.items) < alarms_mod.MAX:                                       # the list is full: there is no room to set it again
            c.alarms.add("alarm", "23:59", where="upstairs")
        c.stop("phone")
        await ring
    run(go())
    assert any("could not be set again" in m for m in logged) and [a for a in c.alarms.items if a.kind == "reminder"] == []
    assert next(f for f in c.fired if f["kind"] == "reminder")["ended"] == "stopped"


def test_r2_a_reminder_that_joins_a_reminders_ring_is_said_twice_as_well(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    said = recording_voice(c)
    c.alarms.add("reminder", "06:30", "water the plants")
    c.alarms.add("reminder", "06:31", "call mom")
    for minute in range(30, 34):
        now[0] = at(2026, 11, 2, 6, minute)
        run(c.tick())
    assert sum("call mom" in s for s in said) == 2 and c.ringing is None
    assert sorted(f["ended"] for f in c.fired) == ["gave_up", "gave_up"]


def test_r2_a_reminder_that_was_leading_keeps_its_count_when_an_alarm_takes_the_ring_over(tmp_path):
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now)
    said = recording_voice(c)
    c.alarms.add("reminder", "06:30", "take the pills")
    c.alarms.add("alarm", "06:31", where="upstairs")
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())                                                                         # the reminder rings alone: said once
    now[0] = at(2026, 11, 2, 6, 31)
    run(c.tick())                                                                         # the alarm falls due and leads; the reminder is said a second time
    assert c.ringing["alarm"].kind == "alarm" and "take the pills" in said[-1]
    now[0] += 60
    run(c.tick())
    assert "take the pills" not in said[-1] and "This is your alarm" in said[-1]          # and that was its two
    assert c.stop("phone") == "Alarm off."
    assert [a for a in c.alarms.items if a.kind == "reminder"] == []
    assert sorted((f["kind"], f["ended"]) for f in c.fired) == [("alarm", "stopped"), ("reminder", "stopped")]


def test_r2_a_reminders_words_are_said_in_its_first_two_rings_only(tmp_path):
    """They were repeated on every ring until the alarm was answered (6 of 6 rings); a reminder is said twice."""
    c, now, posted, played, said = alarm_and_reminder(tmp_path)
    for _ in range(6):
        run(c.tick())
        now[0] += 60
    assert ["take the pills" in s for s in said] == [True, True, False, False, False, False]
    assert all("This is your alarm" in s for s in said) and len(played) == 6              # the alarm itself went on every minute
    assert c.ringing is not None and all(f["ended"] is None for f in c.fired)             # and the reminder stays in the ring until it ends: one stop covers all
    assert c.stop("phone") == "Alarm off."
    assert sorted((f["kind"], f["ended"]) for f in c.fired) == [("alarm", "stopped"), ("reminder", "stopped")]


def test_r2_a_reminder_not_heard_when_the_ring_is_answered_is_put_back_a_minute_later_not_lost(tmp_path):
    """A reminder that joined an alarm's ring is out of the list already (a one-off goes as it falls due), so Stop, Snooze or the wake word during the first
    chime ended it unheard and it was gone."""
    for name, answer in (("stop on the phone", lambda c: c.stop("phone")), ("the wake word", lambda c: c.woke(by="living")),
                         ("snooze aloud", lambda c: c.answered("snooze", by="living")), ("snooze on the phone", lambda c: c.snooze("phone"))):
        c, now, posted, played, said = alarm_and_reminder(tmp_path / name.replace(" ", "-"))
        slow_enclave(c)

        async def go():
            ring = asyncio.ensure_future(c.tick())
            await asyncio.sleep(0.1)                                                      # the first chime is playing: none of the words has been heard
            assert c.ringing is not None
            answer(c)
            await ring
        run(go())
        assert c.ringing is None, name
        back = [a for a in c.alarms.items if a.kind == "reminder"]
        assert [(a.label, round(a.due - now[0])) for a in back] == [("take the pills", 60)], name
        assert next(f for f in c.fired if f["kind"] == "reminder")["ended"] == "snoozed", name      # not 'stopped': nobody heard it
        now[0] += 61
        run(c.tick())
        assert c.ringing["alarm"].kind == "reminder" and said[-1] == "Reminder: take the pills.", name      # a minute later it is said, alone


def test_r2_a_reminder_heard_in_a_full_ring_is_not_put_back_when_the_alarm_is_stopped(tmp_path):
    c, now, posted, played, said = alarm_and_reminder(tmp_path)
    run(c.tick())                                                                         # one whole ring, words included
    assert c.stop("phone") == "Alarm off."
    assert [a for a in c.alarms.items if a.kind == "reminder"] == []
    assert sorted((f["kind"], f["ended"]) for f in c.fired) == [("alarm", "stopped"), ("reminder", "stopped")]


def test_r2_a_repeating_reminder_cancelled_on_his_phone_before_it_was_heard_is_not_put_back(tmp_path):
    c, now, posted, played, said = alarm_and_reminder(tmp_path, repeat_reminder="daily")
    slow_enclave(c)
    reminder = next(a for a in c.alarms.items if a.kind == "reminder")

    async def go():
        ring = asyncio.ensure_future(c.tick())
        await asyncio.sleep(0.1)
        assert c.command({"op": "cancel", "alarm": reminder.id}) == "Cancelled."
        c.stop("phone")
        await ring
    run(go())
    assert [a for a in c.alarms.items if a.kind == "reminder"] == []                      # he cancelled it: it does not come back
    assert sorted((f["kind"], f["ended"]) for f in c.fired) == [("alarm", "stopped"), ("reminder", "cancelled")]


def test_r2_a_reminder_that_falls_due_at_the_tick_where_the_alarm_gives_up_is_put_back_not_closed_unheard(tmp_path):
    """It joined the ring at the tick that ended the ring: closed as 'gave_up' without a single ring, and gone."""
    now = [at(2026, 11, 2, 6, 29)]
    c, posted, played = make(tmp_path, now)
    said = recording_voice(c)
    c.alarms.add("alarm", "06:30", where="upstairs")
    c.alarms.add("reminder", "07:00", "take the pills")
    now[0] = at(2026, 11, 2, 6, 30)
    while now[0] < at(2026, 11, 2, 7, 0):
        run(c.tick())                                                                     # a ring a minute for half an hour, unanswered
        now[0] += 60
    run(c.tick())                                                                         # 07:00: the thirty minutes are up, and the reminder falls due
    assert c.ringing is None and c.fired[0]["ended"] == "gave_up"
    assert not any("take the pills" in s for s in said)                                   # it never rang
    assert [(a.label, round(a.due - now[0])) for a in c.alarms.items if a.kind == "reminder"] == [("take the pills", 60)]
    assert next(f for f in c.fired if f["kind"] == "reminder")["ended"] == "snoozed"
    now[0] += 60
    run(c.tick())
    assert c.ringing["alarm"].kind == "reminder" and said[-1] == "Reminder: take the pills."


# ── a timer's words and the clock ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_r2_a_due_timers_line_does_not_park_the_clock_and_is_said_once_when_the_room_is_free(tmp_path, monkeypatch):
    """tick() waited up to FLOOR_WAIT_S (20 s) for the timer's line to be said, with the next ring still to wait for its own floor behind it."""
    monkeypatch.setattr(alarm_clock, "FLOOR_WAIT_S", 5.0)
    pod = TalkingPod()
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now, rooms={"kitchen": pod})
    c.timers_for("kitchen").add(60, "pasta")
    now[0] = at(2026, 11, 2, 6, 5)

    async def go():
        await pod.voice.acquire()                                                         # a long reply is being said
        await asyncio.wait_for(c.tick(), 1.0)                                             # the clock goes on at once
        before = list(pod.said)
        await asyncio.wait_for(c.tick(), 1.0)                                             # and the timer is not said a second time
        pod.voice.release()
        await asyncio.sleep(0.5)
        return before, list(pod.said), len(c._bg)
    before, after, pending = run(go())
    assert before == [] and after == ["Your pasta timer is done."] and pending == 0


def test_r2_an_alarm_due_while_a_timers_line_waits_for_the_room_rings_after_its_own_floor_wait_only(tmp_path, monkeypatch):
    """The clock was parked on the timer's line for a floor wait, and the alarm then waited its own: twice the bound the ring promises."""
    monkeypatch.setattr(alarm_clock, "FLOOR_WAIT_S", 3.0)
    pod = TalkingPod()
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now, rooms={"kitchen": pod})
    c.timers_for("kitchen").add(60, "pasta")
    c.alarms.add("alarm", "06:30", where="upstairs")
    rang = []

    async def play(kinds, clips, gain):
        rang.append(time.monotonic() - t0)
        return True
    c._play_outs = play
    now[0] = at(2026, 11, 2, 6, 5)                                                        # the timer has run out; the alarm is not due yet

    async def hub_run():                                                                  # AlarmClock.run: a tick, then a pause (a tenth of a second here)
        while not rang:
            await c.tick()
            await asyncio.sleep(0.1)

    async def go():
        await pod.voice.acquire()                                                         # a long reply is being said
        clock = asyncio.ensure_future(hub_run())
        await asyncio.sleep(0.3)
        now[0] = at(2026, 11, 2, 6, 30)                                                   # the alarm falls due
        await asyncio.wait_for(clock, 9)
        pod.voice.release()
        await asyncio.sleep(0.5)
    t0 = time.monotonic()
    run(go())
    assert 3.0 <= rang[0] < 4.8, rang                                                     # its own floor wait (3 s) from when it fell due (0.3 s): it was 6 s, the clock having first been parked on the timer's line for 3
    assert pod.said == ["Your pasta timer is done."] and not c._bg                        # and the timer's line was not lost


def test_r2_a_timer_line_that_fails_neither_aborts_the_tick_nor_stays_in_the_books(tmp_path):
    class Broken(TalkingPod):
        async def announce(self, text):
            raise RuntimeError("the box went away")
    logged = []
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now, rooms={"kitchen": Broken()})
    c.log = logged.append
    c.timers_for("kitchen").add(60, "pasta")
    c.alarms.add("alarm", "06:30", where="upstairs")
    now[0] = at(2026, 11, 2, 6, 30)

    async def go():
        await c.tick()
        await asyncio.sleep(0.3)
    run(go())
    assert c.ringing is not None and len(played) == 1                                     # the alarm rang, and the tick came to its end
    assert any("a timer's words were not said" in m and "the box went away" in m for m in logged) and not c._bg


def test_r2_a_timer_for_a_room_whose_pod_is_away_waits_for_the_houses_voice_like_a_ring_does(tmp_path, monkeypatch):
    """Without tick() waiting for it, the line for an away pod (said on the announcement outputs alone) could start over the next ring or over a room's reply."""
    monkeypatch.setattr(alarm_clock, "FLOOR_WAIT_S", 5.0)
    monkeypatch.setattr(speaker_out, "announce_kinds", lambda env: ["enclave"])
    kitchen = TalkingPod()
    now = [at(2026, 11, 2, 6, 0)]
    c, posted, played = make(tmp_path, now, rooms={"kitchen": kitchen})
    c.timers_for("garage").add(60, "pasta")                                               # the garage's pod is away
    now[0] = at(2026, 11, 2, 6, 5)

    async def go():
        await kitchen.voice.acquire()                                                     # the kitchen is answering him
        await asyncio.wait_for(c.tick(), 1.0)
        await asyncio.sleep(0.5)
        waiting = len(played)
        kitchen.voice.release()
        await asyncio.sleep(0.5)
        return waiting, len(played)
    assert run(go()) == (0, 1)


# ── a test run of the morning routine must not mark the day ──────────────────────────────────────────────────────────────────────────────────────

def test_r2_a_morning_run_asked_for_through_request_json_does_not_mark_the_day(tmp_path):
    """`python daily.py run morning` at 03:00 marked the day, and the scheduled 06:50 briefing then never started."""
    from test_sat_daily import HOME, car_at, finish, make as make_daily, stamp
    now = [stamp("2026-11-05", "03:00")]
    d, room, link, *_ = make_daily(tmp_path, now, car=car_at(HOME))
    d.extras["wake_alarm"] = {"2026-11-05": "07:00"}.get
    (tmp_path / "daily").mkdir(parents=True, exist_ok=True)
    (tmp_path / "daily" / "request.json").write_text('{"routine": "morning"}', encoding="utf-8")

    async def go():
        await d.tick(room)
        assert d.busy                                                                     # the test run
        await finish(d, 15)
        assert d.state.get("last_morning") != "2026-11-05"
        now[0] = stamp("2026-11-05", "06:50")
        await d.tick(room)
        assert d.busy                                                                     # and the scheduled briefing still comes
        await finish(d, 15)
        assert d.state["last_morning"] == "2026-11-05"
    asyncio.run(go())
    assert len([s for s in link.spoken if s.startswith("Good morning, Captain.")]) == 2


# ── the calendar is asked about the morning he will wake to ──────────────────────────────────────────────────────────────────────────────────────

def calendar_with_an_eight_oclock_event_every_day(asked):
    """A calendar (the real reader on canned API answers) with an 08:00 event on every day it is asked about; `asked` collects the days."""
    def api(url):
        if "calendarList" in url:
            return {"items": [{"id": "me@gmail.com", "summary": "Aaron", "primary": True, "selected": True}]}
        if "/events" in url:
            day = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["timeMin"][0][:10]
            asked.append(day)
            start = datetime.fromisoformat(f"{day}T08:00:00").astimezone().isoformat()
            end = datetime.fromisoformat(f"{day}T09:00:00").astimezone().isoformat()
            return {"items": [{"summary": "Standup", "start": {"dateTime": start}, "end": {"dateTime": end}}]}
        raise AssertionError(url)
    return api


def test_r2_plan_tomorrow_asks_the_calendar_about_the_morning_he_will_wake_to_even_after_midnight(tmp_path):
    """first_tomorrow always asked for today + 1: a goodnight at 00:20 on Nov 5 was given Nov 6's 08:00 event, rejected it as the wrong day, and planned nothing
    for the morning of the 5th, which is the one he wakes to."""
    from test_sat_daily import make as make_daily, stamp
    for goodnight, morning, other in (("2026-11-04 23:10", "2026-11-05", "2026-11-06"), ("2026-11-05 00:20", "2026-11-05", "2026-11-06"),
                                      ("2026-11-05 04:29", "2026-11-05", "2026-11-06"), ("2026-11-05 04:31", "2026-11-06", "2026-11-07")):
        date, hm = goodnight.split()
        now = [stamp(date, hm)]
        d, *_ = make_daily(tmp_path / goodnight.replace(" ", "-").replace(":", ""), now)
        asked = []
        gg = google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "t.json"), "GOOGLE_HOME_CLIENT": str(tmp_path / "c.json")},
                                opener=calendar_with_an_eight_oclock_event_every_day(asked), log=lambda m: None)
        gg.clock = lambda: now[0]
        d.extras.update(google_home.extras(gg, d.aword))
        said = asyncio.run(d.plan_tomorrow())
        assert asked == [morning], goodnight                                              # the morning the bedtime leads to, whichever side of midnight
        assert d.morning_time(morning) == "06:30" and "6:30 AM" in said, goodnight
        assert d.morning_time(other) == "07:10", goodnight


def test_r2_plan_tomorrow_hands_the_morning_it_plans_to_an_extra_that_takes_a_day_and_calls_a_bare_one_as_before(tmp_path):
    from test_sat_daily import make as make_daily, stamp
    now = [stamp("2026-11-05", "00:20")]
    d, *_ = make_daily(tmp_path, now)
    seen = []

    async def takes_a_day(day):
        seen.append(day)
        return stamp(day, "08:00")

    async def bare():
        seen.append("bare")
        return stamp("2026-11-05", "08:00")
    d.extras["first_tomorrow"] = takes_a_day
    assert "6:30 AM" in asyncio.run(d.plan_tomorrow()) and seen == ["2026-11-05"]
    d.extras["first_tomorrow"] = bare
    assert "6:30 AM" in asyncio.run(d.plan_tomorrow()) and seen == ["2026-11-05", "bare"]
    d.extras["first_tomorrow"] = lambda *day: takes_a_day(*day or ("2026-11-05",))
    assert "6:30 AM" in asyncio.run(d.plan_tomorrow()) and seen[-1] == "2026-11-05"
    del d.extras["first_tomorrow"]
    assert asyncio.run(d.plan_tomorrow()) is None                                         # no calendar: nothing to plan, as before


def test_r2_first_tomorrow_still_answers_without_a_date_and_takes_a_date_or_a_day_string(tmp_path):
    asked = []
    now = [time.mktime((2026, 11, 4, 23, 10, 0, 0, 0, -1))]
    gg = google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "t.json"), "GOOGLE_HOME_CLIENT": str(tmp_path / "c.json")},
                            opener=calendar_with_an_eight_oclock_event_every_day(asked), log=lambda m: None)
    gg.clock = lambda: now[0]

    async def word(*a):
        return "x"
    logged = []
    first = google_home.extras(gg, word, log=logged.append)["first_tomorrow"]
    def eight(day):
        return time.mktime(time.strptime(f"{day} 08:00", "%Y-%m-%d %H:%M"))
    assert asyncio.run(first()) == eight("2026-11-05") and asked == ["2026-11-05"]       # as before: tomorrow, by his clock
    assert asyncio.run(first(date(2026, 11, 9))) == eight("2026-11-09")
    assert asyncio.run(first("2026-11-10")) == eight("2026-11-10") and asked == ["2026-11-05", "2026-11-09", "2026-11-10"]
    assert asyncio.run(first("not a day")) == eight("2026-11-05") and asked[-1] == "2026-11-05"      # unreadable: tomorrow, by his clock, as without one
    assert any("not a day" in m for m in logged)
