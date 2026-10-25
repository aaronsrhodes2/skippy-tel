"""The house's power (power.py): one DM per change from either UPS (out, low, back), nothing on a quiet start, and the two doors that
feed it: skippy-pc's starter status and skippy-core's POST /home/ups (home token only)."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import power as power_mod  # noqa: E402
from test_app import SETTINGS, FakeSkippy, FakeVm, _pair  # noqa: E402


def test_one_message_per_change_never_a_stream():
    told = []
    p = power_mod.Power(notify=told.append)
    assert p.observe("core", False, 100, 600) is None                     # a portal's first reading, all fine: not news
    assert "Power is out at home: skippy-core" in p.observe("core", True, 98, 40)
    assert p.observe("core", True, 90, 35) is None                        # still on battery: said already
    assert "battery is low" in p.observe("core", True, 20, 4, low=True)
    assert p.observe("core", True, 18, 3, low=True) is None
    assert "Power is back" in p.observe("core", False, 25, 60)
    assert len(told) == 3 and p.observe("nas", True) is None and p.observe("pc", None) is None


def test_an_outage_seen_first_after_a_portal_start_is_still_news():
    told = []
    assert "skippy-pc is on its UPS" in power_mod.Power(notify=told.append).observe("pc", True, 100, 66) and told


@pytest.fixture
def env(monkeypatch):
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    told = []
    monkeypatch.setattr(app.extensions["power"], "notify", told.append)
    client = app.test_client()
    return client, {"Authorization": f"Bearer {_pair(client)}"}, told


def test_skippy_core_reports_through_home_ups_with_the_home_token(env):
    client, H, told = env
    assert client.post("/home/ups", json={"status": "OB"}).status_code == 401
    client.post("/home/ups", json={"status": "OL", "charge": 100, "runtime_s": 36000}, headers=H)
    assert client.post("/home/ups", json={"status": "OB DISCHRG", "charge": 97, "runtime_s": 2400}, headers=H).get_json()["told"]
    client.post("/home/ups", json={"status": "OB LB", "charge": 15, "runtime_s": 240}, headers=H)
    client.post("/home/ups", json={"status": "OL CHRG", "charge": 16}, headers=H)
    assert [t.split(":")[0].split(" (")[0] for t in told] == ["Power is out at home", "skippy-core's UPS battery is low", "Power is back at home"]
    assert "~40 min left" in told[0]
    assert client.post("/home/ups", data="x", headers=H).status_code == 400


def test_skippy_pcs_ups_rides_in_the_starters_status(env):
    client, H, told = env
    send = lambda on, charge: client.post("/home/starter", json={"status": {"docker": True, "ups_on_battery": on, "ups_charge": charge,  # noqa: E731
                                                                            "ups_runtime_min": 60}}, headers=H)
    send(False, 100)
    send(True, 99)
    send(True, 98)
    send(False, 97)
    assert told == ["Power is out at home: skippy-pc is on its UPS (battery 99%, ~60 min left).",
                    "Power is back at home (skippy-pc's UPS is on mains again)."]
