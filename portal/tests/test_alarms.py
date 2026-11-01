"""Alarms & timers (alarms.py): the hub's schedule in, the page's Stop / Snooze / add out, and the backup that messages his phone when the
house can't ring (the hub down at alarm time, gone quiet mid-ring, or silent the evening before)."""
import pytest

from alarms import Board
from tabletop import MemoryStore

T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def view(due, key="1@x", fired=(), ringing=None, kind="alarm"):
    return {"at": due, "alarms": [{"id": 1, "kind": kind, "key": key, "due": due, "hhmm": "06:30", "label": "", "where": "upstairs"}],
            "timers": [], "ringing": ringing, "fired": list(fired), "health": {"enclave": True}, "done": []}


def board(clock, sent):
    return Board(store=MemoryStore(), notify=lambda text: sent.append(text) or True, clock=clock, log=lambda m: None)


def test_stop_from_the_phone_is_queued_for_the_hub_then_acknowledged():
    clock, sent = Clock(), []
    b = board(clock, sent)
    asked = b.ask({"op": "stop"})
    reply = b.report(view(T0 + 3600))
    assert [c["op"] for c in reply["commands"]] == ["stop"] and reply["commands"][0]["id"] == asked["id"]
    assert b.state()["pending"] == [asked["id"]]
    body = view(T0 + 3600)
    body["done"] = [{"id": asked["id"], "said": "Alarm off."}]
    reply = b.report(body)
    assert reply["commands"] == [] and asked["id"] in reply["ack"] and b.state()["results"][asked["id"]]["said"] == "Alarm off."
    with pytest.raises(ValueError):
        b.ask({"op": "rm -rf"})
    with pytest.raises(ValueError):
        b.ask({"op": "add", "time": "25:00"})
    assert b.ask({"op": "add", "time": "06:30", "repeat": "weekdays", "where": "upstairs"})["id"]


def test_the_hub_silent_at_alarm_time_messages_his_phone_once():
    clock, sent = Clock(), []
    b = board(clock, sent)
    b.report(view(T0 + 600, key="1@600"))
    clock.t = T0 + 600 + 30
    assert b.tick() == []                                           # the hub has a minute and a half to say it rang
    clock.t = T0 + 600 + 120
    assert b.tick() == ["miss:1@600"] and "WAKE UP" in sent[-1] and "/alarms" in sent[-1]
    assert b.tick() == []                                           # once
    reborn = Board(store=b.store, notify=lambda t: sent.append(t), clock=clock, log=lambda m: None)
    assert reborn.view["alarms"][0]["key"] == "1@600" and reborn.tick() == []    # a redeployed portal knows the schedule, and what it told


def test_an_alarm_the_hub_rang_is_not_reported_missing():
    clock, sent = Clock(), []
    b = board(clock, sent)
    b.report(view(T0 + 60, key="1@60"))
    clock.t = T0 + 70
    b.report(view(T0 + 86400 + 60, key="1@86460", fired=[{"key": "1@60", "ended": None}], ringing={"key": "1@60", "hhmm": "06:30"}))
    clock.t = T0 + 200
    b.report(view(T0 + 86400 + 60, key="1@86460", fired=[{"key": "1@60", "ended": "stopped", "by": "phone"}]))
    assert b.tick() == [] and sent == []


def test_ringing_when_the_hub_went_quiet_and_silent_the_evening_before():
    clock, sent = Clock(), []
    b = board(clock, sent)
    b.report(view(T0 + 8 * 3600, key="1@eve", ringing={"key": "0@now", "hhmm": "22:00"}))
    clock.t += 130
    assert b.tick() == ["ring:0@now"] and "went quiet" in sent[-1]
    clock.t += 15 * 60
    assert b.tick() == ["eve:1@eve"] and "hasn't reported" in sent[-1]
    assert b.tick() == []


def test_the_routes_are_the_captains_and_the_hubs():
    pytest.importorskip("flask")
    pytest.importorskip("authlib")
    import app as portal
    from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm, _pair, login
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    client = app.test_client()
    assert client.post("/home/alarms", json={}).status_code == 401
    H = {"Authorization": f"Bearer {_pair(client)}"}
    assert client.post("/home/alarms", json=view(T0), headers=H).get_json()["commands"] == []
    login(client, ADMIN)
    assert client.get("/alarms").status_code == 200
    assert client.post("/alarms/do", json={"op": "stop"}).status_code == 400          # no X-Alarms header
    got = client.post("/alarms/do", json={"op": "snooze"}, headers={"X-Alarms": "1"}).get_json()
    assert client.get("/alarms/state").get_json()["pending"] == [got["id"]]
    assert [c["op"] for c in client.post("/home/alarms", json=view(T0), headers=H).get_json()["commands"]] == ["snooze"]
