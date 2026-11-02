"""The Generac generator (generac.py) against a fake Mobile Link: Mobile Link's own JSON shapes parsed into one read-only generator.<name>,
an outage and the power coming back said in every room and DMed, an exercise only remembered, a fault and maintenance told, the morning's line
only when it needs him, the hub's mirror (never a cookie) for the other processes, the session kept and refreshed, a refused session asking
for a sign-in, and build_house choosing the owner. Nothing here reaches Generac."""
import asyncio
import json
import time

import pytest

import generac
import homectl
import house

NOW = 1_800_000_000.0


def apparatus(status=1, connected=True):
    return {"apparatusId": 4242, "serialNumber": "3005031326", "name": "Generator", "type": 0, "modelNumber": "G0071780",
            "apparatusStatus": status, "isConnected": connected, "showWarning": False}


def detail(status=1, battery="13.4", hours="412.5", seen=NOW - 60, warning=False, maint=False, ecode=None, text="Ready to run"):
    return {"apparatusId": 4242, "name": "Generator", "serialNumber": "3005031326", "apparatusStatus": status, "isConnected": True,
            "showWarning": warning, "hasMaintenanceAlert": maint, "eCodeLabel": ecode, "statusLabel": "Ready", "statusText": text,
            "lastSeen": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(seen)),
            "properties": [{"name": "Battery Voltage", "value": battery, "type": 69}, {"name": "Run Hours", "value": hours, "type": 70},
                           {"name": "Protection Time", "value": "17", "type": 31}, {"name": "Exercise Schedule", "value": "Tue 12:00", "type": 12}]}


class FakeLink:
    def __init__(self):
        self.now = [(apparatus(), detail())]

    def set(self, status=1, **kw):
        self.now = [(apparatus(status), detail(status, **kw))]

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
    g = generac.Generac(generac.Session(tmp_path), api=FakeLink(), clock=clock, start=False, log=lambda m: None)
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


def test_mobile_links_json_becomes_one_read_only_generator_and_home_state_says_it(gen):
    states = gen.states()
    assert [s["entity_id"] for s in states] == ["generator.generator"] and states[0]["state"] == "ready"
    a = states[0]["attributes"]
    assert (a["battery_voltage"], a["run_hours"], a["protection_hours"], a["serial"], a["model"]) == (13.4, 412.5, 17.0, "3005031326", "G0071780")
    assert a["properties"] == {"Exercise Schedule": "Tue 12:00"}
    h = gen
    said = h.state_of("generator")
    assert said.startswith("Generator is ready") and "battery 13.4 volts" in said and "412.5 run hours" in said
    assert "only be read" in h.control("generator", "turn_on")
    with pytest.raises(house.HomeError):
        gen._service("generator", "turn_on", {})


def test_an_outage_is_said_in_every_room_and_dmed_and_the_power_back_too(gen):
    hub = Hub()
    gen.api.set(2)
    gen.poll()
    assert gen.states()[0]["state"] == "running"
    dms = drain(gen, hub)
    assert hub.rooms["office"].said == hub.rooms["theater"].said == ["The generator just started. The power must be out."]
    assert dms == ["The generator just started. The power must be out."]
    gen.clock_.t += 47 * 60
    gen.api.set(1)
    gen.poll()
    dms = drain(gen, hub)
    assert hub.rooms["theater"].said[-1] == "The generator has stopped after 47 minutes. The power's back." and dms == [hub.rooms["theater"].said[-1]]
    assert gen.states()[0]["attributes"]["last_run"] == gen.clock_.t
    assert "last ran for an outage just now" in gen.state_of("generator")


def test_an_exercise_is_only_remembered_never_announced(gen):
    hub = Hub()
    gen.api.set(3)
    gen.poll()
    gen.clock_.t += 12 * 60
    gen.api.set(1)
    gen.poll()
    assert drain(gen, hub) == [] and hub.rooms["office"].said == []
    gen.clock_.t += 3 * 86400
    assert "last exercised 3 days ago" in gen.state_of("generator")


def test_a_fault_is_said_in_one_room_and_dmed_and_maintenance_only_dmed(gen):
    hub = Hub()
    gen.api.set(4, ecode="E1300 Low Battery", warning=True)
    gen.poll()
    dms = drain(gen, hub)
    assert hub.rooms["office"].said == ["The generator reports a fault: E1300 Low Battery."] and hub.rooms["theater"].said == []
    assert dms == ["The generator reports a fault: E1300 Low Battery."]
    gen.api.set(1, maint=True, text="Schedule A maintenance due")
    gen.poll()
    dms = drain(gen, hub)
    assert dms == ["The generator says maintenance is due: Schedule A maintenance due."] and len(hub.rooms["office"].said) == 1


def test_the_morning_speaks_only_when_the_generator_needs_him(gen):
    assert gen.attention() is None
    gen.api.set(1, maint=True)
    gen.poll()
    assert gen.attention() == "The generator: maintenance is due."
    gen.api.set(5)
    gen.poll()
    assert "switched off, so it won't start in an outage" in gen.attention()
    gen.api.set(1, seen=NOW - 3 * 3600)                    # Mobile Link hasn't heard from it in hours: its Wi-Fi is down
    gen.poll()
    assert gen.states()[0]["state"] == "offline" and "hasn't heard from it" in gen.attention()


def test_the_status_line_and_the_hubs_heartbeat(gen):
    import hub as hub_mod
    assert gen.summary() == "Generator ready, battery 13.4 V"
    assert hub_mod.backend_health(gen) == {"generac": {"ok": True, "things": 1, "detail": "Generator ready, battery 13.4 V"}}


def test_the_mirror_carries_no_cookie_and_the_other_processes_read_it(tmp_path, gen):
    gen.session.save({"cookies": [{"name": ".AspNetCore.Cookies", "value": "SECRET-COOKIE", "domain": "app.mobilelinkgen.com"}]})
    gen.publish()
    text = (tmp_path / "gen.json").read_text(encoding="utf-8")
    assert "SECRET" not in text and json.loads(text)["connected"] is True
    mirror = generac.Generac(generac.Session(tmp_path), owner=False, clock=gen.clock_, log=lambda m: None)
    assert mirror.states()[0]["state"] == "ready" and "battery 13.4 volts" in mirror.state_of("generator")
    gen.clock_.t += 3600                                    # the hub stopped publishing
    assert mirror.states()[0]["state"] == "unavailable"


class Resp:
    def __init__(self, code, body=None, location=""):
        self.status_code, self.body, self.headers = code, body, {"Location": location}

    def json(self):
        if self.body is None:
            raise ValueError
        return self.body


class FakeHttp:
    def __init__(self, answers):
        import requests
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}
        self.answers = answers
        self.asked = []

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.asked.append(url)
        code, body = self.answers.pop(0)
        if code == 200:
            self.cookies.set(".AspNetCore.Cookies", "refreshed", domain="app.mobilelinkgen.com", path="/")
        return Resp(code, body, "https://generacconnectivity.b2clogin.com/x" if code == 302 else "")


def test_the_session_is_seeded_from_the_vault_kept_when_refreshed_and_a_refusal_asks_for_a_sign_in(tmp_path):
    store = generac.Session(tmp_path, seed=".AspNetCore.Cookies=vault-value; other=1")
    http = FakeHttp([(200, [apparatus()]), (200, detail())])
    link = generac.MobileLink(store, http=http)
    assert http.cookies.get(".AspNetCore.Cookies") == "vault-value"
    assert len(link.fetch()) == 1 and http.asked[0].endswith("/v2/Apparatus/list")
    assert {c["value"] for c in json.loads((tmp_path / "session.json").read_text())["cookies"]} >= {"refreshed"}   # the slid cookie, kept
    link.http.answers = [(302, None)]
    with pytest.raises(generac.SignInNeeded):
        link.fetch()


def test_a_lost_session_is_told_once(tmp_path):
    class Refusing:
        def fetch(self):
            raise generac.SignInNeeded("Mobile Link wants him to sign in again: run `python generac.py signin`")
    store = generac.Session(tmp_path)
    store.save({"cookies": [{"name": "c", "value": "v"}]})
    g = generac.Generac(store, api=Refusing(), start=False, log=lambda m: None, poll_s=0)
    g.poll_s, g._closed = 0, False
    import threading
    calls = []
    orig = generac.time.sleep
    generac.time.sleep = lambda s: (calls.append(s), setattr(g, "_closed", len(calls) >= 3))
    try:
        threading.Thread(target=g._keep).start()
        deadline = time.time() + 2
        while not g._closed and time.time() < deadline:
            orig(0.01)
    finally:
        generac.time.sleep = orig
    events = g.take_events()
    assert [e["kind"] for e in events] == ["signin"] and not g.connected
    assert "sign in" in g.attention().lower() and "Mobile Link" in generac.spoken(events[0])


def test_build_house_adds_the_generator_once_signed_in_and_only_the_hub_owns_it(tmp_path):
    env = {"GENERAC_STATE": str(tmp_path), "SKIPPY_NEST": "0", "SKIPPY_RING": "0", "RING_STATE": str(tmp_path / "ring")}
    assert generac.generac_of(homectl.build_house(env)) is None                       # never signed in: not in the house
    generac.Session(tmp_path).save_mirror({"at": time.time(), "connected": True, "why": "", "gens": {}})
    g = generac.generac_of(homectl.build_house(env))
    assert g is not None and not g.owner and generac.build(env, g) is None             # a mirror has nothing to tell
    assert generac.generac_of(homectl.build_house({**env, "SKIPPY_GENERAC": "0"})) is None
