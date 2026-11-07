"""The light shows (lightshow.py): the storm (blue and white, a roiling sky) and the fortress (deep purple and violet, hardly moving), each bulb on its own
random course. A fake house, a fake clock and a seeded random source: hours of show in milliseconds, and the same show every run."""
import json
import random
import time
from datetime import datetime

import pytest

import homectl
import lightshow
from house import HomeError
from lightshow import BLUE, POLL_SECONDS, VIOLET, Show, pick_look


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class Bulbs:
    """The colour lights as Show sees them (matter.Matter's colour methods), recording every command with the time it was sent."""

    def __init__(self, clock, eids):
        self.clock, self.eids = clock, list(eids)
        self.on = {e: True for e in eids}
        self.broken: set[str] = set()                  # these time out
        self.unreachable: set[str] = set()             # these are off the network (not in states)
        self.log: list[tuple] = []

    def _send(self, kind, eid, *rest):
        if eid in self.broken:
            self.log.append(("failed", kind, eid, self.clock.t))
            raise HomeError("The house's Matter controller took too long to answer.")
        self.log.append((kind, eid, *rest, self.clock.t))

    def color_lights(self):
        return [{"entity_id": e, "label": e.upper()} for e in self.eids]

    def color_snapshot(self, eids):
        return {e: {"on": True, "level": 254, "mode": 1, "x": 24939, "y": 24701} for e in eids}

    def switch(self, eid, on):
        self.on[eid] = on
        self.log.append(("switch", eid, on, self.clock.t))

    def set_white(self, eid, kelvin, seconds=0.0, timeout=15.0, force=False):
        self._send("white", eid, kelvin, seconds)

    def set_color(self, eid, hue, saturation, seconds=0.0, timeout=15.0, force=False):
        self._send("color", eid, hue, saturation, seconds)

    def set_level(self, eid, percent, seconds=0.0, timeout=15.0, force=False):
        self._send("level", eid, percent, seconds)

    def states(self, fresh=False):
        return [{"entity_id": e, "state": "on" if on else "off"} for e, on in self.on.items() if e not in self.unreachable]

    def color_restore(self, eid, was, seconds=2.0, power=True, timeout=15.0):
        self.log.append(("restore", eid, power, self.clock.t))


EIDS = ["light.a", "light.b", "light.c"]
HOUR = 3600


def show(plan=(("storm", None),), seed=7, eids=EIDS):
    clock = Clock()
    bulbs = Bulbs(clock, eids)
    return clock, bulbs, Show(bulbs, eids, plan, rng=random.Random(seed), clock=clock.now, wall=clock.now, sleep=clock.sleep)


def colors(bulbs, eid=None):
    return [r for r in bulbs.log if r[0] == "color" and (eid is None or r[1] == eid)]


# ── the storm ───────────────────────────────────────────────────────────────────
def test_every_bulb_roils_on_its_own_course_between_blue_and_white():
    clock, bulbs, s = show()
    assert s.run(stop=lambda: clock.t >= 1000 + 600) == "stopped"
    for eid in EIDS:
        mine = colors(bulbs, eid)
        assert len(mine) > 40                                                    # a command every few seconds, for ten minutes
        whites = [1 - c[3] / 100 for c in mine]
        assert min(whites) < 0.15 and max(whites) > 0.75                         # every bulb visits deep blue and near white
        assert all(BLUE[0] <= c[2] <= BLUE[1] for c in mine)                     # never another hue on the way
        assert all(0.5 <= c[4] <= 6.5 for c in mine)
    levels = [r for r in bulbs.log if r[0] == "level"]
    assert all(45 <= r[2] <= 100 for r in levels) and max(r[2] for r in levels) - min(r[2] for r in levels) > 30   # a sky is not one brightness
    for _kind, _eid, _pct, secs, _t in levels:
        assert 0.5 <= secs <= 6.5


def test_the_storm_bulbs_never_match_and_never_move_together():
    clock, bulbs, s = show()
    s.run(stop=lambda: clock.t >= 1000 + 900)
    heading: dict[str, float] = {}
    apart = total = 0
    for _kind, eid, _hue, sat, _secs, _t in colors(bulbs):
        white = 1 - sat / 100
        others = [w for e, w in heading.items() if e != eid]
        if others:
            total += 1
            apart += all(abs(white - o) >= lightshow.APART - 1e-9 for o in others)
        heading[eid] = white
    assert apart / total > 0.95                                                  # a pick is always made apart from the others when it can be
    times = [c[-1] for c in colors(bulbs)]
    assert len(set(round(t, 3) for t in times)) > 0.95 * len(times)              # staggered: the bulbs do not change in step


def test_it_starts_with_a_cool_white_and_puts_the_lights_back_when_stopped():
    clock, bulbs, s = show()
    s.run(stop=lambda: clock.t >= 1000 + 30)
    kinds = [(r[0], r[1]) for r in bulbs.log]
    for eid in EIDS:                                                              # on, then a cool white (the bulb keeps it: the white end of a look is cold), then looks
        assert kinds.index(("switch", eid)) < kinds.index(("white", eid)) < kinds.index(("color", eid))
    assert [r for r in bulbs.log if r[0] == "white"][0][2] == lightshow.COOL_WHITE_K == 6500
    restored = [r for r in bulbs.log if r[0] == "restore"]
    assert sorted(r[1] for r in restored) == EIDS and all(r[2] is True for r in restored)     # back on or off as they were


def test_a_look_is_always_blue_to_white_and_apart_from_where_the_others_are_heading():
    for seed in range(300):
        look = pick_look(random.Random(seed), [0.1, 0.9])
        assert 0 <= look.key <= 1 and abs(look.saturation - 100 * (1 - look.key)) < 1e-9
        assert BLUE[0] <= look.hue <= BLUE[1] and 45 <= look.level <= 100 and 0.5 <= look.seconds <= 6.5
        assert min(abs(look.key - 0.1), abs(look.key - 0.9)) >= lightshow.APART
    assert pick_look(random.Random(1), [0.1, 0.9]) == pick_look(random.Random(1), [0.1, 0.9])          # the same seed, the same storm
    assert [(round(a, 2), round(b, 2)) for a, b in lightshow._free([0.1, 0.9])] == [(0.32, 0.68)]      # the room the other two leave
    assert lightshow._free([0.5]) == [(0.0, 0.5 - lightshow.APART), (0.5 + lightshow.APART, 1.0)]
    crowded = pick_look(random.Random(3), [0.0, 0.25, 0.5, 0.75, 1.0])                                 # no room left: the best there is, never a crash
    assert 0 <= crowded.key <= 1


# ── the fortress ────────────────────────────────────────────────────────────────
def test_the_fortress_is_deep_violet_and_purple_so_slow_it_is_hardly_seen_to_change():
    clock, bulbs, s = show([("fortress", None)])
    s.run(stop=lambda: clock.t >= 1000 + 6 * HOUR)
    for eid in EIDS:
        mine = colors(bulbs, eid)
        assert 25 < len(mine) < 90                                                # six hours: a command every five to twelve minutes
        assert all(VIOLET[0] <= c[2] <= VIOLET[1] for c in mine)                  # violet to purple: never blue, never magenta
        assert all(lightshow.FORTRESS_SATURATION[0] <= c[3] <= 100 for c in mine)
        assert all(c[4] >= 240 for c in mine)                                     # every fade takes four minutes or more
        for before, after in zip(mine, mine[1:]):
            assert abs(after[2] - before[2]) / after[4] * 60 <= 2.0 + 1e-9        # at most two degrees of hue a minute
    levels = [r for r in bulbs.log if r[0] == "level"]
    assert all(lightshow.FORTRESS_LEVEL[0] <= r[2] <= lightshow.FORTRESS_LEVEL[1] for r in levels)       # deep: never bright
    for eid in EIDS:
        mine = [r for r in levels if r[1] == eid]
        for before, after in zip(mine, mine[1:]):
            assert abs(after[2] - before[2]) / after[3] * 60 <= 1.5 + 1e-9        # and at most a point and a half of brightness a minute
    assert [r for r in bulbs.log if r[0] == "white"] == []                        # no white flash to start it: it only has violet to do


def test_the_fortress_bulbs_are_never_the_same_violet():
    clock, bulbs, s = show([("fortress", None)])
    s.run(stop=lambda: clock.t >= 1000 + 8 * HOUR)
    heading: dict[str, float] = {}
    apart = total = 0
    for _kind, eid, hue, _sat, _secs, _t in colors(bulbs):
        others = [h for e, h in heading.items() if e != eid]
        if others:
            total += 1
            apart += all(abs(hue - o) >= lightshow.FORTRESS_APART - 1e-9 for o in others)
        heading[eid] = hue
    assert apart / total > 0.8                                                    # apart whenever a slow step can be (the steps are short, the band narrow)
    spread = {eid: {round(c[2]) for c in colors(bulbs, eid)} for eid in EIDS}
    assert all(len(v) > 10 for v in spread.values())                              # and each wanders: it is not parked on one colour


# ── plans: the storm for a time, then the fortress ──────────────────────────────
def test_a_plan_runs_the_storm_for_five_hours_and_then_the_fortress_fades_in_slowly():
    clock, bulbs, s = show([("storm", 5 * HOUR), ("fortress", None)])
    end = 1000 + 5 * HOUR
    assert s.run(stop=lambda: clock.t >= end + HOUR) == "stopped"
    storm = [c for c in colors(bulbs) if c[-1] < end]
    fort = [c for c in colors(bulbs) if c[-1] >= end]
    assert len(storm) > 9000 and len(fort) >= 9
    assert all(c[4] <= 6.5 and BLUE[0] <= c[2] <= BLUE[1] for c in storm)         # five hours of storm
    assert all(c[4] >= 240 and VIOLET[0] <= c[2] <= VIOLET[1] for c in fort)      # then only the fortress
    assert min(c[-1] for c in fort) - end < 5                                     # it takes over at the five-hour mark, not later
    firsts = [next(c for c in fort if c[1] == eid) for eid in EIDS]
    assert all(lightshow.FORTRESS_SETTLE[0] <= c[4] <= lightshow.FORTRESS_SETTLE[1] for c in firsts)       # the way in from the storm's blues is four to seven minutes
    restored = [r for r in bulbs.log if r[0] == "restore"]
    assert len(restored) == 3 and all(r[-1] >= end for r in restored)             # the lights were not put back in between: once, at the very end
    assert [r for r in bulbs.log if r[0] == "white" and r[-1] > 1010] == []       # and no white flash at the switch (the fortress needs none)
    assert s.phase()["routine"] == "fortress" and s.phase()["ends"] is None and s.phase()["then"] is None


def test_a_plan_that_runs_out_ends_the_show_and_puts_the_lights_back():
    clock, bulbs, s = show([("storm", 60)])
    assert s.run() == "done"
    assert 60 <= clock.t - 1000 < 75
    restored = [r for r in bulbs.log if r[0] == "restore"]
    assert len(restored) == 3 and all(r[2] is True for r in restored)


def test_a_new_plan_takes_over_in_place_without_putting_the_lights_back():
    clock, bulbs, s = show([("storm", None)])
    sent = {"done": False}

    def poll():
        if clock.t >= 1000 + 120 and not sent["done"]:
            sent["done"] = True
            return [["fortress", None]]
        return None
    assert s.run(stop=lambda: clock.t >= 1000 + 1800, poll_plan=poll) == "stopped"
    assert any(c[4] <= 6.5 for c in colors(bulbs) if c[-1] < 1000 + 120)          # the storm until then
    after = [c for c in colors(bulbs) if c[-1] >= 1000 + 120]
    assert after and all(c[4] >= 240 for c in after)                              # the fortress from then
    assert len([r for r in bulbs.log if r[0] == "restore"]) == 3                  # restored once, at the end only


def test_the_storm_taking_over_from_the_fortress_sets_its_cool_white_gently():
    clock, bulbs, s = show([("fortress", None)])
    sent = {"done": False}

    def poll():
        if clock.t >= 1000 + 600 and not sent["done"]:
            sent["done"] = True
            return [["storm", None]]
        return None
    s.run(stop=lambda: clock.t >= 1000 + 700, poll_plan=poll)
    whites = [r for r in bulbs.log if r[0] == "white"]
    assert len(whites) == 3 and all(r[2] == 6500 and r[3] == lightshow.SWITCH_PRIME_SECONDS and r[-1] >= 1000 + 600 for r in whites)    # a fade, not a flash
    assert all(c[4] <= 6.5 for c in colors(bulbs) if c[-1] >= 1000 + 600 + 5)     # and the bulbs wait no ten minutes for the storm's first look


def test_a_plan_with_a_routine_it_does_not_know_is_refused():
    with pytest.raises(HomeError, match="I know these routines"):
        Show(Bulbs(Clock(), EIDS), EIDS, [("hurricane", None)])
    with pytest.raises(HomeError, match="at least one"):
        Show(Bulbs(Clock(), EIDS), EIDS, [])


# ── what keeps a show safe ──────────────────────────────────────────────────────
def test_switching_every_light_off_ends_the_show_and_they_stay_off():
    clock, bulbs, s = show()
    seen = {"off_at": None}

    def tick():
        if clock.t >= 1000 + 60 and seen["off_at"] is None:                      # he says "turn off the lights"
            seen["off_at"] = clock.t
            for e in EIDS:
                bulbs.on[e] = False
    assert s.run(stop=lambda: False, tick=tick) == "off"
    assert clock.t < seen["off_at"] + POLL_SECONDS * 3                            # the show noticed within a couple of looks
    assert all(r[0] != "color" or r[-1] <= seen["off_at"] + POLL_SECONDS * 2 for r in bulbs.log)
    restored = [r for r in bulbs.log if r[0] == "restore"]
    assert len(restored) == 3 and all(r[2] is False for r in restored)            # their old colours are put back; the power is left alone


def test_a_light_switched_off_is_left_alone_while_the_others_carry_on():
    clock, bulbs, s = show()
    state = {"done": False}

    def tick():
        if clock.t >= 1000 + 30 and not state["done"]:
            state["done"] = True
            bulbs.on["light.b"] = False
    s.run(stop=lambda: clock.t >= 1000 + 200, tick=tick)
    assert not [c for c in colors(bulbs, "light.b") if c[-1] > 1000 + 30 + POLL_SECONDS * 2]
    assert len([c for c in colors(bulbs, "light.a") if c[-1] > 1100]) > 5 and len([c for c in colors(bulbs, "light.c") if c[-1] > 1100]) > 5


def test_a_bulb_that_does_not_answer_is_skipped_and_tried_again_while_the_others_carry_on():
    clock, bulbs, s = show()
    bulbs.broken.add("light.b")
    fixed = {"done": False}

    def tick():
        if clock.t >= 1000 + 90 and not fixed["done"]:
            fixed["done"] = True
            bulbs.broken.clear()
    s.run(stop=lambda: clock.t >= 1000 + 180, tick=tick)
    tries = [r for r in bulbs.log if r[0] == "failed" and r[2] == "light.b" and r[1] == "color"]
    assert 4 <= len(tries) <= 14                                                  # asked again every ten seconds or so, never in a hammering loop
    assert len(colors(bulbs, "light.a")) > 15 and len(colors(bulbs, "light.c")) > 15
    assert len([c for c in colors(bulbs, "light.b") if c[-1] > 1000 + 100]) > 5   # and it joins in once it answers


def test_a_light_off_the_network_is_not_a_reason_to_stop():
    clock, bulbs, s = show()
    bulbs.unreachable.update(["light.b", "light.c"])
    assert s.run(stop=lambda: clock.t >= 1000 + 120) == "stopped"                  # one still on: the show goes on
    assert len(colors(bulbs, "light.a")) > 15


# ── reading lengths of time and times of day ────────────────────────────────────
def test_lengths_of_time_are_read_the_way_they_are_said():
    assert lightshow.parse_duration("5h") == 5 * HOUR and lightshow.parse_duration("90m") == 5400 and lightshow.parse_duration("5h30m") == 19800
    assert lightshow.parse_duration("1.5 hours") == 5400 and lightshow.parse_duration("300s") == 300 and lightshow.parse_duration("2h 15min") == 8100
    assert lightshow.parse_duration("5 hrs") == 5 * HOUR and lightshow.parse_duration("45 minutes") == 2700 and lightshow.parse_duration("30 seconds") == 30
    for bad in ("", "5", "soon", "0h", "-1h", "5 days"):
        with pytest.raises(HomeError):
            lightshow.parse_duration(bad)


def test_times_of_day_are_read_the_way_they_are_said_and_a_bare_time_is_the_next_one():
    now = datetime(2026, 11, 6, 19, 50)
    at = lambda text: datetime.fromtimestamp(lightshow.parse_at(text, now))        # noqa: E731
    assert at("18:30") == datetime(2026, 11, 7, 18, 30)                            # 18:30 has gone by today: the next one is tomorrow's
    assert at("21:00") == datetime(2026, 11, 6, 21, 0)
    assert at("6:30pm") == datetime(2026, 11, 7, 18, 30) and at("6pm") == datetime(2026, 11, 7, 18, 0) and at("12am") == datetime(2026, 11, 7, 0, 0)
    assert at("tomorrow 09:15") == datetime(2026, 11, 7, 9, 15) and at("2026-11-08 18:30") == datetime(2026, 11, 8, 18, 30)
    for bad in ("banana", "25:00", "7:99", "13pm", "today 18:30", "2026-02-30 10:00", "yesterday 10:00"):
        with pytest.raises(HomeError):
            lightshow.parse_at(bad, now)


def test_a_plan_is_made_from_a_routine_a_length_and_what_comes_next():
    assert lightshow.make_plan("storm", "5h", "fortress") == [("storm", 18000.0), ("fortress", None)]
    assert lightshow.make_plan("fortress") == [("fortress", None)]
    assert lightshow.parse_plan_arg(lightshow.plan_arg(lightshow.make_plan("storm", "5h", "fortress"))) == [("storm", 18000.0), ("fortress", None)]
    with pytest.raises(HomeError, match="--then needs --for"):
        lightshow.make_plan("storm", "", "fortress")
    with pytest.raises(HomeError, match="I know these routines"):
        lightshow.make_plan("hurricane")
    assert lightshow.plan_words([("storm", 18000.0), ("fortress", None)]) == "storm for 5 h, then fortress"
    assert [lightshow.span(s) for s in (40, 600, 5 * HOUR, 5 * HOUR + 14 * 60)] == ["40 s", "10 min", "5 h", "5 h 14 min"]


# ── the process around it: start, switch, stop, status, the worker ──────────────
class StubMatter:
    def __init__(self, lights):
        self.lights = lights

    def color_lights(self):
        return self.lights

    def find(self, name, domains=None):
        for x in self.lights:
            if x["label"].lower() == name.lower():
                return {"entity_id": x["entity_id"]}, []
        return None, []

    def _label(self, s):
        return next(x["label"] for x in self.lights if x["entity_id"] == s["entity_id"])

    def _ambiguous(self, name, close):
        return f"I cannot find anything in the house called {name}."


LIGHTS = [{"entity_id": "light.lifx_1787", "label": "LIFX 1787"}, {"entity_id": "light.lifx_e25d", "label": "LIFX E25D"}]
PLAN = [("storm", 5 * HOUR), ("fortress", None)]


class Proc:
    def __init__(self, dead=False):
        self.dead = dead

    def poll(self):
        return 1 if self.dead else None


def test_start_launches_the_show_in_its_own_process_with_its_plan_and_time_and_waits_for_its_heartbeat(tmp_path):
    clock, lines, launched = Clock(), [], []
    at = lightshow.parse_at("18:30", datetime.now())

    def spawn(argv, **kw):
        launched.append((argv, kw))
        return Proc()

    def sleep(s):
        clock.sleep(s)
        if launched and not lightshow.beat_path(tmp_path).exists() and clock.t > 1002:           # the show's process comes up and beats
            lightshow.write_beat(tmp_path, {"pid": 4242, "started": clock.t, "lights": ["LIFX 1787", "LIFX E25D"], "plan": PLAN, "waiting_until": at, "phase": None})
    assert lightshow.start({}, tmp_path, StubMatter(LIGHTS), PLAN, [], at, lines.append, spawn=spawn, sleep=sleep, now=clock.now) == 0
    argv, kw = launched[0]
    assert argv[1] == "-u" and argv[3] == "run" and argv[4:6] == ["--plan", "storm:18000.0,fortress:"] and argv[6] == "--at" and float(argv[7]) == at
    assert argv[-2:] == ["light.lifx_1787", "light.lifx_e25d"]
    assert kw["stdin"] is not None and (kw.get("creationflags") or kw.get("start_new_session"))     # detached: it outlives this command
    assert lines[0].startswith("Scheduled: storm for 5 h, then fortress, on LIFX 1787, LIFX E25D, from 18:30") and "restarts" in lines[0]


def test_start_without_a_time_begins_now_and_with_names_runs_on_just_those(tmp_path):
    clock, lines, launched = Clock(), [], []

    def spawn(argv, **kw):
        launched.append(argv)
        return Proc()

    def sleep(s):
        clock.sleep(s)
        lightshow.write_beat(tmp_path, {"pid": 1, "started": clock.t, "lights": ["LIFX E25D"], "plan": [["fortress", None]], "waiting_until": None, "phase": None})
    assert lightshow.start({}, tmp_path, StubMatter(LIGHTS), [("fortress", None)], ["lifx e25d"], None, lines.append, spawn=spawn, sleep=sleep, now=clock.now) == 0
    assert "--at" not in launched[0] and launched[0][-1] == "light.lifx_e25d" and "light.lifx_1787" not in launched[0]
    assert lines[0].startswith("The fortress routine is on: LIFX E25D (fortress).")


def test_a_show_that_dies_on_the_way_up_says_why(tmp_path):
    clock, lines = Clock(), []

    def spawn(argv, stdout=None, **kw):
        stdout.write("MATTER_URL isn't set.\n")
        return Proc(dead=True)
    assert lightshow.start({}, tmp_path, StubMatter(LIGHTS), [("storm", None)], [], None, lines.append, spawn=spawn, sleep=clock.sleep, now=clock.now) == 1
    assert lines == ["The show did not start. Its last words: MATTER_URL isn't set."]
    with pytest.raises(HomeError, match="cannot find"):
        lightshow.resolve(StubMatter(LIGHTS), ["the moon"])


def test_a_show_command_while_a_show_is_on_takes_it_over_in_place(tmp_path):
    clock, lines, launched = Clock(), [], []
    lightshow.write_beat(tmp_path, {"pid": 4242, "started": clock.t, "lights": ["LIFX 1787"], "plan": [["storm", None]], "waiting_until": None, "phase": None})
    seen = {}

    def sleep(s):
        clock.sleep(s)
        if lightshow.next_path(tmp_path).exists():                              # the running show picks the plan up
            seen["plan"] = json.loads(lightshow.next_path(tmp_path).read_text(encoding="utf-8"))["plan"]
            lightshow.next_path(tmp_path).unlink()
    assert lightshow.start({}, tmp_path, StubMatter(LIGHTS), [("fortress", None)], [], None, lines.append, spawn=lambda *a, **k: launched.append(a),
                           sleep=sleep, now=clock.now) == 0
    assert seen["plan"] == [["fortress", None]] and not launched                   # no second show was started
    assert lines == ["Switched: fortress. The lights fade on from where they are."]
    assert lightshow.start({}, tmp_path, StubMatter(LIGHTS), [("storm", None)], [], 5e9, lines.append, spawn=lambda *a, **k: launched.append(a)) == 1
    assert "already on" in lines[-1] and not launched                               # a time cannot be given to a show that is already on


def test_a_show_that_does_not_pick_up_the_new_plan_is_reported(tmp_path):
    clock, lines = Clock(), []
    lightshow.write_beat(tmp_path, {"pid": 4242, "started": clock.t, "lights": ["x"], "plan": [["storm", None]], "waiting_until": None, "phase": None})
    assert lightshow.start({}, tmp_path, StubMatter(LIGHTS), [("fortress", None)], [], None, lines.append, sleep=clock.sleep, now=clock.now) == 1
    assert "did not answer" in lines[0] and not lightshow.next_path(tmp_path).exists()


def test_status_says_what_is_on_how_long_is_left_and_what_is_next(tmp_path):
    now = time.time()
    assert lightshow.status(tmp_path) == "No show is on."
    lightshow.write_beat(tmp_path, {"pid": 1, "started": now, "lights": ["LIFX 1787", "LIFX E25D"], "plan": [["storm", 18000.0], ["fortress", None]],
                                    "waiting_until": None, "phase": {"routine": "storm", "ends": now + 3 * HOUR + 14 * 60, "then": "fortress"}})
    assert lightshow.status(tmp_path, now) == "The storm routine is running on LIFX 1787, LIFX E25D, 3 h 14 min left, then the fortress."
    lightshow.write_beat(tmp_path, {"pid": 1, "started": now, "lights": ["LIFX 1787"], "plan": [["fortress", None]], "waiting_until": None,
                                    "phase": {"routine": "fortress", "ends": None, "then": None}})
    assert lightshow.status(tmp_path, now) == "The fortress routine is running on LIFX 1787."
    lightshow.write_beat(tmp_path, {"pid": 1, "started": now, "lights": ["LIFX 1787"], "plan": [["storm", 18000.0], ["fortress", None]],
                                    "waiting_until": lightshow.parse_at("18:30"), "phase": None})
    waiting = lightshow.status(tmp_path, now)
    assert waiting.startswith("A show is waiting to begin at 18:30") and waiting.endswith(": storm for 5 h, then fortress, on LIFX 1787.")


def test_stop_asks_the_show_to_finish_so_the_lights_are_put_back(tmp_path):
    clock, lines = Clock(), []
    lightshow.write_beat(tmp_path, {"pid": 4242, "started": clock.t, "lights": ["LIFX 1787"]})

    def sleep(s):
        clock.sleep(s)
        if lightshow.stop_path(tmp_path).exists():                              # the show sees the flag, restores the lights and removes its heartbeat
            lightshow.beat_path(tmp_path).unlink()
            lightshow.stop_path(tmp_path).unlink()
    killed = []
    assert lightshow.stop(tmp_path, lines.append, sleep=sleep, now=clock.now, kill=killed.append) == 0
    assert lines == ["The show is over; the lights are back as they were."] and not killed


def test_a_show_that_does_not_answer_is_ended_by_its_pid_alone(tmp_path):
    clock, lines, killed = Clock(), [], []
    lightshow.write_beat(tmp_path, {"pid": 4242, "started": clock.t, "lights": ["LIFX 1787"]})
    assert lightshow.stop(tmp_path, lines.append, sleep=clock.sleep, now=clock.now, kill=killed.append) == 1
    assert killed == [4242] and not lightshow.beat_path(tmp_path).exists() and not lightshow.stop_path(tmp_path).exists()
    assert "did not answer" in lines[0]


def test_a_stale_heartbeat_is_a_show_that_is_not_running(tmp_path):
    lightshow.beat_path(tmp_path).write_text(json.dumps({"pid": 1, "started": 0, "lights": ["x"], "beat": 1000.0}), encoding="utf-8")
    assert lightshow.read_beat(tmp_path, now=1000 + lightshow.BEAT_STALE + 1) is None
    assert lightshow.read_beat(tmp_path, now=1010)["pid"] == 1
    assert lightshow.status(tmp_path, now=1000 + lightshow.BEAT_STALE + 1) == "No show is on."
    lines = []
    assert lightshow.stop(tmp_path, lines.append) == 0 and lines == ["No show is on."] and not lightshow.beat_path(tmp_path).exists()


def test_the_worker_waits_for_its_time_without_touching_the_lights_and_then_runs_its_plan(tmp_path):
    clock = Clock()
    bulbs = Bulbs(clock, EIDS)
    begin, waits = clock.t + 50, []

    def sleep(s):
        if clock.t < begin:
            waits.append(json.loads(lightshow.beat_path(tmp_path).read_text(encoding="utf-8")))
        clock.sleep(s)
    assert lightshow.worker(bulbs, tmp_path, EIDS, [("storm", 30.0)], begin, out=lambda m: None, wall=clock.now, sleep=sleep, clock=clock.now) == 0
    assert waits and all(w["waiting_until"] == begin and w["plan"] == [["storm", 30.0]] for w in waits)
    assert all(r[-1] >= begin for r in bulbs.log)                                   # not a command before its time
    assert min(c[-1] for c in colors(bulbs)) < begin + 6 and max(c[-1] for c in colors(bulbs)) < begin + 30 + 10
    assert len([r for r in bulbs.log if r[0] == "restore"]) == 3                    # a plan that ran out: the lights are put back
    assert not lightshow.beat_path(tmp_path).exists() and not lightshow.stop_path(tmp_path).exists()


def test_a_show_cancelled_while_it_waits_never_touched_the_lights(tmp_path):
    clock = Clock()
    bulbs = Bulbs(clock, EIDS)
    begin = clock.t + 3600

    def sleep(s):
        clock.sleep(s)
        if clock.t > begin - 3000:
            lightshow.stop_path(tmp_path).write_text("stop", encoding="utf-8")
    lines = []
    assert lightshow.worker(bulbs, tmp_path, EIDS, PLAN, begin, out=lines.append, wall=clock.now, sleep=sleep, clock=clock.now) == 0
    assert bulbs.log == [] and "cancelled before it began" in lines[0]
    assert not lightshow.beat_path(tmp_path).exists()


def test_homectl_show_status_and_stop_need_no_controller_and_bad_words_are_refused_first(tmp_path):
    lines = []
    env = {"SKIPPY_HOME_STATE": str(tmp_path)}                                   # no MATTER_URL at all: only starting a show needs the controller
    run = lambda *argv: homectl.main(["show", *argv], hk=object(), out=lines.append, env=env)       # noqa: E731
    assert run("status") == 0 and lines == ["No show is on."]
    assert run("stop") == 0
    assert run("storm", "--then", "fortress") == 1 and "--then needs --for" in lines[-1]
    assert run("storm", "--for", "soon") == 1 and "cannot read" in lines[-1]
    assert run("storm", "--at", "banana") == 1 and "cannot read" in lines[-1]
    assert run("storm", "--for", "5h", "--then", "fortress", "--at", "18:30") == 1 and "MATTER_URL" in lines[-1]
