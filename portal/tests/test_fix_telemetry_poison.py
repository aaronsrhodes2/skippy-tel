"""One crafted event must not be able to stall all site telemetry (portal side).

The defect: JSON like {"p": "/x\\ud800y"} is valid JSON text but decodes to a str holding a lone surrogate, which no
UTF-8 encoder accepts. The portal kept it (the beacon's `p`, the key names of any JSON POST body), the home reporter carried it
to flask-api, flask-api's SQLite bind raised, flask-api answered 500 for the whole batch and the reporter retried the batch
forever: no telemetry stored again until the portal redeployed, while the widget said 'ok'.

Here: the recorder never keeps a lone surrogate (it is replaced, everything else is untouched), and the widget says
'stuck' when home is pulling but events sit unfiled. flask-api's half is services/flask-api/tests/test_fix_telemetry_api_poison.py and
the reporter's is services/home-reporter/tests/test_fix_telemetry_reporter_poison.py.
"""
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import telemetry as tel_mod  # noqa: E402
import telemetry_web  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402

PAL = "pal@example.com"


def encodable(value) -> bool:
    """What flask-api will do with an event: turn it into UTF-8 text (json.dumps(ensure_ascii=False) then a SQLite bind)."""
    json.dumps(value, ensure_ascii=False).encode("utf-8")
    return True


def make(enabled=True):
    vm = FakeVm()
    vm.metadata["portal-friends"] = json.dumps({PAL: {"portals": ["valheim", "skippy"], "added_by": ADMIN}})
    app = portal.create_app({**SETTINGS, "ALLOWED_EMAILS": frozenset({ADMIN}), "TELEMETRY_ENABLED": enabled},
                            vm=vm, skippy=FakeSkippy())
    client = app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"email": PAL, "name": "Pal", "picture": None}
    return app, client, app.extensions["telemetry"]


def stored(tel):
    return tel.since(tel.boot, 0, 500)["events"]


def test_a_lone_surrogate_is_replaced_wherever_it_hides_in_what_is_recorded():
    got = tel_mod.clean({"path": "/x\ud800y", "n\udfffame": "ok", "deep": [{"k\ud800": ["a\udc00b"]}], "message": "hi \ud83d there"})
    assert encodable(got)
    assert got["path"] == "/x�y" and got["n�ame"] == "ok"
    assert got["deep"] == [{"k�": ["a�b"]}] and got["message"] == "hi � there"


def test_real_text_is_untouched_astral_characters_and_all():
    text = "café 日本語 \U0001f600 \U0001f4a9 done"
    assert tel_mod.clean({"message": text, "other": text}) == {"message": text, "other": text}
    parsed = json.loads('"\\ud83d\\ude00"')                      # a valid surrogate PAIR in JSON is one real character
    assert tel_mod.clean({"message": parsed}) == {"message": "\U0001f600"}


def test_the_event_itself_never_carries_one_either():
    tel = tel_mod.Telemetry()
    event = tel.record("page", "/p\ud800", user="a\udfff@x.com", role="fr\ud800", sid="\ud800\ud800", note="x\ud800")
    assert encodable(event) and event["name"] == "/p�" and event["user"] == "a�@x.com"
    assert encodable(tel.since(tel.boot, 0))


def test_a_beacon_with_a_lone_surrogate_in_p_is_recorded_as_something_flask_api_can_store():
    app, client, tel = make()
    res = client.post("/telemetry/tap", json={"e": "tile:games", "p": "/x\ud800y"})
    assert res.status_code == 204
    (tap,) = stored(tel)
    assert tap["detail"] == {"page": "/x�y"} and encodable(stored(tel))


def test_a_json_post_whose_key_names_hold_a_lone_surrogate_is_recorded_as_something_flask_api_can_store():
    app, client, tel = make()
    client.post("/chat/send", json={"\ud800": 1, "n\udfffame": 2})               # refused (no message), and recorded as an action
    (act,) = [e for e in stored(tel) if e["kind"] == "action"]
    assert sorted(act["detail"]["fields"]) == ["n�ame", "�"] and encodable(stored(tel))


def test_no_event_the_portal_can_be_made_to_record_is_unencodable():
    app, client, tel = make()
    poison = "𐏿"
    client.post("/telemetry/tap", json={"e": "tile:games", "p": "/" + poison})
    client.post("/chat/send", json={poison: 1})
    client.post("/chat/send", json={"message": "hello " + poison})
    client.get("/" + "nope")
    assert len(stored(tel)) >= 3 and encodable(stored(tel))


# ── the widget must not say 'ok' while events sit unfiled ─────────────────────────────────

def test_the_widget_says_stuck_when_home_pulls_but_events_stay_unfiled():
    now = [10_000.0]
    tel = tel_mod.Telemetry(clock=lambda: now[0])
    tel.record("page", "/games", user="a@x.com")
    tel.since(tel.boot, 0)                                                  # home pulled it, and never acknowledges (never files it)
    assert telemetry_web.activity_view(tel, None, now=now[0])["state"] == "ok"
    now[0] += 5 * 60
    tel.since(tel.boot, 0)                                                  # still pulling every cycle ...
    assert telemetry_web.activity_view(tel, None, now=now[0])["state"] == "ok"          # ... a few minutes is a slow flask-api, not stuck
    now[0] += 30 * 60
    tel.since(tel.boot, 0)
    view = telemetry_web.activity_view(tel, None, now=now[0])
    assert view["state"] == "stuck" and view["stuck_s"] >= 30 * 60 and view["waiting"] == 1


def test_a_healthy_recorder_is_never_stuck_and_a_silent_home_is_stale_not_stuck():
    now = [10_000.0]
    tel = tel_mod.Telemetry(clock=lambda: now[0])
    tel.record("page", "/games", user="a@x.com")
    tel.since(tel.boot, 0)
    now[0] += 20
    tel.since(tel.boot, 1)                                                  # acknowledged: filed
    now[0] += 3600
    tel.since(tel.boot, 1)
    assert telemetry_web.activity_view(tel, None, now=now[0])["state"] == "ok"
    tel.record("page", "/old", user="a@x.com")
    now[0] += 3600                                                          # home stops pulling altogether
    assert telemetry_web.activity_view(tel, None, now=now[0])["state"] == "stale"


def test_the_dashboard_says_what_stuck_means():
    app, client, tel = make()
    now = [1_000_000.0]
    tel.clock = lambda: now[0]
    tel.record("page", "/games", user="a@x.com")
    tel.since(tel.boot, 0)
    now[0] += 3600
    tel.since(tel.boot, 0)
    with client.session_transaction() as s:
        s["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
    page = client.get("/me").data.decode()
    assert "not filing" in page and "flask-api" in page
