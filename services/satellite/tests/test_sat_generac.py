"""The Generac generator (generac.py) against a fake genmon: genmon's own nested JSON (status_json, maint_json, outage_json, getbase) parsed into one
read-only generator.<name>, an outage and the power coming back said in every room and DMed, an exercise only remembered, an alarm and service
due told, the morning's line only when it needs him, the hub's mirror for the other processes, genmon's commands limited to reads, and
build_house choosing the owner. Nothing here reaches a real genmon."""
import asyncio
import json
import time

import pytest

import generac
import homectl
import house

NOW = 1_800_000_000.0


def genmon(engine="Off - Ready", switch="Auto", base="READY", battery="13.40 V", alarm="None", outage="No", run_log="10/27/26 12:00:05 Running - Exercise"):
    """genmon's four answers, in its own shapes (generac_evolution.DisplayStatus / DisplayMaintenance / DisplayOutage)."""
    status = {"Status": [{"Engine": [{"Switch State": switch}, {"Engine State": engine}, {"Battery Voltage": battery}, {"RPM": "0"},
                                     {"System In Alarm": alarm}]},
                         {"Line": [{"Utility Voltage": "241 V"}]},
                         {"Last Log Entries": {"Logs": {"Alarm Log": "05/02/26 08:00:00 Low Battery : Alarm Code: 1300",
                                                        "Service Log": "", "Run Log": run_log}}},
                         {"Time": [{"Monitor Time": "Sunday November 01, 2026 17:00:00"}]}]}
    maint = {"Maintenance": [{"Model": "G0071780"}, {"Controller Detected": "Evolution 2.0, Air Cooled"},
                             {"Exercise": [{"Exercise Time": "Tuesday 12:00 Quiet Mode Off"}, {"Exercise Duration": "12 min"}]},
                             {"Service": [{"Service A Due": "42 hrs or 09/14/2027"}, {"Service B Due": "142 hrs or 09/14/2028"},
                                          {"Battery Check Due": "09/14/2027"}, {"Total Run Hours": "412.5 h"}]}]}
    out = {"Outage": [{"Status": "No outage has occurred since program launched."}, {"System In Outage": outage}, {"Utility Voltage": "241 V"}]}
    return (base, status, maint, out)


class FakeGenmon:
    def __init__(self):
        self.now = genmon()

    def set(self, **kw):
        self.now = genmon(**kw)

    def fetch(self):
        return self.now


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


@pytest.fixture
def gen(tmp_path):
    clock = Clock()
    g = generac.Generac(generac.Mirror(tmp_path), api=FakeGenmon(), clock=clock, start=False, log=lambda m: None)
    g.poll()
    g.clock_ = clock
    return g


class Room:
    def __init__(self):
        self.said = []

    async def announce(self, text):
        self.said.append(text)


class Hub:
    def __init__(self):
        self.rooms = {"office": Room(), "theater": Room()}


def drain(gen, hub):
    """One pass of the Watch (its loop, stopped after the first sleep), and the DMs it sent."""
    dms = []
    watch = generac.Watch(gen, lambda text, image=None: dms.append(text), log=lambda m: None)

    async def once():
        task = asyncio.ensure_future(watch.run(hub, lambda: "office", every=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(once())
    time.sleep(0.1)                                         # the DMs go on their own threads
    return dms


def test_genmons_json_becomes_one_read_only_generator_and_home_state_says_it(gen):
    states = gen.states()
    assert [s["entity_id"] for s in states] == ["generator.generator"] and states[0]["state"] == "ready"
    a = states[0]["attributes"]
    assert (a["battery_voltage"], a["run_hours"], a["utility_voltage"], a["model"]) == (13.4, 412.5, 241.0, "G0071780")
    assert (a["service_a"], a["exercise_time"], a["fault"], a["outage"]) == ("42 hrs or 09/14/2027", "Tuesday 12:00 Quiet Mode Off", None, False)
    assert a["last_exercise"] and a["last_alarm"].startswith("05/02/26")             # from the controller's own logs
    said = gen.state_of("generator")
    assert said.startswith("Generator is ready") and "battery 13.4 volts" in said and "exercises Tuesday 12:00" in said and "412.5 run hours" in said
    assert "only be read" in gen.control("generator", "turn_on")
    with pytest.raises(house.HomeError):
        gen._service("generator", "turn_on", {})


def test_only_genmons_read_commands_are_ever_sent():
    class Http:
        asked = []

        def get(self, url, timeout=None, verify=None):
            self.asked.append(url)
            return type("R", (), {"status_code": 200, "text": "READY", "json": lambda s: {}})()
    g = generac.Genmon("http://genmon:8000", http=Http())
    g.fetch()
    assert [u.rsplit("/", 1)[1] for u in Http.asked] == ["getbase", "status_json", "maint_json", "outage_json"]
    for write in ("start", "stop", "setexercise", "settime", "setquiet"):
        with pytest.raises(house.HomeError):
            g.cmd(write)


def test_an_outage_is_said_in_every_room_and_dmed_and_the_power_back_too(gen):
    hub = Hub()
    gen.api.set(engine="Running", base="RUNNING", outage="Yes")
    gen.poll()
    assert gen.states()[0]["state"] == "running"
    dms = drain(gen, hub)
    assert hub.rooms["office"].said == hub.rooms["theater"].said == ["The generator just started. The power must be out."]
    assert dms == ["The generator just started. The power must be out."]
    assert "the utility power is out" in gen.state_of("generator")
    gen.clock_.t += 47 * 60
    gen.api.set()
    gen.poll()
    dms = drain(gen, hub)
    assert hub.rooms["theater"].said[-1] == "The generator has stopped after 47 minutes. The power's back." and dms == [hub.rooms["theater"].said[-1]]
    assert gen.states()[0]["attributes"]["last_run"] == gen.clock_.t and "last ran for an outage just now" in gen.state_of("generator")


def test_a_hand_start_is_said_in_one_room(gen):
    hub = Hub()
    gen.api.set(engine="Running", switch="Manual", base="RUNNING-MANUAL")
    gen.poll()
    assert drain(gen, hub) == ["The generator was started by hand."] and hub.rooms["theater"].said == []


def test_an_exercise_is_only_remembered_never_announced(gen):
    hub = Hub()
    gen.api.set(engine="Running - Exercising", base="EXERCISING")
    gen.poll()
    gen.clock_.t += 12 * 60
    gen.api.set()
    gen.poll()
    assert drain(gen, hub) == [] and hub.rooms["office"].said == []
    gen.clock_.t += 3 * 86400
    assert "last exercised 3 days ago" in gen.state_of("generator")


def test_an_alarm_is_said_in_one_room_and_dmed_and_service_due_only_dmed(gen):
    hub = Hub()
    gen.api.set(alarm="Low Battery", base="ALARM")
    gen.poll()
    dms = drain(gen, hub)
    assert gen.states()[0]["state"] == "warning"
    assert hub.rooms["office"].said == ["The generator reports an alarm: Low Battery."] and hub.rooms["theater"].said == []
    assert dms == ["The generator reports an alarm: Low Battery."]
    gen.api.set(base="SERVICEDUE")
    gen.poll()
    dms = drain(gen, hub)
    assert dms == ["The generator says service is due: service A in 42 hrs or 09/14/2027, service B in 142 hrs or 09/14/2028, "
                   "battery check in 09/14/2027."] and len(hub.rooms["office"].said) == 1


def test_the_morning_speaks_only_when_the_generator_needs_him(gen):
    assert gen.attention() is None
    gen.api.set(switch="Off", base="OFF")
    gen.poll()
    assert gen.attention() == "The generator: its switch is off, so it won't start in an outage."
    gen.api.set(base="SERVICEDUE")
    gen.poll()
    assert gen.attention().startswith("The generator: service is due (service A in 42 hrs")
    gen.connected, gen.why = False, "genmon at http://genmon:8000 is not answering (ConnectionError)"
    assert "can't see the generator" in gen.attention()


def test_the_status_line_and_the_hubs_heartbeat(gen):
    import hub as hub_mod
    assert gen.summary() == "Generator ready, battery 13.4 V"
    assert hub_mod.backend_health(gen) == {"generac": {"ok": True, "things": 1, "detail": "Generator ready, battery 13.4 V"}}


def test_the_mirror_feeds_the_other_processes_and_goes_stale(tmp_path, gen):
    gen.publish()
    assert json.loads((tmp_path / "gen.json").read_text(encoding="utf-8"))["connected"] is True
    mirror = generac.Generac(generac.Mirror(tmp_path), owner=False, clock=gen.clock_, log=lambda m: None)
    assert mirror.states()[0]["state"] == "ready" and "battery 13.4 volts" in mirror.state_of("generator")
    gen.clock_.t += 3600                                    # the hub stopped publishing
    assert mirror.states()[0]["state"] == "unavailable"


def test_build_house_adds_the_generator_with_genmon_and_only_the_hub_polls(tmp_path):
    env = {"GENERAC_STATE": str(tmp_path), "SKIPPY_NEST": "0"}
    assert generac.generac_of(homectl.build_house(env)) is None                       # no genmon yet: not in the house
    g = generac.generac_of(homectl.build_house({**env, "GENMON_URL": "http://genmon:8000"}))
    assert g is not None and not g.owner and g.api is None and generac.build(env, g) is None   # a mirror: nothing polled, nothing told
    owner = generac.from_env({**env, "GENMON_URL": "http://genmon:8000", "SKIPPY_GENERAC_OWNER": "1"}, tmp_path)
    owner.close()
    assert owner.owner and owner.api.url == "http://genmon:8000"
    assert generac.generac_of(homectl.build_house({**env, "GENMON_URL": "x", "SKIPPY_GENERAC": "0"})) is None


def test_the_dm_goes_through_the_portal_and_is_retried():
    sent = []

    def post(text):
        sent.append(text)
        if len(sent) < 2:
            raise OSError("portal down")
        return True
    generac.portal_alert("https://portal", "tok", log=lambda m: None, post=post, wait_s=0)("The generator just started. The power must be out.")
    assert sent == ["The generator just started. The power must be out."] * 2
