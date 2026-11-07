"""The storm (lightshow.py): blue and white, every bulb on its own random course. A fake house, a fake clock and a seeded random source: hours of
storm in milliseconds, and the same storm every run."""
import json
import random

import pytest

import homectl
import lightshow
from house import HomeError
from lightshow import BLUE, POLL_SECONDS, Storm, pick_look


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class Bulbs:
    """The colour lights as Storm sees them (matter.Matter's colour methods), recording every command with the time it was sent."""

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

    def color_snapshot(self, eids):
        return {e: {"on": True, "level": 254, "mode": 1, "x": 24939, "y": 24701} for e in eids}

    def switch(self, eid, on):
        self.on[eid] = on
        self.log.append(("switch", eid, on, self.clock.t))

    def set_white(self, eid, kelvin, seconds=0.0, timeout=15.0, force=False):
        self._send("white", eid, kelvin)

    def set_color(self, eid, hue, saturation, seconds=0.0, timeout=15.0, force=False):
        self._send("color", eid, hue, saturation, seconds)

    def set_level(self, eid, percent, seconds=0.0, timeout=15.0, force=False):
        self._send("level", eid, percent, seconds)

    def states(self, fresh=False):
        return [{"entity_id": e, "state": "on" if on else "off"} for e, on in self.on.items() if e not in self.unreachable]

    def color_restore(self, eid, was, seconds=2.0, power=True, timeout=15.0):
        self.log.append(("restore", eid, power, self.clock.t))


EIDS = ["light.a", "light.b", "light.c"]


def storm(seed=7, eids=EIDS):
    clock = Clock()
    bulbs = Bulbs(clock, eids)
    return clock, bulbs, Storm(bulbs, eids, rng=random.Random(seed), clock=clock.now, sleep=clock.sleep)


def colors(bulbs, eid=None):
    return [r for r in bulbs.log if r[0] == "color" and (eid is None or r[1] == eid)]


def test_every_bulb_roils_on_its_own_course_between_blue_and_white():
    clock, bulbs, s = storm()
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


def test_the_bulbs_never_match_and_never_move_together():
    clock, bulbs, s = storm()
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
    clock, bulbs, s = storm()
    s.run(stop=lambda: clock.t >= 1000 + 30)
    kinds = [(r[0], r[1]) for r in bulbs.log]
    for eid in EIDS:                                                              # on, then a cool white (the bulb keeps it: the white end of a look is cold), then looks
        assert kinds.index(("switch", eid)) < kinds.index(("white", eid)) < kinds.index(("color", eid))
    assert [r for r in bulbs.log if r[0] == "white"][0][2] == lightshow.COOL_WHITE_K == 6500
    restored = [r for r in bulbs.log if r[0] == "restore"]
    assert sorted(r[1] for r in restored) == EIDS and all(r[2] is True for r in restored)     # back on or off as they were


def test_switching_every_light_off_ends_the_storm_and_they_stay_off():
    clock, bulbs, s = storm()
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
    clock, bulbs, s = storm()
    state = {"done": False}

    def tick():
        if clock.t >= 1000 + 30 and not state["done"]:
            state["done"] = True
            bulbs.on["light.b"] = False
    s.run(stop=lambda: clock.t >= 1000 + 200, tick=tick)
    assert not [c for c in colors(bulbs, "light.b") if c[-1] > 1000 + 30 + POLL_SECONDS * 2]
    assert len([c for c in colors(bulbs, "light.a") if c[-1] > 1100]) > 5 and len([c for c in colors(bulbs, "light.c") if c[-1] > 1100]) > 5


def test_a_bulb_that_does_not_answer_is_skipped_and_tried_again_while_the_others_carry_on():
    clock, bulbs, s = storm()
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
    clock, bulbs, s = storm()
    bulbs.unreachable.update(["light.b", "light.c"])
    assert s.run(stop=lambda: clock.t >= 1000 + 120) == "stopped"                  # one still on: the show goes on
    assert len(colors(bulbs, "light.a")) > 15


def test_a_look_is_always_blue_to_white_and_apart_from_where_the_others_are_heading():
    for seed in range(300):
        look = pick_look(random.Random(seed), [0.1, 0.9])
        assert 0 <= look.white <= 1 and abs(look.saturation - 100 * (1 - look.white)) < 1e-9
        assert BLUE[0] <= look.hue <= BLUE[1] and 45 <= look.level <= 100 and 0.5 <= look.seconds <= 6.5
        assert min(abs(look.white - 0.1), abs(look.white - 0.9)) >= lightshow.APART
    assert pick_look(random.Random(1), [0.1, 0.9]) == pick_look(random.Random(1), [0.1, 0.9])          # the same seed, the same storm
    assert [(round(a, 2), round(b, 2)) for a, b in lightshow._free([0.1, 0.9])] == [(0.32, 0.68)]      # the room the other two leave
    assert lightshow._free([0.5]) == [(0.0, 0.5 - lightshow.APART), (0.5 + lightshow.APART, 1.0)]
    crowded = pick_look(random.Random(3), [0.0, 0.25, 0.5, 0.75, 1.0])                                 # no room left: the best there is, never a crash
    assert 0 <= crowded.white <= 1


# ── start, stop, status ─────────────────────────────────────────────────────────
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


class Proc:
    def __init__(self, dead=False):
        self.dead = dead

    def poll(self):
        return 1 if self.dead else None


def test_start_launches_the_show_in_its_own_process_and_waits_for_its_heartbeat(tmp_path):
    clock, lines, launched = Clock(), [], []

    def spawn(argv, **kw):
        launched.append((argv, kw))
        return Proc()

    def sleep(s):
        clock.sleep(s)
        if len(launched) and not lightshow.beat_path(tmp_path).exists() and clock.t > 1002:      # the show's process comes up and beats
            lightshow.write_beat(tmp_path, {"pid": 4242, "started": clock.t, "lights": ["LIFX 1787", "LIFX E25D"]})
    assert lightshow.start({}, tmp_path, StubMatter(LIGHTS), [], lines.append, spawn=spawn, sleep=sleep, now=clock.now) == 0
    argv, kw = launched[0]
    assert argv[-3:] == ["run", "light.lifx_1787", "light.lifx_e25d"] and argv[1] == "-u"
    assert kw["stdin"] is not None and (kw.get("creationflags") or kw.get("start_new_session"))        # detached: it outlives this command
    assert lines == ["The storm is on: LIFX 1787, LIFX E25D. `homectl.py storm stop` ends it and puts them back."]
    assert lightshow.start({}, tmp_path, StubMatter(LIGHTS), [], lines.append, spawn=spawn, sleep=sleep) == 0     # a second start does not start a second show
    assert len(launched) == 1 and lines[-1].startswith("The storm is running on LIFX 1787, LIFX E25D")


def test_start_names_which_lights_and_says_so_when_the_show_dies_on_the_way_up(tmp_path):
    clock, lines = Clock(), []
    lightshow.log_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    spawned = []

    def spawn(argv, stdout=None, **kw):
        spawned.append(argv)
        stdout.write("MATTER_URL isn't set.\n")
        return Proc(dead=True)
    assert lightshow.start({}, tmp_path, StubMatter(LIGHTS), ["lifx e25d"], lines.append, spawn=spawn, sleep=clock.sleep, now=clock.now) == 1
    assert spawned[0][-1] == "light.lifx_e25d" and "light.lifx_1787" not in spawned[0]
    assert lines == ["The storm did not start. Its last words: MATTER_URL isn't set."]
    with pytest.raises(HomeError, match="cannot find"):
        lightshow.resolve(StubMatter(LIGHTS), ["the moon"])


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
    assert lines == ["The storm is over; the lights are back as they were."] and not killed


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
    assert lightshow.status(tmp_path, now=1000 + lightshow.BEAT_STALE + 1) == "The storm is not running."
    assert "running on x" in lightshow.status(tmp_path, now=1010)
    lines = []
    assert lightshow.stop(tmp_path, lines.append) == 0 and lines == ["The storm is not running."] and not lightshow.beat_path(tmp_path).exists()


def test_homectl_storm_status_and_stop_need_no_controller(tmp_path):
    lines = []
    env = {"SKIPPY_HOME_STATE": str(tmp_path)}                                   # no MATTER_URL at all: only start and run need the controller
    assert homectl.main(["storm", "status"], hk=object(), out=lines.append, env=env) == 0 and lines == ["The storm is not running."]
    assert homectl.main(["storm", "stop"], hk=object(), out=lines.append, env=env) == 0
    assert homectl.main(["storm", "start"], hk=object(), out=lines.append, env=env) == 1 and "MATTER_URL" in lines[-1]
