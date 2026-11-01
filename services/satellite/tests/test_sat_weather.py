"""The weather tool: the NWS forecast and alerts, NOAA's tides and nearest wave buoy, Open-Meteo's 'now'; Mentat's unit factors; a source down
never silences the others."""
import json

import weather
from brain import Brain
from timers import Timers

HOME = (37.10, -122.10)
BUOY_TXT = ("#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
            "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft\n"
            "2026 10 31 22 50 290  5.0  6.0   1.5  14.0   9.0 290 1015.0  14.0  15.0  10.0   MM   MM    MM\n")
STATIONS = ('<stations><station id="harbor1" lat="37.5" lon="-122.2" name="A harbor" met="y" />'
            '<station id="46042" lat="36.8" lon="-122.4" name="MONTEREY" met="y" currents="n" /></stations>')


def fake(urls, down=()):
    def get(url, raw=False):
        urls.append(url)
        if any(d in url for d in down):
            raise OSError("down")
        if "/points/" in url:
            return {"properties": {"forecast": "https://api.weather.gov/gridpoints/MTR/1,2/forecast"}}
        if "/forecast" in url and "weather.gov" in url:
            return {"properties": {"periods": [
                {"name": "This Afternoon", "isDaytime": True, "detailedForecast": "Sunny. High near 62."},
                {"name": "Tonight", "isDaytime": False, "detailedForecast": "Patchy fog. Low around 44."},
                {"name": "Sunday", "isDaytime": True, "detailedForecast": "Areas of fog, then sunny."},
                {"name": "Sunday Night", "isDaytime": False, "detailedForecast": "Mostly clear."}]}}
        if "/alerts/active" in url:
            return {"features": [{"properties": {"event": "High Surf Advisory", "headline": "High surf until Sunday"}}]}
        if "open-meteo" in url:
            return {"current": {"temperature_2m": 61.6, "apparent_temperature": 58.2, "weather_code": 0, "wind_speed_10m": 6}}
        if "mdapi" in url:
            return {"stations": [{"id": "far", "name": "Far away", "lat": "40.0", "lng": "-124.0"},
                                 {"id": "9413745", "name": "Santa Cruz, Monterey Bay", "lat": "36.96", "lng": "-122.02"}]}
        if "datagetter" in url:
            return {"predictions": [{"t": "2026-10-31 04:11", "v": "3.966", "type": "H"}, {"t": "2026-10-31 21:52", "v": "-0.532", "type": "L"}]}
        if "activestations" in url:
            return STATIONS
        if "realtime2/HARBOR1" in url:
            return BUOY_TXT.replace("  1.5  14.0", "    MM    MM")
        if "realtime2/46042" in url:
            return BUOY_TXT
        raise AssertionError(url)
    return get


def make(tmp_path, down=()):
    urls = []
    w = weather.Weather({"SKIPPY_HOME_LATLON": f"{HOME[0]},{HOME[1]}"}, get=fake(urls, down), state=tmp_path / "weather.json")
    return w, urls


def test_today_with_the_coast(tmp_path):
    w, urls = make(tmp_path)
    out = w.run({"when": "today", "coast": True})
    lines = out.splitlines()
    assert lines[0].startswith("Right now at home: 62 F, feels like 58 F, clear")
    assert lines[1] == "ALERT: High Surf Advisory: High surf until Sunday"            # an alert comes before the forecast
    assert "This Afternoon: Sunny" in out and "Tonight: Patchy fog" in out
    assert "Tides today at Santa Cruz, Monterey Bay (NOAA): high 4.0 ft at 4:11 AM, low -0.5 ft at 9:52 PM." in out
    assert "MONTEREY: waves 5 ft every 14 seconds, sea 59 F, wind 11 mph." in out            # 1.5 m, 15 C, 5 m/s through Mentat's factors
    saved = json.loads((tmp_path / "weather.json").read_text())
    assert saved["tide"]["id"] == "9413745" and saved["buoy"]["id"] == "46042"           # the harbor sensor has no waves: skipped


def test_which_way_the_tide_is_going(tmp_path):
    import time
    w, _ = make(tmp_path)
    w.clock = lambda: time.mktime(time.strptime("2026-10-31 16:50", "%Y-%m-%d %H:%M"))
    assert "Right now the tide is falling, toward the low at 9:52 PM." in w.run({"coast": True})


def test_tomorrow_and_tonight_pick_their_periods(tmp_path):
    w, _ = make(tmp_path)
    assert "Sunday: Areas of fog" in w.run({"when": "tomorrow"}) and "This Afternoon" not in w.run({"when": "tomorrow"})
    tonight = w.run({"when": "tonight"})
    assert "Tonight: Patchy fog" in tonight and "Right now" not in tonight


def test_cached_and_stations_found_once(tmp_path):
    w, urls = make(tmp_path)
    w.run({"coast": True})
    n = len(urls)
    w.run({"coast": True})
    assert len(urls) == n                                                                # all from the cache
    w2 = weather.Weather({"SKIPPY_HOME_LATLON": f"{HOME[0]},{HOME[1]}"}, get=fake(urls), state=tmp_path / "weather.json")
    w2.run({"coast": True})
    assert not any("mdapi" in u or "activestations" in u for u in urls[n:])              # the stations come from state/weather.json


def test_one_source_down_never_silences_the_rest(tmp_path):
    w, _ = make(tmp_path, down=("weather.gov",))
    out = w.run({"coast": True})
    assert "Right now at home" in out and "Tides today" in out and "Not answering: the National Weather Service" in out
    w, _ = make(tmp_path, down=("weather.gov", "open-meteo", "noaa.gov"))
    assert "not answering right now" in w.run({"coast": True})


def test_no_home_says_so(tmp_path):
    w = weather.Weather({}, get=fake([]), state=tmp_path / "w.json", daily_state=tmp_path / "none.json")
    assert "don't know where home is" in w.run({})


def test_a_weather_question_goes_straight_to_the_tool(tmp_path):
    w, _ = make(tmp_path)
    sent = []

    class B:
        def __init__(self, **k): self.__dict__.update(k)

    class Client:
        class messages:
            @staticmethod
            def create(**k):
                sent.append(k)
                return B(content=[B(type="text", text="Grab an umbrella, monkey.")])
    brain = Brain(Client, "m", Timers(), persona="p", weather=w)
    assert brain.reply("do I need an umbrella tomorrow?") == "Grab an umbrella, monkey."
    assert brain.actions[0]["tool"] == "weather" and brain.actions[0]["args"] == {"when": "tomorrow"}
    assert "Sunday: Areas of fog" in sent[0]["messages"][-1]["content"] and "tools" not in sent[0] and "TAKEAWAY" in sent[0]["system"]


def test_routing_words():
    r = weather.pre_route
    assert r("what's the weather like?") == {"when": "today"}
    assert r("how's the surf today") == {"when": "today", "coast": True}
    assert r("when is low tide tomorrow") == {"when": "tomorrow", "coast": True}
    assert r("is it going to rain tonight?") == {"when": "tonight"}
    assert r("forecast for the weekend") == {"when": "week"}
    for other in ("play Purple Rain", "news about the storm", "turn on the lights", "tell me a joke about the weather"):
        assert r(other) is None, other
