"""The Car app and Skippy's car_status: the Captain's Tesla as the home report carries it (TeslaMate's last values from skippy-core's
car reader). His alone (where the car is, whether it's locked): no grant opens the page, no friend gets the tool. It says plainly
when home is silent, TeslaMate isn't running or nobody signed in to Tesla yet, and shows miles and °F."""
import pytest

pytest.importorskip("authlib")

import car_web  # noqa: E402
import chat_tools  # noqa: E402
from test_app import ADMIN, FRIEND, env, login  # noqa: E402,F401

CAR = {"id": 1, "name": "T3-P0", "state": "charging", "battery_level": 64, "charge_limit_soc": 80, "est_battery_range_km": 321.9,
       "plugged_in": True, "charger_power": 11, "time_to_full_charge": 1.5, "locked": True, "doors_open": False, "windows_open": False,
       "trunk_open": False, "frunk_open": False, "inside_temp": 21.0, "outside_temp": 10.0, "is_climate_on": False, "geofence": "Home",
       "sentry_mode": False, "odometer": 16093.44, "latitude": 47.6, "longitude": -122.3, "updated_s": 9}
SIGNED_IN = {"ready": True, "mqtt": True, "cars": [CAR], "sign_in": "http://skippy-core:4000"}


def report(app, car):
    app.extensions["home"].accept({"capabilities": {"car": car} if car is not None else {}})


def test_view_reads_in_miles_and_fahrenheit():
    v = car_web.view(SIGNED_IN, online=True)
    assert v["state"] == "ok" and v["name"] == "T3-P0"
    u = {x["key"]: x for x in v["units"]}
    assert u["battery"]["text"] == "64% / 80%" and u["range"]["text"] == "200 mi"
    assert u["charge"]["text"] == "11 kW · 1.5 h" and u["charge"]["lamp"] == "on"
    assert u["climate"]["text"] == "70°F / 50°F" and u["locks"]["text"] == "locked" and u["locks"]["lamp"] == "on"
    assert u["where"]["text"] == "Home" and u["state"]["lamp"] == "on"


def test_view_says_what_is_missing():
    assert car_web.view(SIGNED_IN, online=False)["state"] == "no_home"
    assert car_web.view({"ready": False}, online=True)["state"] == "no_teslamate"
    got = car_web.view({"ready": False, "mqtt": True, "cars": [], "sign_in": "http://skippy-core:4000"}, online=True)
    assert got["state"] == "no_car" and got["sign_in"] == "http://skippy-core:4000"


def test_an_open_door_or_a_low_battery_warns():
    car = {**CAR, "state": "asleep", "battery_level": 12, "doors_open": True, "locked": False}
    u = {x["key"]: x for x in car_web.view({**SIGNED_IN, "cars": [car]}, online=True)["units"]}
    assert u["battery"]["lamp"] == "warn" and u["locks"]["lamp"] == "warn" and u["locks"]["text"] == "unlocked · open"
    assert u["state"]["lamp"] == "idle"


def test_summary_is_plain_units_for_skippy():
    s = car_web.summary(SIGNED_IN)
    assert s["estimated_range_mi"] == 200 and s["odometer_mi"] == 10000 and s["inside_temp_f"] == 70
    assert s["battery_level"] == 64 and s["geofence"] == "Home" and "est_battery_range_km" not in s
    assert car_web.summary({"ready": False}) is None


def test_the_page_is_the_captains_alone(env):
    client, _, _ = env
    report(client.application, SIGNED_IN)
    assert client.get("/car").status_code == 302                 # signed out: to the sign-in
    login(client, FRIEND)
    assert client.get("/car").status_code == 403 and client.get("/car/state").status_code == 403
    assert b'href="/car"' not in client.get("/apps").data
    login(client, ADMIN)
    page = client.get("/car")
    assert page.status_code == 200 and b"64% / 80%" in page.data and b"200 mi" in page.data
    state = client.get("/car/state").get_json()
    assert state["state"] == "ok" and client.get("/car/state").headers["Cache-Control"] == "private, no-store"
    assert b'href="/car"' in client.get("/apps").data             # a tile on his launcher


def test_the_page_points_at_the_sign_in_when_no_car(env):
    client, _, _ = env
    report(client.application, {"ready": False, "mqtt": True, "cars": [], "sign_in": "http://skippy-core:4000"})
    login(client, ADMIN)
    page = client.get("/car").data
    assert b'href="http://skippy-core:4000"' in page and b"skippy-core:4000" in page


def test_green_never_has_the_car_not_even_for_the_captain(env):
    """Green is the guest chat (the Captain, 2026-10-30): where his car is stays with purple."""
    client, _, _ = env
    tools_for = client.application.extensions["chat_tools_for"]
    ctx = client.application.app_context()
    ctx.push()
    report(client.application, SIGNED_IN)
    for who in (ADMIN, FRIEND):
        assert "car_status" not in tools_for({"email": who}).available()
    ctx.pop()


def test_the_car_tool_reads_the_report_when_given_one():
    class Car:
        def car(self):
            return SIGNED_IN
    tools = chat_tools.ChatTools(None, is_admin=True, home_car=Car())
    assert "car_status" in tools.available() and any(s["name"] == "car_status" for s in tools.schemas())
    assert '"estimated_range_mi": 200' in tools.run("car_status", {})


def test_a_car_that_stopped_reporting_is_said_plainly():
    class Silent:
        def car(self):
            return None
    tools = chat_tools.ChatTools(None, is_admin=True, home_car=Silent())
    with pytest.raises(chat_tools.ToolError, match="isn't reporting"):
        tools.run("car_status", {})


def test_the_fleet_public_key_is_served_to_everyone_at_teslas_address(env):
    client, _, _ = env
    got = client.get("/.well-known/appspecific/com.tesla.3p.public-key.pem")       # signed out: Tesla reads it with no cookie
    assert got.status_code == 200 and got.mimetype == "application/x-pem-file"
    text = got.get_data(as_text=True)
    assert text.startswith("-----BEGIN PUBLIC KEY-----") and "PRIVATE" not in text   # only ever the public half


def test_the_oauth_callback_never_shows_the_code(env):
    client, _, _ = env
    got = client.get("/car/oauth/callback?code=SECRET-ONE-TIME-CODE&state=abc")
    assert got.status_code == 200 and b"SECRET-ONE-TIME-CODE" not in got.data and b"Tesla said yes" in got.data
    assert got.headers["Cache-Control"] == "no-store" and got.headers["Referrer-Policy"] == "no-referrer"
    assert b"Tesla said no" in client.get("/car/oauth/callback?error=access_denied").data
