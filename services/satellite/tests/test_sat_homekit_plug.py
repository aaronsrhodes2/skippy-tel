"""The first thing moved off Alexa: the Theater Fan, a Wi-Fi smart plug. Every command is read back from the plug and, when a dozing plug did not
change, asked again on a fresh connection, so he never has to say it twice; keep-warm stops it dozing; `homectl.py prove` times it for real."""
import time

import pytest

import homectl
import homekit
from house import HomeError

from test_sat_homekit import FakePairing, kit  # noqa: F401 — the fixture

PLUG = "aa:bb:cc:00:00:02"


@pytest.fixture
def fan(kit, monkeypatch):                                                   # noqa: F811
    monkeypatch.setattr(homekit, "RETRY_SECONDS", 0)
    store = kit.store.load()
    store["devices"][PLUG] = {"name": "Theater Fan", "area": "Theater", "pairing": {"k": 1}}
    kit.store.save(store)
    kit.invalidate()
    return kit


def plug(kit, cls=FakePairing):
    p = cls(kit.fc.accessories[PLUG])
    kit.fc.pairings[PLUG] = p
    return p


class Dozy(FakePairing):
    """The first write after a quiet spell is swallowed: the plug's Wi-Fi was asleep (what makes him tell Alexa twice)."""

    def __init__(self, accessories, swallow=1, raise_instead=False):
        super().__init__(accessories)
        self.swallow, self.raise_instead = swallow, raise_instead

    async def put_characteristics(self, writes):
        if self.swallow:
            self.swallow -= 1
            self.writes.append(list(writes))
            if self.raise_instead:
                raise ConnectionResetError("the plug dropped the connection")
            return
        await super().put_characteristics(writes)


def test_the_theater_fan_is_a_switch_he_can_call_just_the_fan(fan):
    p = plug(fan)
    assert [s["entity_id"] for s in fan.states(fresh=True)] == ["switch.theater_fan"]
    assert fan.control("the fan", "on").startswith("Done: Theater Fan")
    assert p.writes == [[(2, 20, True)]] and fan.last["attempts"] == 1
    assert fan.control("theater fan", "off").startswith("Done") and p.values[(2, 20)] is False


def test_a_dozing_plug_that_ignored_the_first_command_is_asked_again_on_a_fresh_connection(fan):
    p = plug(fan, lambda acc: Dozy(acc))
    loads = len(fan.fc.loaded)
    assert fan.control("theater fan", "on").startswith("Done")
    assert p.values[(2, 20)] is True and len(p.writes) == 2 and fan.last["attempts"] == 2
    assert len(fan.fc.loaded) > loads                                        # it reconnected rather than shouting into the same dead socket


def test_a_plug_that_dropped_the_connection_is_asked_again(fan):
    p = plug(fan, lambda acc: Dozy(acc, raise_instead=True))
    assert fan.control("theater fan", "on").startswith("Done") and p.values[(2, 20)] is True and fan.last["attempts"] == 2


def test_a_plug_that_never_changes_is_reported_not_claimed(fan):
    plug(fan, lambda acc: Dozy(acc, swallow=99))
    with pytest.raises(HomeError, match="twice"):
        fan.control("theater fan", "on")


def test_keep_warm_pings_the_plug_in_the_background(fan):
    p = plug(fan)
    fan.states(fresh=True)
    reads = p.reads
    fan.keep_warm(0.01)
    for _ in range(200):
        if p.reads >= reads + 2:
            break
        time.sleep(0.01)
    assert p.reads >= reads + 2


def test_prove_switches_it_on_and_off_times_it_and_leaves_it_as_it_was(fan):
    p = plug(fan, lambda acc: Dozy(acc))
    p.values[(2, 20)] = False
    lines = []
    assert homectl.main(["prove", "theater fan", "--rounds", "2", "--gap", "0"], hk=fan, out=lines.append) == 0
    assert lines[0].startswith("1  on") and "second try" in lines[0]
    assert lines[-1].startswith("Theater Fan: 4/4 worked") and "1 needed a second try" in lines[-1]
    assert p.values[(2, 20)] is False                                       # off when we started, off when we finished


def test_prove_says_so_when_there_is_no_such_thing(fan):
    plug(fan)
    lines = []
    assert homectl.main(["prove", "garage heater", "--rounds", "1", "--gap", "0"], hk=fan, out=lines.append) == 1
    assert "cannot find" in lines[-1]
