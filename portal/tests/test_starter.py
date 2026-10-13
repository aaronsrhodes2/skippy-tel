"""The starter link: the portal can ask skippy-pc's always-on manager to start or stop the stack, only the Captain can, only three things can be asked, and a PC that
is not there says so instead of pretending."""
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


def test_only_the_three_operations_can_be_queued_and_the_queue_is_small():
    s = Starter()
    for bad in ("format", "", "START", "start; rm", None):
        with pytest.raises(ValueError):
            s.request(bad, ADMIN)
    for op in starter_mod.OPS:
        s.request(op, ADMIN)
    assert sorted(w["op"] for w in s.view()["waiting"]) == ["restart", "start", "stop"] and len(s.poll({})) == 3


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


def press(client, action, **form):
    return client.post(f"/admin/recovery/{action}", data={"confirm": "yes", **form}, headers={"Origin": "http://localhost"}, follow_redirects=False)


def test_the_buttons_are_the_captains_and_say_so_when_the_pc_is_not_answering(world):
    app, client, starter = world
    assert press(client, "starter-start").status_code in (302, 401, 403)             # not signed in
    login(client, FRIEND)
    assert press(client, "starter-start").status_code in (302, 401, 403)
    login(client, ADMIN)
    page = client.get("/admin").data.decode()
    assert "Start or stop SkippyTel" in page and "isn&#39;t answering" in page or "isn't answering" in page
    assert "Start SkippyTel" not in page.split("Start or stop SkippyTel")[1].split('id="pc-recovery"')[0]              # no buttons for a PC that is not there
    resp = press(client, "starter-start")
    assert resp.status_code == 302
    flashed = client.get("/admin").data.decode()
    assert "starter on skippy-pc isn" in flashed and "Nothing remote can wake" in flashed
    assert starter.view()["waiting"] == []                                                                       # nothing was queued for a PC that is not there


def test_with_the_starter_answering_a_press_queues_the_op_and_the_answer_comes_back_to_the_card(world):
    app, client, starter = world
    login(client, ADMIN)
    h = {"Authorization": f"Bearer {token(app)}"}
    starter.poll({"status": {"docker": True, "containers_up": 2, "containers_total": 10, "bridge": False, "busy": False}})
    page = client.get("/admin").data.decode()
    assert "Docker up" in page and "2 of 10 containers" in page and "bridge DOWN" in page
    for label in ("Start SkippyTel", "Restart SkippyTel", "Stop SkippyTel"):
        assert label in page

    def pc():                                                                       # the starter on the PC: gets the request, does it, answers
        for _ in range(600):                                                         # up to ~30 s: a loaded machine can be slow to get the press in
            cmds = client.application.test_client().post("/home/starter", json={}, headers=h).get_json()["commands"]
            if cmds:
                time.sleep(0.05)
                client.application.test_client().post("/home/starter", json={"results": [{"id": cmds[0]["id"], "ok": True, "summary": "Docker up; 10 of 10 containers; bridge answering."}]}, headers=h)
                return
            time.sleep(0.05)
    t = threading.Thread(target=pc)
    t.start()
    resp = press(client, "starter-start")
    t.join()
    assert resp.status_code == 302
    assert "10 of 10 containers; bridge answering" in client.get("/admin").data.decode()


def test_a_slow_start_says_it_is_still_working_and_the_press_asks_for_a_confirm_page_without_one(world, monkeypatch):
    app, client, starter = world
    login(client, ADMIN)
    starter.poll({})
    import recovery_web
    monkeypatch.setattr(recovery_web, "STARTER_WAIT_S", 0.05)
    page = client.post("/admin/recovery/starter-stop", data={}, headers={"Origin": "http://localhost"})              # no confirm: the server asks on a page of its own
    assert page.status_code == 200 and b"Stop SkippyTel" in page.data and starter.view()["waiting"] == []
    press(client, "starter-restart")
    assert [w["op"] for w in starter.view()["waiting"]] == ["restart"]
    assert "still working" in client.get("/admin").data.decode()


def test_a_post_from_another_site_is_refused(world):
    app, client, starter = world
    login(client, ADMIN)
    starter.poll({})
    resp = client.post("/admin/recovery/starter-stop", data={"confirm": "yes"}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403 and starter.view()["waiting"] == []


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


def test_the_status_shows_the_planned_resume_and_the_stop_form_offers_the_three_choices(world):
    app, client, starter = world
    login(client, ADMIN)
    starter.poll({"status": {"docker": True, "containers_up": 0, "containers_total": 10, "bridge": False, "resume_at": "2026-10-13T07:00"}})
    page = client.get("/admin").data.decode()
    assert "will start itself at 2026-10-13 07:00" in page and "Start SkippyTel now cancels that" in page
    for option in ('value="7am"', 'value="4h"', 'value="manual"'):
        assert option in page
    press(client, "starter-stop", resume="4h")
    assert starter.view()["waiting"][0]["op"] == "stop" and starter.poll({})[0]["resume"] == "4h"
    resp = press(client, "starter-stop", resume="whenever")
    assert resp.status_code == 302 and "Not sent" in client.get("/admin").data.decode()
