"""What the Echoes did besides timers: alarms and reminders at a time (kept on disk, repeating, ringing until answered, snooze), lists by
voice, timers that outlive a hub restart, and the stereo's volume and "what's playing"."""
import time

import alarms as alarms_mod
from alarms import Alarms, next_at, parse_days, parse_hhmm
from lists import Lists, list_name
from timers import Timers


def local(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


SUN_2200 = local(2026, 11, 1, 22, 0)          # a Sunday evening


def test_times_and_repeats_are_read_the_way_he_says_them():
    assert parse_hhmm("07:00") == (7, 0) and parse_hhmm("7") == (7, 0) and parse_hhmm("6:30 pm") == (18, 30) and parse_hhmm("12am") == (0, 0)
    assert parse_days("weekdays") == ["mon", "tue", "wed", "thu", "fri"] and parse_days("mon, wed and fri") == ["mon", "wed", "fri"]
    assert parse_days("once") == []
    # 7 AM from Sunday 22:00 is Monday; "weekends" from there is Saturday
    assert time.localtime(next_at("07:00", [], SUN_2200)).tm_mday == 2
    assert time.localtime(next_at("07:00", ["sat", "sun"], SUN_2200)).tm_wday == 5


def test_an_alarm_is_kept_on_disk_goes_off_rings_until_answered_and_repeats(tmp_path):
    now = [SUN_2200]
    path = tmp_path / "theater.json"
    a = Alarms(path, clock=lambda: now[0])
    said = a.run("set_alarm", {"time": "07:00", "repeat": "weekdays", "label": "work"})
    assert said.startswith("Alarm set for tomorrow at 7 AM, every weekday")
    again = Alarms(path, clock=lambda: now[0])   # the hub restarted
    assert len(again.items) == 1 and "7 AM" in again.listing()
    now[0] = local(2026, 11, 2, 7, 0)
    fired = again.due()
    assert [f.label for f in fired] == ["work"] and again.ringing is not None
    assert "Your work alarm" in again.spoken(fired[0])
    assert time.localtime(again.items[0].due).tm_mday == 3     # a weekday alarm comes back Tuesday
    assert again.ring_again() is None                         # not a minute yet
    now[0] += 61
    assert again.ring_again() is not None
    assert again.answered("snooze").startswith("Fine. Snoozed until 7:10 AM")
    assert again.ringing is None and len(again.items) == 2     # the snooze is a one-off nine minutes on
    now[0] += 9 * 60
    assert again.due() and again.answered("stop") == "Alarm off."
    assert len(again.items) == 1


def test_an_unanswered_alarm_stops_after_a_few_rings_and_anything_else_said_silences_it(tmp_path):
    now = [SUN_2200]
    a = Alarms(None, clock=lambda: now[0])
    a.add("alarm", "22:05")
    now[0] += 300
    assert a.due()
    rings = 0
    for _ in range(10):
        now[0] += 61
        rings += a.ring_again() is not None
    assert rings == alarms_mod.RINGS["alarm"] - 1 and a.ringing is None
    a.add("alarm", "23:00")
    now[0] = local(2026, 11, 1, 23, 0)
    a.due()
    assert a.answered("what's the weather tomorrow") is None and a.ringing is None   # stopped; the brain answers the question


def test_reminders_in_minutes_or_at_a_time_and_cancelling_by_words_or_time(tmp_path):
    now = [SUN_2200]
    a = Alarms(None, clock=lambda: now[0])
    assert a.run("set_reminder", {"text": "check the oven", "in_minutes": 20}) == "Reminder set for 10:20 PM: check the oven."
    assert a.run("set_reminder", {"text": "call Mom", "time": "17:00"}).startswith("Reminder set for tomorrow at 5 PM")
    a.run("set_alarm", {"time": "6:30"})
    assert a.run("set_reminder", {"text": ""}).startswith("Not set")
    assert a.run("set_alarm", {"time": "banana"}).startswith("Not set")
    assert a.run("cancel_alarm", {"what": "the Mom reminder"}) == "Cancelled 1."
    assert a.run("cancel_alarm", {"what": "6:30"}) == "Cancelled 1."
    now[0] += 20 * 60
    [r] = a.due()
    assert a.spoken(r) == "Reminder: check the oven." and a.answered("thanks") == "Okay."


def test_a_one_off_missed_by_hours_while_the_hub_was_down_is_dropped_a_repeating_one_moves_on(tmp_path):
    now = [SUN_2200]
    path = tmp_path / "a.json"
    a = Alarms(path, clock=lambda: now[0])
    a.add("alarm", "23:00")
    a.add("alarm", "23:00", repeat="daily")
    now[0] = local(2026, 11, 2, 9, 0)
    b = Alarms(path, clock=lambda: now[0])
    assert len(b.items) == 1 and b.items[0].days and b.items[0].due > now[0]


def test_timers_outlive_a_restart(tmp_path):
    now = [1000.0]
    path = tmp_path / "t.json"
    Timers(clock=lambda: now[0], path=path).add(300, "pasta")
    t = Timers(clock=lambda: now[0], path=path)
    assert t.remaining() == [("pasta", 300)]
    now[0] += 301
    assert [x.label for x in t.due()] == ["pasta"] and Timers(clock=lambda: now[0], path=path).remaining() == []


def test_lists_add_read_remove_clear_and_are_shared_on_disk(tmp_path):
    path = tmp_path / "lists.json"
    li = Lists(path)
    assert list_name("my grocery list") == "shopping" and list_name("to do") == "to-do" and list_name("the Costco list") == "costco"
    assert li.run("list", {"action": "add", "list": "shopping", "items": ["milk", "eggs"]}) == "Added milk and eggs to the shopping list."
    assert li.run("list", {"action": "add", "list": "groceries", "items": ["Milk", "bread"]}) == "Added bread to the shopping list. Milk was already on it."
    assert Lists(path).run("list", {"action": "read", "list": "shopping"}) == "On the shopping list (3): milk, eggs and bread."
    assert li.run("list", {"action": "remove", "list": "shopping", "items": ["the eggs", "jam"]}) == "Took eggs off the shopping list. jam was not on it."
    assert li.run("list", {"action": "add", "list": "to do", "items": ["fix the gate"]}).startswith("Added fix the gate to the to-do list")
    assert li.run("list", {"action": "lists"}) == "His lists: shopping (2); to-do (1)."
    assert li.run("list", {"action": "clear", "list": "shopping"}) == "Cleared the shopping list (2 items)."
    assert li.run("list", {"action": "read"}) == "The shopping list is empty."
    assert li.run("list", {"action": "add"}).startswith("Not done")


# ── in the room: an alarm goes off on the room's clock, and a plain "stop" / "snooze" is answered without the brain ──
def test_the_room_rings_the_alarm_and_stop_or_snooze_is_answered_without_the_brain(tmp_path, monkeypatch):
    import asyncio

    import hub
    from hub import Room
    from test_sat_starters import Brain, Gate, Link
    monkeypatch.setattr(hub, "one_stream", lambda mp3, **k: mp3)
    now = [SUN_2200]
    link, brain = Link(), Brain()
    r = Room("theater", link, Gate(), brain, Timers(), log=lambda m: None)
    r.alarms = Alarms(None, clock=lambda: now[0])
    r.alarms.add("alarm", "22:01", label="pizza")
    now[0] += 60
    asyncio.run(r.timers_due())
    assert any("Your pizza alarm" in s for s in link.spoken)
    asyncio.run(r.handle("snooze"))
    assert brain.heard == [] and any("Snoozed until 10:10 PM" in s for s in link.spoken)
    now[0] += 9 * 60
    asyncio.run(r.timers_due())
    asyncio.run(r.handle("okay I'm up"))
    assert brain.heard == [] and link.spoken[-1] == "Alarm off." and r.alarms.items == []
    asyncio.run(r.handle("what time is it"))                            # nothing ringing: the brain as always
    assert brain.heard == ["what time is it"]


class _Player:
    def __init__(self, gain=1.0):
        self.gain = gain

    def set_gain(self, g):
        self.gain = g


class _House:
    def __init__(self):
        self.player, self.level = _Player(), 1.0

    def set_level(self, v):
        self.level = v
        self.player.set_gain(v)


def test_stereo_volume_steps_down_and_up_in_tenths_and_full_is_the_top():
    from hub import stereo_volume
    h = _House()
    assert stereo_volume(h, "louder").startswith("Not louder")
    assert stereo_volume(h, "quieter") == "Done: the house stereo is at 8 of 10." and h.player.gain == 0.8
    assert stereo_volume(h, "volume", 3) == "Done: the house stereo is at 3 of 10."
    assert stereo_volume(h, "volume", 0) == "Done: the house stereo is at 1 of 10."
    assert stereo_volume(h, "volume") == "The house stereo is at 1 of 10."
    assert stereo_volume(h, "louder") == "Done: the house stereo is at 3 of 10."


class _Box:
    def __init__(self, playing, title=("", "")):
        self.playing, self._t = playing, title

    def track(self):
        return "x.flac" if self.playing else ""

    def title(self):
        return self._t


def test_whats_playing_names_the_song_here_or_on_the_stereo():
    from hub import now_playing

    class H:
        box = _Box(True, ("Susie Q", "Creedence Clearwater Revival"))
    assert now_playing(None, _Box(False), None, H(), True) == "Playing on the house stereo: Susie Q by Creedence Clearwater Revival."
    assert now_playing(None, _Box(True, ("Time", "Pink Floyd")), None, None, False) == "Playing: Time by Pink Floyd."
    assert now_playing(None, _Box(False), None, None, False) == "Nothing is playing."


def test_words_handed_to_the_running_hub_from_this_pc_are_taken_as_typed(tmp_path, monkeypatch):
    import asyncio
    import json

    import hub
    from hub import Room
    from test_sat_starters import Brain, Gate, Link
    monkeypatch.setattr(hub, "one_stream", lambda mp3, **k: mp3)
    link, brain = Link(), Brain()
    r = Room("theater", link, Gate(), brain, Timers(), log=lambda m: None)
    r.inbox = tmp_path / "theater.json"
    r.inbox.write_text(json.dumps({"text": "what's on my shopping list"}), encoding="utf-8")
    asyncio.run(r.timers_due())
    assert brain.heard == ["what's on my shopping list"] and not r.inbox.exists()


def test_plain_list_requests_are_routed_to_the_tool_before_the_model():
    from lists import pre_route
    assert pre_route("Skippy, add milk, eggs and coffee to the shopping list") == {"action": "add", "list": "shopping", "items": ["milk", "eggs", "coffee"]}
    assert pre_route("put batteries on my Costco list") == {"action": "add", "list": "costco", "items": ["batteries"]}
    assert pre_route("take the eggs off the shopping list") == {"action": "remove", "list": "shopping", "items": ["the eggs"]}
    assert pre_route("remove bread from my grocery list") == {"action": "remove", "list": "shopping", "items": ["bread"]}
    assert pre_route("what's on my shopping list?") == {"action": "read", "list": "shopping"}
    assert pre_route("what's on my to do list") == {"action": "read", "list": "to-do"}
    assert pre_route("clear the shopping list") == {"action": "clear", "list": "shopping"}
    assert pre_route("add a timer for ten minutes") is None and pre_route("what's the weather") is None


def test_the_stereo_level_survives_ducking_under_skippy():
    import asyncio

    from stereo import DUCK, StereoMusic

    class P(_Player):
        pass

    async def go():
        s = StereoMusic.__new__(StereoMusic)
        s.player, s._undo, s.level = P(), None, 1.0
        s.set_level(0.4)
        assert s.player.gain == 0.4
        s.duck(True, 0.01)
        assert abs(s.player.gain - DUCK * 0.4) < 1e-9
        await asyncio.sleep(0.05)
        assert s.player.gain == 0.4 and s._undo is None
    asyncio.run(go())
