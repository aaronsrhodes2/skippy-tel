"""The daily routines (daily.py): bedtime on "goodnight", the morning at its time only when he's home, coming home when the car gets back,
skip / later / stop in the pauses, and Bilby's shuffle on the stereo (stereo.py)."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import daily
import housemem
import hub
from hub import FOLLOWUP, IDLE, Room
from timers import Timers
import pytest

HOME = (37.1737, -122.1416)
AWAY = (37.40, -122.00)


@pytest.fixture(autouse=True)
def _voice_as_is(monkeypatch):
    monkeypatch.setattr(hub, "one_stream", lambda mp3, **k: mp3)          # no ffmpeg: the fake voice goes out as it is (and the next file gets the real one back)


def stamp(date, hm):
    return time.mktime(time.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M"))


class Link:
    hub_follows_up = True

    def __init__(self):
        self.spoken, self.room, self.on_speak = [], None, None

    async def heard(self, t): pass
    async def released(self): pass
    async def woke(self, by): pass
    async def show(self, t): pass
    async def voice_started(self): pass
    async def voice_stopped(self): pass
    async def idle_face(self): pass

    async def speak(self, said, voice, follow_up=False):
        self.spoken.append(said)
        if self.on_speak:
            self.on_speak(said)

    announce = speak


class Gate:
    def tts(self, text): return b"mp3"
    def summary(self, text): return text


class Brain:
    def __init__(self):
        self.heard, self.offers, self.actions, self.news = [], [], [], None

    def offer(self, text, note): self.offers.append((text, note))

    def reply(self, heard):
        self.heard.append(heard)
        return "Brain answer."


class House:
    def __init__(self, things):
        self.things = things                                            # {entity_id: (name, state)}
        self.calls = []

    def states(self, fresh=False):
        return [{"entity_id": e, "state": st, "attributes": {"friendly_name": n}} for e, (n, st) in self.things.items()]

    def _service(self, domain, service, data):
        self.calls.append((service, data["entity_id"], data.get("brightness_pct")))
        n, _ = self.things[data["entity_id"]]
        self.things[data["entity_id"]] = (n, "on" if service == "turn_on" else "off")

    def invalidate(self): pass


class Client:
    """Core, faked: says which task it was given, so the tests can see what was asked for and in what order."""

    def __init__(self):
        self.tasks = []
        self.messages = self

    def create(self, model, max_tokens, system, messages):
        task = messages[0]["content"].split("\n\nMaterial:")[0]
        self.tasks.append(task)
        word = "serendipity" if task.startswith("Name one uncommon") else f"<{task.split('.')[0][:30]}>"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=word)])


def make(tmp_path, now, car=None, things=None, env=None, posts=None, home_ll=HOME):
    car_state = {"cars": [car]} if car else {"cars": []}
    posts = posts if posts is not None else []

    def get(url, timeout=15):
        if "/car" in url:
            return car_state
        if "open-meteo" in url:
            return {"current": {"temperature_2m": 55, "apparent_temperature": 52, "weather_code": 2},
                    "daily": {"temperature_2m_max": [66], "temperature_2m_min": [48], "precipitation_probability_max": [10], "weather_code": [2],
                              "wind_speed_10m_max": [9], "uv_index_max": [4], "sunrise": ["2026-10-31T07:30"], "sunset": ["2026-10-31T18:15"]}}
        if "/search" in url:
            return {"results": [{"title": "serendipity: meaning and definition", "content": "serendipity, the faculty of making happy discoveries",
                                 "url": "https://dict.example/serendipity", "engine": "x"},
                                {"title": "Probe finds water", "content": "A probe found water.", "url": "https://news.example/1", "engine": "y"}]}
        raise AssertionError(url)

    def post(url, body, timeout=120):
        posts.append((url, body))
        return {"ok": True}
    house = House(things if things is not None else {"light.bedroom": ("Bedroom", "off"), "light.k1": ("kitchen under 1", "on"),
                                                      "switch.sec": ("security Light", "on"), "switch.fan": ("Desk fan", "on")})
    hlog = housemem.HouseLog(tmp_path / "home", clock=lambda: now[0])
    d = daily.Daily({"SKIPPY_HOME_LATLON": f"{home_ll[0]},{home_ll[1]}", **(env or {})}, house=house, client=Client(), model="m", hlog=hlog,
                    state_dir=tmp_path / "daily", get=get, post=post, clock=lambda: now[0], sleep=lambda s: asyncio.sleep(0.001), log=lambda m: None)
    d.gap_s = 0.05
    d.night_shift = lambda: None                                        # never the real tools/nightly.py from a test (it prunes Docker and may deploy)
    link, brain = Link(), Brain()
    room = Room("office", link, Gate(), brain, Timers(), log=lambda m: None, hlog=hlog)
    room.daily = d
    return d, room, link, brain, house, posts, car_state


def car_at(pos, **k):
    return {"latitude": pos[0], "longitude": pos[1], "battery_level": 60, "charge_limit_soc": 80, "plugged_in": True, "charging_state": "Stopped",
            "rated_battery_range_km": 300.0, "shift_state": "P", **k}


async def finish(d, timeout=5):
    t0 = time.monotonic()
    while d.busy and time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.01)
    assert not d.busy


# ── bedtime ──
def test_goodnight_turns_lights_off_charges_arms_sentry_and_offers_to_talk(tmp_path):
    now = [stamp("2026-10-31", "22:30")]
    d, room, link, brain, house, posts, _ = make(tmp_path, now, car=car_at(HOME))
    d.hlog.event("command", room="office", words="what time is it?", outcome="ok")     # something happened today, for the day's summary

    async def go():
        await room.handle("goodnight")
        await finish(d)
    asyncio.run(go())
    assert brain.heard == []                                            # the routine answered, not the brain
    assert ("turn_off", "light.k1", None) in house.calls and ("turn_off", "switch.sec", None) in house.calls
    assert not any(e == "switch.fan" for _, e, _ in house.calls)        # a plug that isn't a light stays as it is
    assert [u.rsplit("/", 1)[-1] for u, _ in posts] == ["charge", "sentry"] and posts[1][1] == {"action": "arm"}
    assert "Lights off, all 2." in link.spoken[0] and "The car is charging." in link.spoken[0] and "Sentry is armed." in link.spoken[0]
    assert "Next: your day and tomorrow." in link.spoken[0]
    assert "Anything on your mind" in link.spoken[-1] and room.mode == FOLLOWUP
    log = [json.loads(x) for x in (tmp_path / "home" / "log" / "2026-10-31.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e.get("outcome") for e in log if e["kind"] == "routine"] == ["started", "done"]
    assert "routine" not in d.client.tasks and not any("routine" in t for t in d.client.tasks)


def test_a_car_already_charging_is_left_alone_and_sentry_can_be_off(tmp_path):
    now = [stamp("2026-10-31", "22:30")]
    d, room, link, brain, house, posts, _ = make(tmp_path, now, car=car_at(HOME, charging_state="Charging"), env={"SKIPPY_BEDTIME_SENTRY": "off"})

    async def go():
        await room.handle("goodnight")
        await finish(d)
    asyncio.run(go())
    assert posts == [] and "plugged in and charging" in link.spoken[0]


def test_timed_bedtime_asks_first(tmp_path):
    now = [stamp("2026-10-31", "21:00")]
    d, room, link, brain, house, posts, _ = make(tmp_path, now)

    async def go():
        await room.handle("goodnight at 11")
        assert "11 PM" in link.spoken[-1] and not d.busy
        now[0] = stamp("2026-10-31", "23:00")
        await d.tick(room)
        assert "Bedtime routine?" in link.spoken[-1]
        await room.handle("yes")
        await finish(d)
    asyncio.run(go())
    assert "Anything on your mind" in link.spoken[-1]


# ── skip, later, stop ──
def test_skip_drops_the_next_part_and_stop_ends_it(tmp_path):
    now = [stamp("2026-10-31", "22:30")]
    d, room, link, brain, house, posts, _ = make(tmp_path, now, car=car_at(HOME))
    said = []

    def on_speak(text):
        said.append(text)
        if len(said) == 1:
            asyncio.ensure_future(room.handle("skip"))
    link.on_speak = on_speak

    async def go():
        await room.handle("goodnight")
        await finish(d)
    asyncio.run(go())
    assert "Skipping it." in link.spoken
    assert not any(s.startswith("<Sum up") for s in link.spoken)        # the day summary was skipped
    assert "Anything on your mind" in link.spoken[-1]

    d2, room2, link2, *_ = make(tmp_path / "b", now, car=car_at(HOME))
    link2.on_speak = lambda text: asyncio.ensure_future(room2.handle("stop")) if len(link2.spoken) == 1 else None

    async def go2():
        await room2.handle("goodnight")
        await finish(d2)
    asyncio.run(go2())
    assert "Fine. Routine over." in link2.spoken and not any("Anything on your mind" in s for s in link2.spoken)


def test_later_snoozes_ten_minutes(tmp_path):
    now = [stamp("2026-10-31", "22:30")]
    d, room, link, brain, house, posts, _ = make(tmp_path, now, car=car_at(HOME))
    off = [0.0]
    d.mono = lambda: time.monotonic() + off[0]                          # the routine's clock, wound forward past the snooze
    link.on_speak = lambda text: asyncio.ensure_future(room.handle("later")) if len(link.spoken) == 1 else None

    async def go():
        await room.handle("goodnight")
        for _ in range(200):
            await asyncio.sleep(0.005)
        assert d.busy and d.run.snooze_until > time.monotonic() + daily.SNOOZE_S - 5
        off[0] += daily.SNOOZE_S + 1
        await finish(d)
    asyncio.run(go())
    assert "Ten minutes" in " ".join(link.spoken) and "Right. Where was I." in link.spoken and "Anything on your mind" in link.spoken[-1]


# ── the morning ──
def test_morning_runs_once_at_its_time_only_when_home(tmp_path):
    now = [stamp("2026-11-02", "07:05")]
    d, room, link, brain, house, posts, car = make(tmp_path, now, car=car_at(AWAY))

    async def go():
        await d.tick(room)
        assert not d.busy
        now[0] = stamp("2026-11-02", "07:10")
        await d.tick(room)                                              # away (and nothing heard last night): skipped
        assert not d.busy and d.state["last_morning"] == "2026-11-02"
        now[0] = stamp("2026-11-03", "07:10")
        car["cars"][0].update(latitude=HOME[0], longitude=HOME[1])
        d._car_check = -1e9
        await d.tick(room)
        assert d.busy
        await finish(d, 15)
        d.state["last_morning"] = "2026-11-03"
        await d.tick(room)
        assert not d.busy
    asyncio.run(go())
    assert ("turn_on", "light.bedroom", 5) in house.calls and ("turn_on", "light.bedroom", 50) in house.calls
    text = " ".join(link.spoken)
    assert text.startswith("Good morning, Captain.") and "Next: the news." in link.spoken[0]
    for task in ("Give him today's weather", "This morning's news", "One or two sentences on the car", "Tell him one short", "The word of the day"):
        assert task.split(".")[0][:30] in text, task
    tasks = d.client.tasks
    assert any(t.startswith('The word of the day is "serendipity"') for t in tasks)
    assert d.state["said"]["words"] == ["serendipity"]


def test_good_morning_runs_it_by_hand(tmp_path):
    now = [stamp("2026-11-02", "09:00")]
    d, room, link, *_ = make(tmp_path, now, car=car_at(HOME))

    async def go():
        await room.handle("Good morning")
        await finish(d, 15)
    asyncio.run(go())
    assert link.spoken[0].startswith("Good morning, Captain.")


# ── coming home ──
def test_coming_home_lights_now_then_news_music_and_his_day_once_he_is_heard(tmp_path):
    now = [stamp("2026-11-02", "08:00")]
    d, room, link, brain, house, posts, car = make(tmp_path, now, car=car_at(AWAY),
                                                   things={"light.k1": ("kitchen under 1", "off"), "switch.sec": ("security Light", "off")})
    shuffles = []

    class Stereo:
        playing = False

        async def shuffle(self):
            shuffles.append(1)
            return 10
    d.stereo = lambda: Stereo()

    async def go():
        await d.tick(room)
        assert "away_since" in d.state
        now[0] = stamp("2026-11-02", "18:30")                           # ten and a half hours later, after sunset
        car["cars"][0].update(latitude=HOME[0], longitude=HOME[1])
        d._car_check = -1e9
        await d.tick(room)
        assert ("turn_on", "light.k1", None) in house.calls and ("turn_on", "switch.sec", None) in house.calls
        assert d.arrived_at is not None and not d.busy and link.spoken == []
        room._voice_at = time.monotonic() + 1                           # he walks in and says something
        await d.tick(room)
        await finish(d)
    asyncio.run(go())
    assert shuffles == [1]
    assert link.spoken[0].startswith("<He just got home: welcome") and link.spoken[-1].startswith("<He just got home from work")
    assert brain.offers and room.mode == FOLLOWUP


def test_a_short_trip_is_not_coming_home_from_work(tmp_path):
    now = [stamp("2026-11-02", "12:00")]
    d, room, link, brain, house, posts, car = make(tmp_path, now, car=car_at(AWAY))

    async def go():
        await d.tick(room)
        now[0] = stamp("2026-11-02", "13:00")
        car["cars"][0].update(latitude=HOME[0], longitude=HOME[1])
        d._car_check = -1e9
        await d.tick(room)
    asyncio.run(go())
    assert d.arrived_at is None and house.calls == []


def test_home_is_learned_from_where_the_car_sleeps(tmp_path):
    now = [stamp("2026-11-02", "03:00")]
    d, room, *_ = make(tmp_path, now, car=car_at(HOME), env={"SKIPPY_HOME_LATLON": ""})
    d.env.pop("SKIPPY_HOME_LATLON")

    async def go():
        await d.tick(room)
    asyncio.run(go())
    assert d.home() == (round(HOME[0], 3), round(HOME[1], 3))


# ── the stereo ──
def test_stereo_shuffles_the_library_and_moves_on_when_a_track_ends():
    import stereo

    class Play:
        def __init__(self, src):
            self.source, self.finished, self.gain = src, threading.Event(), 1.0

    class Router:
        sinks = {"hdmi": 1}

        def __init__(self):
            self.plays, self.stopped = [], []

        def available(self, k): return True

        def play(self, source, layer, targets, gain):
            p = Play(source)
            self.plays.append((p, layer, targets))
            return p

        def stop(self, p):
            self.stopped.append(p)
            p.finished.set()

    class Media:
        async def catalog(self): return {f"a/{i}.flac": (f"T{i}", "A", "B") for i in range(5)}
        def track_url(self, path, start, towards=""): return "http://127.0.0.1:10800/m/x/track.flac?p=" + path

    class Proc:
        stdout = SimpleNamespace(read=lambda n: b"")
        def kill(self): pass

    async def go():
        r = Router()
        clock = [0.0]
        s = stereo.StereoMusic(r, ["hdmi"], Media(), log=lambda m: None, clock=lambda: clock[0], popen=lambda *a, **k: Proc())
        assert await s.shuffle() == 5
        assert len(r.plays) == 1 and r.plays[0][1] == "media" and r.plays[0][2] == {"hdmi"}
        first = s.box.track()
        clock[0] = 100.0
        r.plays[0][0].finished.set()                                    # the track ended by itself
        for _ in range(100):
            await asyncio.sleep(0.01)
            if len(r.plays) == 2:
                break
        assert len(r.plays) == 2 and s.box.track() != first and s.box.idx == 1
        s.duck(True, 30)
        assert r.plays[1][0].gain == stereo.DUCK
        await s.stop()
        assert not s.playing
    asyncio.run(go())


def test_a_request_file_starts_a_routine(tmp_path):
    now = [stamp("2026-10-31", "22:30")]
    d, room, link, *_ = make(tmp_path, now, car=car_at(HOME))
    (tmp_path / "daily").mkdir(parents=True, exist_ok=True)
    (tmp_path / "daily" / "request.json").write_text('{"routine": "bedtime"}', encoding="utf-8")

    async def go():
        await d.tick(room)
        assert d.busy and not (tmp_path / "daily" / "request.json").exists()
        await finish(d)
    asyncio.run(go())
    assert "Anything on your mind" in link.spoken[-1]


def test_a_routine_moves_to_the_new_room_when_the_box_reconnects(tmp_path):
    now = [stamp("2026-10-31", "22:30")]
    d, room, link, brain, *_ = make(tmp_path, now, car=car_at(HOME))
    new_link = Link()
    new_room = Room("office", new_link, Gate(), brain, Timers(), log=lambda m: None)
    new_room.daily = d

    async def go():
        await room.handle("goodnight")
        room.buf = []                                                   # the old connection died mid-sentence: it never goes quiet
        for _ in range(50):
            await asyncio.sleep(0.01)
        await d.tick(new_room)
        await finish(d)
    asyncio.run(go())
    assert "Anything on your mind" in new_link.spoken[-1]


def test_core_down_means_plain_sourced_words_not_silence(tmp_path):
    now = [stamp("2026-11-02", "07:10")]
    d, room, link, *_ = make(tmp_path, now, car=car_at(HOME))

    class Down:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **k):
            raise ConnectionError("Core is restarting")
    d.client = Down()
    assert asyncio.run(d.weather()).startswith("Today: a high of 66 and a low of 48 degrees, partly cloudy")
    assert "news.example" in asyncio.run(d.morning_news()) or "The headlines." in asyncio.run(d.morning_news())
    assert asyncio.run(d.joke()) is None


def test_an_early_first_event_moves_tomorrows_morning(tmp_path):
    now = [stamp("2026-11-02", "22:00")]
    d, *_ = make(tmp_path, now)

    async def first():
        return stamp("2026-11-03", "07:00")
    d.extras["first_tomorrow"] = first
    said = asyncio.run(d.plan_tomorrow())
    assert d.morning_time("2026-11-03") == "05:30" and "5:30 AM" in said

    async def late():
        return stamp("2026-11-03", "10:00")
    d.extras["first_tomorrow"] = late
    assert asyncio.run(d.plan_tomorrow()) is None and d.morning_time("2026-11-03") == "07:10"


def test_the_night_shift_starts_once_when_the_house_is_quiet_and_the_morning_reads_its_line(tmp_path):
    now = [stamp("2026-11-03", "01:40")]
    d, room, link, *_ = make(tmp_path, now)
    started = []
    d.night_shift = lambda: started.append(1)
    room._turn_at = time.monotonic() - 60                               # he was still talking a minute ago

    async def go():
        await d.tick(room)
        assert started == []
        room._turn_at = time.monotonic() - 3600
        await d.tick(room)
        await d.tick(room)
    asyncio.run(go())
    assert started == [1]
    (tmp_path / "daily" / "overnight.json").write_text(json.dumps({"at": now[0], "line": "Overnight: all clean."}), encoding="utf-8")
    assert asyncio.run(d.overnight()) == "Overnight: all clean."
    now[0] += 2 * 86400
    assert asyncio.run(d.overnight()) is None
