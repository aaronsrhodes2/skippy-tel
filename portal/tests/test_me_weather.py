"""The Weather and traffic pane on /me and the Discord delay alert: shown from what skippy-pc's reporter sends, one DM per episode, retirable in the Graveyard."""
import pytest

import notify
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm, login

import app as portal

LOCAL = {
    "weather": {"home": {"ok": True, "place": "Boulder Creek, CA", "temp_f": 55, "feels_f": 51, "text": "clear", "wind_mph": 7, "hi_f": 70, "lo_f": 48, "rain_chance": 20,
                         "next_hours": [{"hour": "9 AM", "temp_f": 58, "rain": 10, "text": "clear"}], "alerts": [{"event": "Wind Advisory", "headline": "Wind Advisory until 8 PM"}],
                         "age_s": 30, "stale": False},
                "work": {"ok": True, "place": "Sunnyvale, CA", "temp_f": 60, "feels_f": 58, "text": "clear", "wind_mph": 1, "hi_f": 71, "lo_f": 46, "rain_chance": 0, "next_hours": [],
                         "alerts": [], "age_s": 30, "stale": False}},
    "commute": {"ok": True, "provider": "", "legs": [], "note": "Live drive times need a TomTom key.", "incidents_ok": True, "alert": "Possible delays: crash at Sr17 N / Lark Ave Ofr.",
                "incidents": [{"time": "Oct 11 2026  6:50PM", "kind": "crash", "severity": "major", "where": "Sr17 N / Lark Ave Ofr", "detail": "NB 17 JNO LARK", "area": "San Jose"},
                              {"time": "Oct 11 2026  6:40PM", "kind": "hazard", "severity": "minor", "where": "Sr9 / Shadeland Rd", "detail": "", "area": "Santa Cruz"}], "age_s": 20},
}


@pytest.fixture
def env():
    app = portal.create_app({**SETTINGS, "CHAT_ENABLED": True}, vm=FakeVm(), skippy=FakeSkippy())
    return app, app.test_client()


def report(app, local):
    app.extensions["home"].accept({"capabilities": {}, "local": local})


def test_the_pane_shows_both_towns_the_alert_the_incidents_and_what_is_missing(env):
    app, client = env
    login(client, ADMIN)
    report(app, LOCAL)
    page = client.get("/me").data.decode()
    assert 'id="weather"' in page and 'data-pane="weather"' in page and 'data-tel="pane:weather"' in page
    for text in ("Boulder Creek, CA", "Sunnyvale, CA", "55&deg;F", "Wind Advisory until 8 PM", "Possible delays: crash at Sr17 N / Lark Ave Ofr.", "Sr9 / Shadeland Rd", "Live drive times need a TomTom key."):
        assert text in page, text
    assert 'data-s="warn"' in page.split('data-pane="weather"')[0].rsplit("<div", 1)[1]                  # the tile's lamp is amber while there is an alert


def test_with_nothing_reported_yet_the_pane_says_so_and_with_a_clear_road_the_lamp_is_quiet(env):
    app, client = env
    login(client, ADMIN)
    assert "Nothing yet" in client.get("/me").data.decode()
    report(app, {**LOCAL, "commute": {**LOCAL["commute"], "alert": "", "incidents": []}})
    page = client.get("/me").data.decode()
    assert "No incidents reported on SR 9" in page and "Possible delays" not in page


def test_a_delay_is_one_discord_dm_per_episode_not_one_per_report():
    sent = []
    clock = [1000.0]
    n = notify.Notifier("tok", FakeVm(), transport=lambda *a, **k: (200, b"{}"), clock=lambda: clock[0], background=False)
    n._send = lambda text: sent.append(text) or True
    n.enabled = lambda owner: True
    assert n.traffic_alert(ADMIN, "") is False and sent == []
    assert n.traffic_alert(ADMIN, "Possible delays: crash at Sr17.") is True and sent == ["Traffic: Possible delays: crash at Sr17."]
    clock[0] += 120
    assert n.traffic_alert(ADMIN, "Possible delays: crash at Sr17.") is False                          # the same story, two minutes later
    assert n.traffic_alert(ADMIN, "Possible delays: hazard at Sr9.") is False                           # a changed story inside half an hour: not a flood
    clock[0] += 1800
    assert n.traffic_alert(ADMIN, "Possible delays: hazard at Sr9.") is True and len(sent) == 2
    n.traffic_alert(ADMIN, "")                                                                           # the road clears
    clock[0] += 60
    assert n.traffic_alert(ADMIN, "To work: 60 min, 20 min slower than usual.") is True and len(sent) == 3          # a new episode after it cleared
    n.enabled = lambda owner: False                                                                      # his switch is off: never
    clock[0] += 99999
    assert n.traffic_alert(ADMIN, "A brand new delay.") is False


def test_the_report_route_sends_the_alert_to_the_notifier_and_never_fails_the_report(env, monkeypatch):
    app, client = env
    got = []
    monkeypatch.setattr(app.extensions["notifier"], "traffic_alert", lambda owner, text: got.append((owner, text)))
    token = app.extensions["home"].pair()
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/home/report", json={"capabilities": {}, "local": LOCAL}, headers=h).status_code == 200
    assert got == [(ADMIN, "Possible delays: crash at Sr17 N / Lark Ave Ofr.")]
    assert client.post("/home/report", json={"capabilities": {}, "local": "junk"}, headers=h).status_code == 200
    assert got[-1][1] == ""
    monkeypatch.setattr(app.extensions["notifier"], "traffic_alert", lambda *a: (_ for _ in ()).throw(RuntimeError("discord down")))
    assert client.post("/home/report", json={"capabilities": {}, "local": LOCAL}, headers=h).status_code == 200


def test_the_graveyard_can_retire_the_pane_when_nobody_opens_it(env):
    app, client = env
    login(client, ADMIN)
    assert 'id="weather"' in client.get("/me").data.decode()
    client.post("/admin/modules", data={"id": "me-weather", "action": "retire"})
    assert 'id="weather"' not in client.get("/me").data.decode()
    assert "Dashboard: Weather and traffic" in client.get("/admin").data.decode()
