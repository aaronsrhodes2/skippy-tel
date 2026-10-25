"""The starter link: the portal asks skippy-pc's always-on manager (tools/skippy_starter.py) to start, stop, heal one thing or put everything
back; only what is in its lists can be asked, and a PC that is not there says so instead of pretending."""
import threading
import time

import pytest

import starter as starter_mod
from starter import Starter
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login

import app as portal


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_the_state_machine_queues_redelivers_and_records_results():
    clock = Clock()
    s = Starter(clock=clock)
    assert s.online() is False and s.view()["age_s"] is None
    cid = s.request("start", ADMIN)
    assert s.request("start", ADMIN) == cid                                          # the same ask is not queued twice
    assert s.poll({"status": {"docker": True, "containers_up": 3, "containers_total": 10, "bridge": False, "busy": False}}) == [{"id": cid, "op": "start"}]
    assert s.online() and s.view()["status"]["containers_up"] == 3 and s.view()["waiting"][0]["op"] == "start"
    assert s.poll({}) == []                                                          # handed over: not again at once
    clock.t += starter_mod.REDELIVER_S
    assert s.poll({}) == [{"id": cid, "op": "start"}]                                # lost on the way: handed over again
    s.poll({"results": [{"id": cid, "ok": True, "summary": "Docker up; 10 of 10 containers; bridge answering."}]})
    assert s.view()["waiting"] == [] and s.view()["recent"][0] == {"op": "start", "ok": True, "summary": "Docker up; 10 of 10 containers; bridge answering.", "age_s": 0, "by": ADMIN}
    assert s.wait(cid, 0)["ok"] is True
    clock.t += starter_mod.ONLINE_S + 1
    assert s.online() is False                                                       # silent for too long: not there


def test_only_its_operations_can_be_queued_and_the_queue_is_small():
    s = Starter()
    for bad in ("format", "", "START", "start; rm", None):
        with pytest.raises(ValueError):
            s.request(bad, ADMIN)
    for op in ("start", "stop", "restart"):
        s.request(op, ADMIN)
    assert sorted(w["op"] for w in s.view()["waiting"]) == ["restart", "start", "stop"] and len(s.poll({})) == 3
    with pytest.raises(ValueError):
        s.request("heal", ADMIN, arg="voice-gate")                                  # the queue is small: three at a time


def test_what_the_starter_reports_is_cleaned_and_a_result_for_nothing_we_asked_is_ignored():
    s = Starter()
    s.poll({"status": {"docker": "yes", "containers_up": 99999, "containers_total": True, "bridge": True, "working": "x" * 500, "evil": "<script>"}, "results": [
        {"id": "nothing", "ok": True, "summary": "x"}, "junk", {"id": ["a"]}]})
    st = s.view()["status"]
    assert st == {"bridge": True, "working": "x" * 60} and s.view()["recent"] == []


@pytest.fixture
def world():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    return app, app.test_client(), app.extensions["starter"]


def token(app):
    return app.extensions["home"].pair()


def test_the_machine_route_wants_the_home_token_and_a_json_object(world):
    app, client, starter = world
    assert client.post("/home/starter", json={}).status_code == 401
    assert client.post("/home/starter", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    t = token(app)
    h = {"Authorization": f"Bearer {t}"}
    assert client.post("/home/starter", data="nope", headers=h).status_code == 400
    assert client.post("/home/starter", data="x" * 30_000, headers={**h, "Content-Type": "application/json"}).status_code == 413
    got = client.post("/home/starter", json={"status": {"docker": True}}, headers=h)
    assert got.status_code == 200 and got.get_json() == {"commands": []} and starter.online()


def test_a_stop_carries_how_it_comes_back_and_only_the_three_ways_are_allowed():
    s = Starter()
    cid = s.request("stop", ADMIN)                                                  # no choice: the default is tomorrow at 7 AM
    assert s.poll({}) == [{"id": cid, "op": "stop", "resume": "7am"}]
    s2 = Starter()
    assert s2.poll({}) == [] and s2.request("stop", ADMIN, "manual") and s2.poll({})[0]["resume"] == "manual"
    for bad in ("tomorrow", "0", "7AM", "rm -rf"):
        with pytest.raises(ValueError):
            Starter().request("stop", ADMIN, bad)
    s3 = Starter()
    cid3 = s3.request("start", ADMIN, "7am")                                         # a start has nothing to resume
    assert s3.poll({}) == [{"id": cid3, "op": "start"}]


def test_heal_names_one_thing_from_its_list_and_repair_lists_what_is_failing():
    s = Starter()
    cid = s.request("heal", "doctor", arg="voice-gate")
    assert s.request("heal", "doctor", arg="voice-gate") == cid                       # the same heal is not queued twice
    other = s.request("heal", "doctor", arg="bridge")
    assert other != cid
    for bad in ("", "voice-gate; rm -rf /", "nginx", "VOICE-GATE"):
        with pytest.raises(ValueError):
            Starter().request("heal", "doctor", arg=bad)
    s2 = Starter()
    rid = s2.request("repair", ADMIN, arg="flask-api,whisper")
    assert s2.poll({}) == [{"id": rid, "op": "repair", "arg": "flask-api,whisper"}]
    with pytest.raises(ValueError):
        Starter().request("repair", ADMIN, arg="whisper,evil")
    assert Starter().request("start", ADMIN, arg="ignored") and Starter().poll({}) == []
    s3 = Starter()
    c3 = s3.request("start", ADMIN, arg="ignored")
    assert s3.poll({}) == [{"id": c3, "op": "start"}]                                 # only heal and repair carry an arg
    s3.poll({"results": [{"id": c3, "ok": True, "summary": "Up."}]})
    assert s3.result(c3)["summary"] == "Up." and s3.result("nope") is None
