"""Weather and the commute for the dashboard: parsed from what the services send, polite (cached), never raising, and honest about what is missing."""
import json
import urllib.error

import localinfo
from localinfo import LocalInfo

NOW = 1_800_000_000 + 8 * 3600          # 2027-01-15 08:00 Pacific (16:00 UTC), a Friday morning: inside the commute hours

CHP = """<?xml version="1.0" ?>
<State><Center ID = "X"><Dispatch ID = "MYCC">
<Log ID = "1"><LogTime>"Oct 11 2026  6:50PM"</LogTime><LogType>"1183-Trfc Collision-Unkn Inj"</LogType><Location>"Sr17 N / Lark Ave Ofr"</Location><LocationDesc>"NB 17 JNO LARK"</LocationDesc><Area>"San Jose"</Area><LATLON>"37250000:121950000"</LATLON></Log>
<Log ID = "2"><LogTime>"Oct 11 2026  6:40PM"</LogTime><LogType>"1125A-Animal Hazard"</LogType><Location>"Sr9 / Shadeland Rd"</Location><LocationDesc>"NB  JNO"</LocationDesc><Area>"Santa Cruz"</Area><LATLON>"37162908:122134640"</LATLON></Log>
<Log ID = "3"><LogTime>"Oct 11 2026  6:30PM"</LogTime><LogType>"1183-Trfc Collision-Unkn Inj"</LogType><Location>"I80 W / Treasure Island Onr"</Location><LocationDesc>""</LocationDesc><Area>"San Francisco"</Area><LATLON>"37808318:122367026"</LATLON></Log>
<Log ID = "4"><LogTime>"Oct 11 2026  6:20PM"</LogTime><LogType>"1182-Trfc Collision-No Inj"</LogType><Location>"Sr99 N / Dillard"</Location><LocationDesc>""</LocationDesc><Area>"Sac"</Area><LATLON>"38342931:121335297"</LATLON></Log>
<Log ID = "5"><LogTime>"Oct 11 2026  6:10PM"</LogTime><LogType>"CZP-Assist with Construction"</LogType><Location>"Sr85 S / Saratoga"</Location><LocationDesc>""</LocationDesc><Area>"San Jose"</Area><LATLON>"37270000:122030000"</LATLON></Log>
<Log ID = "6"><LogTime>"Oct 11 2026  6:00PM"</LogTime><LogType>"1183-Trfc Collision-Unkn Inj"</LogType><Location>"Us101 S / Far Away"</Location><LocationDesc>""</LocationDesc><Area>"LA"</Area><LATLON>"34050000:118240000"</LATLON></Log>
</Dispatch></Center></State>"""


def weather_json(temp=55):
    times = [f"2027-01-15T{h:02d}:00" for h in range(0, 24)] + [f"2027-01-16T{h:02d}:00" for h in range(0, 24)]
    return {"current": {"temperature_2m": temp, "apparent_temperature": temp - 4, "weather_code": 2, "wind_speed_10m": 7.2},
            "hourly": {"time": times, "temperature_2m": [50 + i % 10 for i in range(48)], "precipitation_probability": [10] * 48, "weather_code": [3] * 48},
            "daily": {"temperature_2m_max": [70], "temperature_2m_min": [48], "precipitation_probability_max": [20]}}


class Net:
    def __init__(self, **kw):
        self.calls, self.kw = [], kw

    def __call__(self, url, data=None, headers=None, timeout=10.0, raw=False):
        self.calls.append(url)
        if "geocoding-api" in url:
            name = "Boulder Creek" if "Boulder" in url else "Sunnyvale"
            return {"results": [{"name": name, "admin1": "California", "latitude": 37.1, "longitude": -122.1}]}
        if "api.open-meteo.com" in url:
            if self.kw.get("weather_down"):
                raise urllib.error.URLError("down")
            return weather_json(self.kw.get("temp", 55))
        if "api.weather.gov" in url:
            return {"features": [{"properties": {"event": "Wind Advisory", "headline": "Wind Advisory until 8 PM", "severity": "Moderate"}}]} if self.kw.get("alert") else {"features": []}
        if "media.chp.ca.gov" in url:
            if self.kw.get("chp_down"):
                raise urllib.error.URLError("down")
            return CHP.encode()
        if "api.tomtom.com/search" in url:
            return {"results": [{"position": {"lat": 37.2, "lon": -122.0}}]}
        if "api.tomtom.com/routing" in url:
            live, free = self.kw.get("tomtom", (2400, 2400))
            return {"routes": [{"summary": {"travelTimeInSeconds": live, "noTrafficTravelTimeInSeconds": free, "lengthInMeters": 40000}}]}
        if "routes.googleapis.com" in url:
            if self.kw.get("google") == 403:
                raise urllib.error.HTTPError(url, 403, "denied", {}, None)
            return {"routes": [{"duration": "2700s", "staticDuration": "2400s", "distanceMeters": 40000, "description": "SR 17"}]}
        raise AssertionError(url)


def make(env=None, **kw):
    net = Net(**kw)
    clock = [float(NOW)]
    base = {"HOME_ADDRESS": "311 Teilh Dr, Boulder Creek, CA 95006", "TOMTOM_API_KEY": "", "MAPS_API_KEY": ""}
    li = LocalInfo({**base, **(env or {})}, fetch=net, clock=lambda: clock[0])
    li.net, li.t = net, clock
    return li


def test_the_places_come_from_the_addresses_and_the_weather_is_parsed_in_fahrenheit_and_miles():
    li = make()
    assert li.places == {"home": "Boulder Creek, CA", "work": "Sunnyvale, CA"}
    w = li.weather("home")
    assert w["ok"] and w["place"] == "Boulder Creek, CA" and w["temp_f"] == 55 and w["feels_f"] == 51 and w["text"] == "partly cloudy" and w["wind_mph"] == 7
    assert (w["hi_f"], w["lo_f"], w["rain_chance"]) == (70, 48, 20) and 1 <= len(w["next_hours"]) <= 4 and w["alerts"] == [] and w["stale"] is False
    assert any("temperature_unit=fahrenheit" in c and "wind_speed_unit=mph" in c for c in li.net.calls)
    assert li.weather("work")["place"] == "Sunnyvale, CA"


def test_alerts_from_the_weather_service_are_carried_and_spoken():
    li = make(alert=True)
    assert li.weather("home")["alerts"][0]["event"] == "Wind Advisory" and "Weather alert: Wind Advisory" in li.speak_weather("home")


def test_asking_again_costs_nothing_until_the_cache_runs_out_and_a_failure_keeps_the_last_answer_marked_stale():
    li = make()
    li.weather("home")
    n = len(li.net.calls)
    li.weather("home")
    assert len(li.net.calls) == n                                                    # cached
    li.t[0] += localinfo.WEATHER_TTL + 1
    li.net.kw["weather_down"] = True
    w = li.weather("home")
    assert w["ok"] and w["temp_f"] == 55 and w["age_s"] > localinfo.WEATHER_TTL        # the last good answer, not an error
    li.t[0] += localinfo.STALE_AFTER
    assert li.weather("home")["stale"] is True
    assert make(weather_down=True).weather("home")["ok"] is False                       # never had one: say so, don't raise


def test_chp_incidents_are_only_the_roads_between_home_and_work_in_the_box_worst_first():
    li = make()
    inc = li.incidents()
    assert inc["ok"] and [(i["severity"], i["where"]) for i in inc["items"]] == [("major", "Sr17 N / Lark Ave Ofr"), ("minor", "Sr9 / Shadeland Rd"), ("info", "Sr85 S / Saratoga")]
    assert inc["items"][0]["kind"] == "crash"                                          # not the I-80, Sacramento or Los Angeles ones: wrong road or outside the box
    assert make(chp_down=True).incidents()["ok"] is False


def test_with_no_drive_time_provider_the_incidents_still_show_and_the_note_says_what_is_missing():
    c = make().commute()
    assert c["ok"] and c["provider"] == "" and c["legs"] == [] and "TomTom" in c["note"] and c["incidents_ok"]
    assert c["alert"].startswith("Possible delays: crash at Sr17 N / Lark Ave Ofr")


def test_tomtom_gives_the_live_time_and_a_delay_is_slower_than_free_flow_not_merely_long():
    calm = make({"TOMTOM_API_KEY": "k"}, tomtom=(2400, 2300))
    c = calm.commute()
    assert c["provider"] == "tomtom" and [l["name"] for l in c["legs"]] == ["To work", "To home"] and c["legs"][0]["minutes"] == 40 and c["legs"][0]["delayed"] is False
    assert "no real delay" in calm.speak_commute()
    jam = make({"TOMTOM_API_KEY": "k"}, tomtom=(3600, 2400))                           # 20 minutes over and 50 percent
    cj = jam.commute()
    assert cj["legs"][0]["delay_minutes"] == 20 and cj["legs"][0]["delayed"] and cj["alert"].startswith("To work: 60 min, 20 min slower than usual")
    small = make({"TOMTOM_API_KEY": "k"}, tomtom=(3000, 2700))                          # 5 minutes over: not a delay
    assert small.commute()["legs"][0]["delayed"] is False


def test_google_that_is_not_allowed_is_reported_once_and_not_hammered():
    li = make({"MAPS_API_KEY": "k"}, google=403)
    c = li.commute()
    assert c["provider"] == "" and "google: HTTP 403" in c["note"] and c["incidents_ok"]
    li.t[0] += 10_000
    li.commute()
    assert sum("routes.googleapis.com" in u for u in li.net.calls) == 1               # it said no: it is not asked again this run
    ok = make({"MAPS_API_KEY": "k"}).commute()
    assert ok["provider"] == "google" and ok["legs"][0]["route"] == "SR 17" and ok["legs"][0]["delay_minutes"] == 5


def test_no_home_address_says_so_and_the_commute_hours_set_how_often_it_asks():
    c = make({"HOME_ADDRESS": ""}).commute()
    assert "HOME_ADDRESS" in c["note"] and c["legs"] == []
    li = make()
    assert li.busy_hours() is True and li._ttl() == localinfo.BUSY_TTL                   # Friday 08:00 Pacific
    li.t[0] += 6 * 3600                                                                  # 14:00
    assert li.busy_hours() is False and li._ttl() == localinfo.QUIET_TTL


def test_what_skippy_says_is_short_and_plain():
    li = make({"TOMTOM_API_KEY": "k"}, tomtom=(3600, 2400))
    said = li.speak_commute()
    assert "to work: 60 minutes, 20 minutes slower than usual" in said and "crash at Sr17 N / Lark Ave Ofr" in said
    assert make().speak_weather("home").startswith("In Boulder Creek, CA it is 55 degrees and partly cloudy")


def test_snapshot_carries_both_towns_and_the_commute_and_can_be_switched_off():
    snap = make().snapshot()
    assert set(snap["weather"]) == {"home", "work"} and snap["commute"]["ok"]
    json.dumps(snap)                                                                   # it travels in the report
    assert make({"LOCAL_INFO": "off"}).snapshot() == {"disabled": True}
    assert set(make({"HOME_ADDRESS": ""}).snapshot()["weather"]) == {"work"}
