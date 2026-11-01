"""The garage door closing itself when the car drives away (autoclose.py), against a fake car and a fake door, plus the real Garage's
close-only carve-out against the simulated ratgdo (test_sat_garage.py's): departure with the door open closes it; closed does nothing; an
obstruction is an alert (DM at once, said in the house, one reminder 10 minutes on) with no retry; arriving never opens; nothing can open
through the carve-out."""
import inspect

import pytest

import autoclose
from autoclose import AutoClose
from house import HomeError

HOME = (47.6000, -122.3000)
AT_HOME = {"state": "online", "shift_state": "P", "latitude": 47.6000, "longitude": -122.3000}
DRIVEWAY = {"state": "driving", "shift_state": "R", "latitude": 47.6005, "longitude": -122.3000}      # ~55 m: still home
AWAY = {"state": "driving", "shift_state": "D", "latitude": 47.6030, "longitude": -122.3000}          # ~330 m


class Door:
    """Only what autoclose may use: door() and close_on_departure(). Anything else it touches fails the test."""

    def __init__(self, state="open", obstructed=False, closes=True):
        self.state, self.obstructed, self.closes = state, obstructed, closes
        self.closings = 0

    def door(self):
        return {"state": self.state, "obstructed": self.obstructed}

    def close_on_departure(self):
        self.closings += 1
        if self.obstructed:
            raise HomeError("obstructed")
        if self.state == "closed":
            return "already closed"
        self.state = "closing"
        return "closing"

    def __getattr__(self, name):                                  # e.g. control(), _service(): never reached by autoclose
        raise AssertionError(f"autoclose used garage.{name}")


class Rig:
    def __init__(self, door, car=AT_HOME):
        self.door, self.car, self.now = door, dict(car), 1000.0
        self.dms, self.said = [], []
        self.ac = AutoClose(door, lambda: self.car, lambda: HOME, self.dms.append, self.said.append, clock=lambda: self.now,
                            wall=lambda: 0.0, log=lambda m: None, grace_s=20, wait_s=60, repeat_s=600)

    def step(self, seconds=10, car=None):
        if car is not None:
            self.car = dict(car)
        self.now += seconds
        self.ac.tick()


def leave(rig):
    rig.step(car=AT_HOME)
    rig.step(car=AWAY)                                            # away and driving: the grace starts
    rig.step(car=AWAY)
    rig.step(car=AWAY)                                            # 20 s of it: gone


def test_departure_with_the_door_open_closes_it():
    rig = Rig(Door("open"))
    leave(rig)
    assert rig.door.closings == 1 and rig.dms == []
    rig.door.state = "closed"
    rig.step()
    assert rig.dms == [autoclose.CLOSED] and rig.said == []
    assert "closed the garage door" in rig.ac.news() and rig.ac.news() is None    # told once, in the homecoming routine
    for _ in range(80):
        rig.step(car=AWAY)                                        # still away: nothing more
    assert rig.door.closings == 1 and len(rig.dms) == 1


def test_departure_with_the_door_closed_does_nothing():
    rig = Rig(Door("closed"))
    leave(rig)
    assert rig.door.closings == 0 and rig.dms == [] and rig.said == []


def test_the_driveway_and_the_grace_never_trigger_it():
    rig = Rig(Door("open"))
    rig.step(car=AT_HOME)
    for _ in range(20):
        rig.step(car=DRIVEWAY)                                    # backing out, stopping in the driveway
    rig.step(car=AWAY)
    rig.step(car={**AWAY, "state": "online", "shift_state": "P"})  # parked just down the road: the grace starts over
    rig.step(car=AWAY)
    assert rig.door.closings == 0
    rig.step(car=AWAY)
    rig.step(car=AWAY)
    assert rig.door.closings == 1


def test_an_obstruction_is_an_alert_with_no_retry_and_one_reminder():
    rig = Rig(Door("open", obstructed=True))
    leave(rig)
    assert rig.door.closings == 0                                 # blocked beforehand: not even tried
    assert rig.dms == [autoclose.OBSTRUCTED] and rig.said == [autoclose.OBSTRUCTED]
    for _ in range(59):
        rig.step(car=AWAY)
    assert rig.dms == [autoclose.OBSTRUCTED]
    rig.step(car=AWAY)                                            # 10 minutes on, still open: one reminder
    assert rig.dms == [autoclose.OBSTRUCTED, autoclose.STILL_OPEN]
    for _ in range(100):
        rig.step(car=AWAY)
    assert len(rig.dms) == 2 and rig.door.closings == 0


def test_the_eyes_reverse_it_while_closing_no_second_try():
    rig = Rig(Door("open"))
    leave(rig)
    assert rig.door.closings == 1
    rig.door.state, rig.door.obstructed = "opening", True         # the opener's safety eyes reversed it
    rig.step(car=AWAY)
    assert rig.dms == [autoclose.OBSTRUCTED] and rig.said == [autoclose.OBSTRUCTED]
    for _ in range(100):
        rig.step(car=AWAY)
    assert rig.door.closings == 1


def test_not_closed_in_time_is_an_alert():
    rig = Rig(Door("open"))
    leave(rig)
    for _ in range(6):
        rig.step(car=AWAY)                                        # 60 s and still "closing"
    assert rig.dms == []
    rig.step(car=AWAY)
    assert rig.dms == [autoclose.TIMED_OUT.format(s=60)] and rig.said == rig.dms
    assert rig.door.closings == 1


def test_every_failure_is_its_own_dm():
    rig = Rig(Door("open", obstructed=True))
    leave(rig)
    rig.step(car=AT_HOME)                                         # home again (the reminder is dropped: he can see it)
    leave(rig)
    assert rig.dms == [autoclose.OBSTRUCTED, autoclose.OBSTRUCTED]


def test_an_offline_opener_is_reported():
    rig = Rig(Door("unavailable"))
    leave(rig)
    assert rig.dms == [autoclose.OFFLINE] and rig.door.closings == 0


def test_arriving_never_opens():
    rig = Rig(Door("closed"), car=AWAY)
    for car in (AWAY, AWAY, DRIVEWAY, AT_HOME, AT_HOME):
        rig.step(car=car)
    assert rig.door.state == "closed" and rig.door.closings == 0 and rig.dms == []


def test_a_hub_started_while_away_waits_for_home_first():
    rig = Rig(Door("open"), car=AWAY)
    for _ in range(10):
        rig.step(car=AWAY)
    assert rig.door.closings == 0


def test_the_home_geofence_counts_too():
    rig = Rig(Door("open"))
    rig.ac.home = lambda: None
    rig.ac.home_geofence = "home"
    rig.step(car={"state": "online", "geofence": "Home"})
    for _ in range(3):
        rig.step(car={"state": "driving", "shift_state": "D", "geofence": None})
    assert rig.door.closings == 1


# ── the real Garage: the carve-out only closes ──
def test_the_carve_out_cannot_open():
    pytest.importorskip("aioesphomeapi")
    from test_sat_garage import DOOR, FakeRatgdo, make
    import garage

    assert list(inspect.signature(garage.Garage.close_on_departure).parameters) == ["self"]     # nothing to say "open" with
    box = FakeRatgdo()
    box.position = 1.0
    g = make(box)
    assert g.close_on_departure() == "closing"
    assert box.commands == [("cover", DOOR, 0.0, False)]
    box.position = 0.0
    box.push()
    from test_sat_garage import wait_for
    assert wait_for(lambda: g.door()["state"] == "closed")
    assert g.close_on_departure() == "already closed" and len(box.commands) == 1
    box.position, box.obstructed = 1.0, True
    box.push()
    assert wait_for(lambda: g.door()["obstructed"])
    with pytest.raises(HomeError):
        g.close_on_departure()
    assert all(c[2] in (0.0, None) for c in box.commands if c[0] == "cover")    # never once a position above closed
    # and the spoken-yes rule is untouched for everything else
    assert "confirm" in g.control("garage door", "open")
    g.close()


def test_autoclose_only_ever_calls_close():
    src = inspect.getsource(autoclose)
    assert "close_on_departure()" in src
    for forbidden in ("open_cover", ".control(", "_service(", "set_position", "cover_command"):
        assert forbidden not in src, forbidden


def test_build_needs_a_ratgdo_and_the_switch():
    class Plain:
        parts = []
    assert autoclose.build({}, None, lambda: HOME) is None
    assert autoclose.build({}, Plain(), lambda: HOME) is None
