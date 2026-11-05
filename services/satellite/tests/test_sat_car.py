"""The house voice's car (the Captain, 2026-11-04: asked "What is my car's charge level right now?" the house said "I do not have access to your car's systems"; "You are wrong,
you DO have access. Missed tool call."): car_status reads skippy-core's car reader (TeslaMate's last values), READ ONLY, in plain words and units; plain questions about his car
reach it before the model answers; the prompt names the car as out of reach only while the tool is missing."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import car
from brain import Brain
from test_sat_brain import Client, text, tool
from timers import Timers

NOW = 1_800_000_000.0
HOME = "37.1,-122.1"


def iso(seconds_ago: float) -> str:
    return datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parked(**kw) -> dict:
    """What core/car serves for a parked, awake Model 3 (the fields of the real reader, 2026-11-04)."""
    c = {"id": 1, "name": "T3-P0", "state": "online", "since": iso(600), "updated_s": 5, "battery_level": 57, "usable_battery_level": 57, "est_battery_range_km": None,
         "rated_battery_range_km": 330.56, "ideal_battery_range_km": 330.56, "charge_limit_soc": 95, "charger_power": 0, "time_to_full_charge": 0.0, "charging_state": "Disconnected",
         "plugged_in": False, "locked": True, "sentry_mode": False, "doors_open": False, "windows_open": False, "trunk_open": False, "frunk_open": False,
         "inside_temp": 24.5, "outside_temp": 12.0, "is_climate_on": False, "shift_state": "P", "speed": 0, "odometer": 9896.83, "update_available": False,
         "latitude": 37.1, "longitude": -122.1, "geofence": None}
    c.update(kw)
    return c


def make(reply, env=None, tmp_path=None):
    asked = []

    def get(url, timeout=15):
        asked.append(url)
        if isinstance(reply, Exception):
            raise reply
        return reply
    daily_state = (tmp_path / "no-daily-state.json") if tmp_path is not None else None
    return car.Car({"SKIPPY_HOME_LATLON": HOME} if env is None else env, get=get, clock=lambda: NOW, daily_state=daily_state), asked


def reader(car_dict=None, **top):
    return {"cars": [car_dict if car_dict is not None else parked()], "mqtt": True, "signed_in": True, "sign_in": "http://skippy-core:4000", **top}


def test_the_charge_level_is_said_in_percent_and_miles_and_asking_goes_to_the_reader_only():
    c, asked = make(reader())
    said = c.run({"about": "charge"})
    assert said == "T3-P0 is awake. Battery 57 percent, charge limit 95 percent, about 205 miles of rated range. Not plugged in."
    assert asked == ["http://skippy-core:7351/car"]                                  # the tailnet reader, one read: never Tesla, never a command
    assert "205" in said and "330" not in said                                       # the miles come from Mentat's factor, not the kilometres


def test_the_cars_own_estimate_is_the_range_when_it_has_one_and_the_rated_range_says_so_when_it_does_not():
    c, _ = make(reader(parked(est_battery_range_km=400.0)))
    assert "about 249 miles of range." in c.run({"about": "charge"})                  # 400 km x 0.621371
    c, _ = make(reader(parked(est_battery_range_km=None, rated_battery_range_km=None, ideal_battery_range_km=None)))
    assert c.run({"about": "charge"}).endswith("Battery 57 percent, charge limit 95 percent. Not plugged in.")


def test_charging_says_the_power_and_the_time_to_the_limit_and_a_finished_charge_says_so():
    c, _ = make(reader(parked(state="charging", charging_state="Charging", plugged_in=True, charger_power=11.2, time_to_full_charge=2.25)))
    assert "Charging at 11.2 kilowatts, about 2 hours 15 minutes to the limit." in c.run({"about": "charge"})
    c, _ = make(reader(parked(charging_state="Complete", plugged_in=True)))
    assert "Plugged in, charging is complete." in c.run({"about": "charge"})
    c, _ = make(reader(parked(charging_state="Stopped", plugged_in=True)))
    assert "Plugged in, charging is stopped." in c.run({"about": "charge"})
    c, _ = make(reader(parked(charging_state="Charging", state="charging", plugged_in=True, charger_power=48, time_to_full_charge=0.4)))
    assert "Charging at 48 kilowatts, about 24 minutes to the limit." in c.run({"about": "charge"})


def test_home_or_away_comes_from_where_the_house_has_learned_home_is(tmp_path):
    c, _ = make(reader())
    assert "Parked at home." in c.run({"about": "where"})
    c, _ = make(reader(parked(latitude=37.145, longitude=-122.1, geofence="Work")))     # 5 km north
    assert "Away from home, at Work." in c.run({"about": "where"})
    c, _ = make(reader(parked(latitude=37.145, longitude=-122.1)))
    assert c.run({"about": "where"}).endswith("Away from home.")
    c, _ = make(reader(parked(latitude=None, longitude=None, geofence="Home")))        # TeslaMate's own named place says home
    assert "Parked at home." in c.run({"about": "where"})
    c, _ = make(reader(parked(latitude=None, longitude=None)))
    assert "I can't tell where it is." in c.run({"about": "where"})
    c, _ = make(reader(), env={}, tmp_path=tmp_path)                                   # home never learned: it does not guess
    assert "I do not know where home is yet" in c.run({"about": "where"})


def test_driving_says_the_speed_in_miles_per_hour():
    c, _ = make(reader(parked(state="driving", shift_state="D", speed=72.0, latitude=37.2)))
    assert "Driving, at 45 miles per hour." in c.run({"about": "where"})


def test_a_sleeping_or_offline_car_says_its_values_are_from_when_it_went_quiet_and_how_long_ago():
    c, _ = make(reader(parked(state="asleep", since=iso(5400))))
    assert c.run({"about": "charge"}) == ("T3-P0 is asleep. Battery 57 percent, charge limit 95 percent, about 205 miles of rated range. Not plugged in. "      # the readings first
                                          "These are its last readings, from 1 hour 30 minutes ago, when it went quiet; asking never wakes it. Give the readings and say how old they are.")
    c, _ = make(reader(parked(state="offline", since=iso(3 * 86400))))
    assert "T3-P0 is asleep or out of signal." in c.run({}) and "from 3 days ago, when it went quiet" in c.run({})
    c, _ = make(reader(parked(state="asleep", since="not a time")))
    assert "from when it went quiet;" in c.run({"about": "locks"})
    c, _ = make(reader(parked(updated_s=2400)))                                         # awake, but the reader has heard nothing for 40 minutes
    assert "The last word from the car was 40 minutes ago." in c.run({"about": "charge"})
    c, _ = make(reader())
    assert "last word" not in c.run({"about": "charge"})


def test_locks_what_is_open_sentry_and_the_cabin_temperature():
    c, _ = make(reader(parked(locked=False, windows_open=True, trunk_open=True, sentry_mode=True)))
    assert c.run({"about": "locks"}).endswith("Unlocked. Open: a window, the trunk. Sentry mode is on.")
    c, _ = make(reader())
    assert c.run({"about": "locks"}).endswith("Locked. Nothing is open. Sentry mode is off.")
    assert c.run({"about": "climate"}).endswith("Inside 76 F, outside 54 F.")             # 24.5 C and 12 C by weather.c_to_f (Mentat's factors)
    c, _ = make(reader(parked(is_climate_on=True, outside_temp=None)))
    assert c.run({"about": "climate"}).endswith("Inside 76 F. The climate control is on.")


def test_all_says_everything_once_and_an_unknown_topic_is_all():
    c, _ = make(reader(parked(update_available=True)))
    said = c.run({})
    for part in ("Battery 57 percent", "Not plugged in.", "Parked at home.", "Locked.", "Inside 76 F", "Odometer 6150 miles.", "A software update is waiting."):
        assert part in said, part
    assert c.run({"about": "nonsense"}) == said and c.run(None) == said


def test_signed_out_no_car_and_a_dead_reader_each_say_so_and_never_guess():
    c, _ = make(reader(signed_in=False))
    said = c.run({"about": "charge"})
    assert said.startswith("Tesla signed TeslaMate out") and "OLD values" in said and "Battery 57 percent" in said and "http" not in said     # no sign-in link, no sign-in attempt
    c, _ = make({"cars": [], "mqtt": True, "signed_in": True})
    assert "no car yet" in c.run({})
    logged = []
    c, _ = make(OSError("tailnet down"))
    c.log = logged.append
    said = c.run({})
    assert "not answering" in said and "do not guess" in said and "Battery" not in said and logged and "OSError" in logged[0]


def test_what_it_says_is_plain_words_no_markup_no_links_and_no_coordinates():
    c, _ = make(reader(parked(latitude=37.12345, longitude=-122.54321, geofence="Costco")))
    said = c.run({})
    assert not any(ch in said for ch in "*#`_[]<>{}|") and "http" not in said and "://" not in said
    assert "37.12" not in said and "122.54" not in said and "Away from home, at Costco." in said


def test_plain_questions_about_his_car_are_routed_to_the_tool():
    r = car.pre_route
    assert r("What is my car's charge level right now?") == {"about": "charge"}
    assert r("Skippy, how much charge does my Tesla have?") == {"about": "charge"}
    assert r("is the Tesla plugged in") == {"about": "charge"} and r("can you check my car's battery") == {"about": "charge"}
    assert r("what's the range on the car") == {"about": "charge"} and r("is the car charging") == {"about": "charge"}
    assert r("where is the car") == {"about": "where"} and r("is the car home") == {"about": "where"} and r("is my car in the garage") == {"about": "where"}
    assert r("is my car locked") == {"about": "locks"} and r("did I leave a window open on the car") == {"about": "locks"}
    assert r("how warm is it in the car") == {"about": "climate"}
    assert r("how's the car") == {"about": "all"} and r("how's T3-P0 doing") == {"about": "all"} and r("is the car home and charging") == {"about": "all"}


def test_commands_other_cars_and_chatter_are_left_to_the_model():
    r = car.pre_route
    for other in ("lock the car", "Skippy, unlock my car", "start charging my car", "stop charging the car", "can you open the trunk of the car", "turn on the climate in my car",
                  "set the charge limit on the car to 80", "precondition the Tesla", "plug in the car",
                  "what is the range of the Tesla Model Y", "is Tesla stock up today", "play some music in the car", "tell me a joke about my car", "my car is full of junk",
                  "what's the weather", "how warm is it in here", "turn on the kitchen lights", "what is the charge on my phone"):
        assert r(other) is None, other


def test_a_question_about_the_car_runs_the_tool_before_the_model_answers_and_the_prompt_stops_denying_it():
    c, asked = make(reader())
    seen = []

    class Core:
        class messages:
            @staticmethod
            def create(**kw):
                seen.append(kw)
                return NS(content=[text("Fifty-seven percent, about two hundred and five miles, and it is not plugged in. Plug it in, monkey.")])
    b = Brain(Core, "m", Timers(), persona="P", car=c)
    assert b.reply("What is my car's charge level right now?").startswith("Fifty-seven percent")
    assert b.actions[0]["tool"] == "car_status" and b.actions[0]["args"] == {"about": "charge"} and asked == ["http://skippy-core:7351/car"]
    assert "Battery 57 percent" in seen[0]["messages"][-1]["content"] and "tools" not in seen[0]            # the tool ran first; the model only words the answer
    system = seen[0]["system"]
    assert "car_status" in system and "Out of your reach from here: his car" not in system and "never say you have no access to his car" in system
    assert ("Out of your reach from here: physics calculations (Mentat), his files and code. The Skippy chat on makerportal.live has Mentat, "
            "and the private Skippy there writes code.") in system                                       # only Mentat is still missing here


def test_the_model_can_call_the_car_itself_when_the_words_are_not_the_routed_ones():
    c, _ = make(reader(parked(state="charging", charging_state="Charging", plugged_in=True, charger_power=7, time_to_full_charge=1.0)))
    client = Client([tool("car_status", about="charge")], [text("Seven kilowatts, an hour to go.")])
    b = Brain(client, "m", Timers(), persona="P", car=c)
    assert b.reply("Skippy, anything I should know about the Tesla?") == "Seven kilowatts, an hour to go."       # no topic word: not routed, the model chose the tool
    assert "car_status" in {t["name"] for t in client.calls[0]["tools"]} and "Charging at 7 kilowatts" in str(client.calls[1]["messages"])
    assert b.run_tool("car_status", {"about": "where"}).count("Parked at home") == 1


def test_without_the_tool_the_prompt_says_the_car_is_out_of_reach_and_where_it_is_not():
    client = Client([text("ok")])
    b = Brain(client, "m", Timers(), persona="P")
    b.reply("hello")
    system = client.calls[0]["system"]
    assert "Out of your reach from here: his car, physics calculations (Mentat), his files and code." in system
    assert "The Skippy chat on makerportal.live has the car and Mentat, and the private Skippy there writes code." in system
    assert "car_status" not in {t["name"] for t in client.calls[0]["tools"]}
    assert b.run_tool("car_status", {}) == "I have no such tool."
