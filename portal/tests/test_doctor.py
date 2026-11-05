"""The doctor (doctor.py): it heals what has failed for long enough, a few times an hour at most, never while the stack is stopped on
purpose or the bridge is mid-run, tells the Captain when it gives up or skippy-pc can't be reached, and its Core test is a real reply."""
import json

import pytest

import doctor as doc
import starter as starter_mod


class Clock:
    def __init__(self):
        self.t = 10_000.0

    def __call__(self):
        return self.t


class Home:
    def __init__(self, age=5):
        self._age = age

    def age(self):
        return self._age


def world(age=5, store=None):
    clock = Clock()
    starter = starter_mod.Starter(clock=clock)
    starter.poll({"status": {"docker": True, "containers_up": 9, "containers_total": 10, "bridge": True, "busy": False}})
    told = []
    d = doc.Doctor(Home(age), starter, notify=lambda text: told.append(text) or True, clock=clock, store=store, log=lambda line: None)
    return d, starter, clock, told


def alive(starter, **status):
    """The starter's call every ~10 s (it is online while it keeps calling)."""
    starter.poll({"status": {"docker": True, "bridge": True, "busy": False, **status}})


def failing(name, why="no answer in time"):
    return {name: {"ok": False, "ms": 5000, "why": why}}


def handed(starter):
    return [(c["op"], c.get("arg")) for c in starter.poll({"status": {"docker": True, "bridge": True}})]


def test_a_failure_is_healed_only_after_it_has_lasted_and_a_recovery_resets_it():
    d, starter, clock, _ = world()
    d.observe_home(failing("voice-gate"))
    assert d.tick() == []                                            # a blip, or a session rebuilding it: left alone
    clock.t += doc.HEAL_AFTER_S - 1
    alive(starter)
    d.observe_home(failing("voice-gate"))
    assert d.tick() == []
    clock.t += 2
    alive(starter)
    d.observe_home(failing("voice-gate"))
    assert d.tick() == ["voice-gate"] and handed(starter) == [("heal", "voice-gate")]
    d.observe_home({"voice-gate": {"ok": True, "ms": 20, "why": ""}})
    clock.t += doc.HEAL_AFTER_S + 1
    alive(starter)
    assert d.tick() == []                                            # it came back: nothing more to do


def test_a_freshly_deployed_portal_believes_the_reporters_own_down_count():
    d, starter, clock, _ = world()                                    # this portal has just started (a deploy)...
    alive(starter)
    d.observe_home({"flask-api": {"ok": False, "ms": 5000, "why": "no answer", "down_s": doc.HEAL_AFTER_S + 30}})
    assert d.tick() == ["flask-api"]                                  # ...but flask-api has been down for over 5 min: healed now
    d2, starter2, _, _ = world()
    alive(starter2)
    d2.observe_home({"flask-api": {"ok": False, "ms": 5000, "why": "no answer", "down_s": 10**9}})
    assert d2.tick() == []                                            # a nonsense count is ignored: the portal's own clock rules


def test_three_tries_an_hour_then_one_dm_and_it_waits():
    d, starter, clock, told = world()
    d.observe_home(failing("whisper"))
    healed = 0
    for _ in range(6):
        clock.t += doc.HEAL_AFTER_S + 1
        alive(starter)
        d.observe_home(failing("whisper"))
        healed += len(d.tick())
        for c in starter.poll({"status": {"docker": True, "bridge": True}}):
            starter.poll({"results": [{"id": c["id"], "ok": False, "summary": "still broken"}]})
    assert healed == doc.MAX_TRIES and len(told) == 1 and "Whisper" in told[0] and "3 tries" in told[0]
    clock.t += 3600
    alive(starter)
    d.observe_home(failing("whisper"))
    assert d.tick() == ["whisper"]                                    # a new hour: it tries again


def test_never_while_stopped_on_purpose_never_the_bridge_mid_run_and_docker_first():
    d, starter, clock, _ = world()
    d.observe_home({**failing("whisper"), **failing("bridge")})
    clock.t += doc.HEAL_AFTER_S + 1
    starter.poll({"status": {"docker": True, "bridge": True, "busy": True}})
    d.observe_home({**failing("whisper"), **failing("bridge")})
    assert d.tick() == ["whisper"]                                   # the bridge is mid-run: never pulled out from under a task
    starter.poll({"status": {"docker": True, "resume_at": "2026-10-24T07:00"}})
    clock.t += 3600
    assert d.tick() == [] and d.view()["maintenance"] is True        # stopped on purpose: nothing is healed
    starter.poll({"status": {"docker": False}})
    assert d.tick() == ["docker"]                                    # Docker down: that first, nothing inside it


def test_a_silent_reporter_is_restarted_and_an_unreachable_pc_is_told_once():
    d, starter, clock, told = world(age=doc.SILENT_HEAL_S + 5)
    assert d.tick() == ["home-reporter"]
    d2, starter2, clock2, told2 = world(age=doc.UNREACHABLE_DM_S + 5)
    clock2.t += starter_mod.ONLINE_S + 1                              # the starter is silent too
    assert d2.tick() == [] and len(told2) == 1 and "skippy-pc" in told2[0]
    d2.tick()
    assert len(told2) == 1


def test_the_core_test_is_a_real_short_reply_with_a_deadline():
    seen = {}

    def post(url, body, headers, timeout):
        seen.update(url=url, body=body, headers=headers, timeout=timeout)
        return 200, {"content": [{"type": "text", "text": "OK"}]}
    ok, why, ms = doc.core_test("https://core.example/", "k-123", "chat", post=post)
    assert ok and seen["url"] == "https://core.example/v1/messages" and seen["body"]["max_tokens"] == 8
    assert seen["timeout"] == doc.CORE_TIMEOUT_S and seen["headers"]["x-api-key"] == "k-123"
    assert doc.core_test("https://core.example", "k", post=lambda *a: (200, {"content": []}))[:2] == (False, "it answered with nothing")
    assert doc.core_test("https://core.example", "k", post=lambda *a: (503, {}))[:2] == (False, "HTTP 503")

    def hang(*a):
        raise TimeoutError("timed out")
    assert doc.core_test("https://core.example", "k", post=hang, get=lambda *a: {})[:2] == (False, "no answer in time")
    assert doc.core_test("", "", post=post)[:2] == (False, "Skippy Core isn't configured")


def test_a_slow_core_with_every_model_loaded_is_busy_not_dead():
    """2026-10-24: a video pop-up rendering on the same GPU made replies crawl, and the doctor reloaded every model mid-job, twice."""
    def hang(*a):
        raise TimeoutError("timed out")
    ready = lambda *names: (lambda *a: {"running": [{"model": n, "state": "ready"} for n in names]})   # noqa: E731
    ok, why, _ = doc.core_test("https://core.example", "k", post=hang, get=ready("chat", "coder", "vision"))
    assert ok is True and why.startswith("busy")
    assert doc.core_test("https://core.example", "k", post=hang, get=ready("chat", "vision"))[:2] == (False, "no answer in time")
    def down(*a):
        raise OSError("refused")
    assert doc.core_test("https://core.example", "k", post=hang, get=down)[0] is False


def test_the_button_sends_one_repair_with_what_is_failing_and_starts_the_allowances_afresh():
    d, starter, clock, _ = world()
    d.observe_home({**failing("flask-api"), **failing("tts-studio"), "whisper": {"ok": True, "ms": 9, "why": ""}})
    d.tries["flask-api"] = [clock.t] * 3
    r = d.repair("captain@example.com")
    assert r["done"] is False and d.tries == {}
    assert handed(starter) == [("repair", "flask-api,tts-studio")]
    assert d.tick() == []                                            # while the repair runs, no heals on top of it
    view = d.view()
    assert {"starter", "docker", "home-reporter", "flask-api", "tts-studio", "whisper"} <= {row["name"] for row in view["rows"]}
    assert view["actions"][0]["what"] == "repair"


def test_openaudible_is_healed_when_its_app_is_gone_and_only_reported_when_the_app_runs():
    d, starter, clock, told = world()
    alive(starter, openaudible=False, openaudible_app=False)
    assert d.tick() == []
    clock.t += doc.HEAL_AFTER_S + 1
    alive(starter, openaudible=False, openaudible_app=False)
    assert d.tick() == ["openaudible"] and handed(starter) == [("heal", "openaudible")]
    row = next(r for r in d.view()["rows"] if r["name"] == "openaudible")
    assert not row["ok"] and row["label"] == "OpenAudible (web server)" and row["why"] == "the app is not running" and row["for_s"] > doc.HEAL_AFTER_S
    alive(starter, openaudible=True, openaudible_app=True)           # healed: green, and the clock starts over
    d.tick()
    assert next(r for r in d.view()["rows"] if r["name"] == "openaudible")["ok"]
    # running with the web server off: never healed, the Captain told once
    d2, starter2, clock2, told2 = world()
    alive(starter2, openaudible=False, openaudible_app=True)
    d2.tick()
    clock2.t += doc.HEAL_AFTER_S + 1
    alive(starter2, openaudible=False, openaudible_app=True)
    assert d2.tick() == [] and handed(starter2) == [] and len(told2) == 1 and "Preferences > Web Server" in told2[0]
    alive(starter2, openaudible=False, openaudible_app=True)
    d2.tick()
    assert len(told2) == 1


def test_the_starters_down_count_survives_a_portal_redeploy_and_apps_are_healed_never_killed():
    d, starter, clock, told = world()
    # a fresh portal (just redeployed) is told by the starter that Spotify has already been down 6 minutes
    alive(starter, spotify=False, vbcable=False, openaudible=True, down_s={"spotify": 360})
    assert d.tick() == ["spotify"] and handed(starter) == [("heal", "spotify")]
    rows = {r["name"]: r for r in d.view()["rows"]}
    assert rows["spotify"]["label"] == "Spotify (desktop)" and not rows["spotify"]["ok"] and rows["spotify"]["for_s"] >= 360
    assert rows["openaudible"]["ok"]
    assert not rows["vbcable"]["ok"] and "VB-Cable" in rows["vbcable"]["label"]


def test_the_vb_cable_is_a_row_only_and_a_short_outage_is_left_alone():
    d, starter, clock, _ = world()
    alive(starter, spotify=False, vbcable=False, down_s={"spotify": 20})
    clock.t += 2000
    alive(starter, spotify=False, vbcable=False, down_s={"spotify": 2020})
    assert d.tick() == ["spotify"]                                   # 2000 s of VB-Cable failing is never a heal
    d2, starter2, clock2, _ = world()
    alive(starter2, spotify=False, down_s={"spotify": 30})
    assert d2.tick() == []


# ── what the Captain is told (2026-11-02: eighteen "still failing after 3 tries" DMs in one evening, from the same two things) ──
def run_failing(d, starter, clock, name, seconds, why="no answer in time"):
    """Time passes with `name` failing the whole while; every heal the doctor asks for fails too."""
    end = clock.t + seconds
    while clock.t < end:
        clock.t += doc.HEAL_AFTER_S + 1
        alive(starter)
        d.observe_home(failing(name, why))
        d.tick()
        for c in starter.poll({"status": {"docker": True, "bridge": True}}):
            starter.poll({"results": [{"id": c["id"], "ok": False, "summary": "still broken"}]})


def run_fine(d, starter, clock, name, seconds):
    end = clock.t + seconds
    while clock.t < end:
        clock.t += 30
        alive(starter)
        d.observe_home({name: {"ok": True, "ms": 12, "why": ""}})
        d.tick()


def test_a_thing_that_stays_broken_is_mentioned_once_then_only_every_six_hours():
    d, starter, clock, told = world()
    run_failing(d, starter, clock, "whisper", 13 * 3600)
    assert len(told) == 3                                              # it gave up; then six hours on; then twelve (it was hourly: 13)
    assert "Whisper" in told[0] and "3 tries" in told[0] and "tell you once when it's back" in told[0]
    assert "still failing after 6 h" in told[1] and "still failing after 12 h" in told[2]


def test_when_it_is_back_he_is_told_once_after_it_has_been_fine_a_while():
    d, starter, clock, told = world()
    run_failing(d, starter, clock, "whisper", 1500)
    assert len(told) == 1
    run_fine(d, starter, clock, "whisper", doc.BACK_AFTER_S - 40)
    assert len(told) == 1                                              # fine for a moment: not yet a recovery
    run_fine(d, starter, clock, "whisper", 120)
    assert len(told) == 2 and "Whisper (hearing) is working again after" in told[1]
    run_fine(d, starter, clock, "whisper", 3600)
    assert len(told) == 2                                              # said once


def test_a_blip_of_good_health_neither_closes_the_episode_nor_starts_a_new_one():
    d, starter, clock, told = world()
    run_failing(d, starter, clock, "whisper", 1500)
    run_fine(d, starter, clock, "whisper", 60)                         # fine for a minute...
    run_failing(d, starter, clock, "whisper", 1500)                    # ...and broken again
    assert len(told) == 1                                              # no "working again", no second "failing"


def test_breaking_again_soon_after_it_was_fixed_is_not_news_but_later_it_is():
    d, starter, clock, told = world()
    run_failing(d, starter, clock, "whisper", 1500)
    run_fine(d, starter, clock, "whisper", 300)
    assert len(told) == 2                                              # failing, then working again
    run_failing(d, starter, clock, "whisper", 1800)
    assert len(told) == 2                                              # it broke again half an hour later: not news yet
    run_failing(d, starter, clock, "whisper", 3 * 3600)
    assert len(told) == 3 and "is failing and 3 tries" in told[2]      # three hours on it is: a new problem, in the first words


def test_a_redeploy_does_not_make_it_forget_who_it_told_or_how_many_tries_it_used():
    from tabletop import MemoryStore
    store = MemoryStore()
    d, starter, clock, told = world(store=store)
    run_failing(d, starter, clock, "whisper", 1500)
    assert len(told) == 1 and store.get(doc.STATE_KEY)
    still = {"whisper": {"ok": False, "ms": 5000, "why": "no answer", "down_s": doc.HEAL_AFTER_S + 30}}
    # the portal is deployed again: a new Doctor, the same bucket, the same hour
    told2 = []
    d2 = doc.Doctor(Home(5), starter, notify=lambda text: told2.append(text) or True, clock=clock, store=store, log=lambda line: None)
    alive(starter)
    d2.observe_home(still)
    assert d2.tick() == [] and told2 == []                             # the three tries are remembered; so is the DM
    clock.t += 7 * 3600
    run_failing(d2, starter, clock, "whisper", 1500)                   # seven hours on, still broken: three more tries, then a reminder
    assert len(told2) == 1 and "still failing after 8 h" in told2[0]       # (since the first failure, not since the deploy)
    # without the bucket (before 2026-11-02) a deploy started it all again: three more tries and the same words
    d3 = doc.Doctor(Home(5), starter, notify=lambda text: told2.append(text) or True, clock=clock, log=lambda line: None)
    alive(starter)
    d3.observe_home(still)
    assert d3.tick() == ["whisper"]


def test_a_bucket_that_fails_never_stops_the_doctor():
    class Broken:
        def get(self, path):
            raise OSError("no bucket")

        def put(self, path, data, content_type):
            raise OSError("no bucket")
    d, starter, clock, told = world(store=Broken())
    run_failing(d, starter, clock, "whisper", 1500)
    assert len(told) == 1 and d._dirty is True                          # still heals and still tells; the next look tries to save again


def test_an_old_episode_in_the_bucket_is_forgotten_quietly():
    import json
    from tabletop import MemoryStore
    store = MemoryStore()
    clock = Clock()
    store.put(doc.STATE_KEY, json.dumps({"open": {"ghost": clock.t - doc.FORGET_S - 5, "fresh": clock.t - 100}, "told": {"ghost": clock.t - 10},
                                         "tries": {"whisper": [clock.t - 4000, clock.t - 60]}}).encode(), "application/json")
    d = doc.Doctor(Home(5), None, clock=clock, store=store, log=lambda line: None)
    assert set(d.open) == {"fresh"} and d.tries == {"whisper": [clock.t - 60]}      # (an hour-old try is no allowance used)


def test_a_desktop_app_is_mentioned_once_then_only_after_a_day():
    d, starter, clock, told = world()
    for _ in range(int(30 * 3600 / (doc.HEAL_AFTER_S + 1))):
        clock.t += doc.HEAL_AFTER_S + 1
        alive(starter, spotify=False)
        d.tick()
        for c in starter.poll({"status": {"docker": True, "bridge": True}}):
            starter.poll({"results": [{"id": c["id"], "ok": False, "summary": "it did not start"}]})
    assert len(told) == 2 and "Spotify" in told[0] and "still failing after 24 h" in told[1]      # (it was one an hour)


def test_the_pc_coming_back_after_a_silence_is_told_once():
    d, starter, clock, told = world(age=doc.UNREACHABLE_DM_S + 5)
    clock.t += starter_mod.ONLINE_S + 1                                  # the starter is silent too
    d.tick()
    assert len(told) == 1 and "skippy-pc" in told[0]
    d.home._age = 5                                                      # the reporter and the starter call again
    alive(starter)
    d.tick()
    assert len(told) == 1                                                # (one good look is not a recovery)
    clock.t += doc.BACK_AFTER_S + 1
    alive(starter)
    d.tick()
    assert len(told) == 2 and told[1].startswith("Skippy's doctor: skippy-pc is working again after")
    clock.t += 60
    alive(starter)
    d.tick()
    assert len(told) == 2


# ── the direct door (edge.py): a row once it has been up, one DM when it stays down, never healed ──
class Door:
    """Stands in for edge.Edge: the doctor only asks it alive() and status()."""

    def __init__(self, host="home.makerportal.live"):
        self.host, self.up, self.ever, self.error, self.looks = host, False, False, "", 0

    def alive(self):
        self.looks += 1
        return self.up

    def status(self):
        return {"host": self.host, "alive": self.up, "checked_s_ago": 1.0, "error": self.error, "ever_alive": self.ever}

    def set(self, up, error=""):
        self.up, self.error = up, error
        self.ever = self.ever or up


def door_world(**kw):
    d, starter, clock, told = world(**kw)
    d.edge = door = Door()
    return d, starter, clock, told, door


def watch_door(d, starter, clock, seconds, step=30, **status):
    """Time passes with the stack up (the starter calls every `step` seconds and the doctor looks each time)."""
    end = clock.t + seconds
    while clock.t < end:
        clock.t += step
        alive(starter, **status)
        d.tick()


def door_row(d):
    return next((r for r in d.view()["rows"] if r["name"] == doc.EDGE_CHECK), None)


def test_a_door_that_was_never_up_is_silent_and_not_even_a_row():
    d, starter, clock, told, door = door_world()
    door.set(False, "the name does not resolve")                       # configured, but the router/DNS were never set up: it is optional
    watch_door(d, starter, clock, 6 * 3600)
    assert told == [] and door_row(d) is None and door.looks > 100     # (it was looked at all along, and said nothing)
    assert handed(starter) == []


def test_no_door_at_all_changes_nothing():
    d, starter, clock, told = world()
    assert d.edge is None
    watch_door(d, starter, clock, 3600)
    assert told == [] and door_row(d) is None and d.view()["ok"] is True


def test_a_door_that_was_up_and_stays_down_ten_minutes_is_told_once_then_every_six_hours():
    d, starter, clock, told, door = door_world()
    door.set(True)
    watch_door(d, starter, clock, 120)
    row = door_row(d)
    assert row["ok"] and row["label"] == "Direct door" and told == []
    door.set(False, "connection refused")
    watch_door(d, starter, clock, doc.EDGE_DOWN_S - 90)
    row = door_row(d)
    assert not row["ok"] and row["why"] == "connection refused" and told == []              # a row at once, a DM only after ten minutes
    watch_door(d, starter, clock, 120)
    assert len(told) == 1
    assert "direct door (home.makerportal.live)" in told[0] and "connection refused" in told[0] and "Funnel" in told[0]
    assert told[0].startswith("Skippy's doctor:") and "10 min" in told[0]
    watch_door(d, starter, clock, 5 * 3600)
    assert len(told) == 1                                                                   # said once
    watch_door(d, starter, clock, 3600)
    assert len(told) == 2 and "still down after 6 h" in told[1]                             # and again only after six hours


def test_when_the_door_is_back_he_is_told_once_after_it_has_been_up_a_while():
    d, starter, clock, told, door = door_world()
    door.set(True)
    watch_door(d, starter, clock, 60)
    door.set(False, "no answer in time")
    watch_door(d, starter, clock, 1500)
    assert len(told) == 1
    door.set(True)
    watch_door(d, starter, clock, doc.BACK_AFTER_S - 60)
    assert len(told) == 1                                                                   # up for a moment: not yet a recovery
    watch_door(d, starter, clock, 120)
    assert len(told) == 2 and "Direct door is working again after" in told[1]
    assert door_row(d)["ok"] is True
    watch_door(d, starter, clock, 3600)
    assert len(told) == 2


def test_a_blip_shorter_than_ten_minutes_says_nothing_and_the_count_starts_over_each_time():
    d, starter, clock, told, door = door_world()
    door.set(True)
    watch_door(d, starter, clock, 60)
    for _ in range(4):                                                  # down nine minutes, up for a minute, down again...
        door.set(False, "no answer in time")
        watch_door(d, starter, clock, doc.EDGE_DOWN_S - 60)
        door.set(True)
        watch_door(d, starter, clock, 60)
    assert told == [] and door_row(d)["ok"] is True


def test_the_door_is_never_healed_and_the_repair_button_does_not_trip_over_it():
    d, starter, clock, told, door = door_world()
    door.set(True)
    watch_door(d, starter, clock, 60)
    door.set(False, "connection refused")
    watch_door(d, starter, clock, 3 * 3600)
    assert len(told) == 1 and handed(starter) == []                     # no heal for it, however long: a router is not the starter's
    assert "direct-door" not in doc.HEALABLE and doc.EDGE_CHECK in doc.REPORT_ONLY
    run = d.repair("captain@example.com")                               # the starter would refuse a name it does not heal: the button must not name it
    sent = handed(starter)
    assert run["done"] is False and [op for op, _ in sent] == ["repair"] and not sent[0][1]       # (a repair that names nothing: the door is not one of its services)
    assert "starter" in {r["name"] for r in d.view()["rows"]} and door_row(d)["ok"] is False


def test_the_home_reporter_cannot_make_up_a_door():
    d, starter, clock, told = world()
    d.observe_home({"direct-door": {"ok": False, "ms": 1, "why": "forged"}})
    assert door_row(d) is None


def test_a_door_nobody_has_looked_at_for_a_while_starts_its_clock_over():
    """The PC was off, the stack was stopped on purpose, or Docker was down: how long the door has been down is not known, so no DM comes at once."""
    d, starter, clock, told, door = door_world()
    door.set(True)
    watch_door(d, starter, clock, 60)
    door.set(False, "connection refused")
    watch_door(d, starter, clock, 300)                                  # down five minutes...
    for _ in range(360):                                                # ...then the Captain stops the stack for three hours (the starter still calls)
        clock.t += 30
        starter.poll({"status": {"docker": True, "bridge": False, "resume_at": "2026-11-05T21:00"}})
        d.tick()
    looks = door.looks
    assert told == []
    watch_door(d, starter, clock, doc.EDGE_DOWN_S - 90)                 # the stack is back: the door is still down, but nobody knows for how long
    assert told == [] and door.looks > looks
    watch_door(d, starter, clock, 150)
    assert len(told) == 1                                               # ten minutes of looking later, it is news


def test_docker_down_is_that_one_problem_not_the_doors_as_well():
    d, starter, clock, told, door = door_world()
    door.set(True)
    watch_door(d, starter, clock, 60)
    door.set(False, "connection refused")                               # Docker dying takes the door's container with it...
    watch_door(d, starter, clock, 20)
    looks = door.looks
    watch_door(d, starter, clock, 2 * 3600, docker=False)
    assert door.looks == looks                                          # ...and the door is not looked at, or told, while Docker is the thing down
    assert [t for t in told if "direct door" in t] == [] and any("Docker is failing" in t for t in told)      # (Docker has its own word)
    door_words = lambda: [t for t in told if "direct door" in t]        # noqa: E731
    watch_door(d, starter, clock, doc.EDGE_DOWN_S - 90)                 # Docker is back (and said so), the door still isn't
    assert door_words() == [] and any("Docker is working again" in t for t in told)
    watch_door(d, starter, clock, 150)
    assert len(door_words()) == 1


def test_a_door_that_cannot_be_asked_never_stops_the_doctor():
    class Broken(Door):
        def status(self):
            raise RuntimeError("no door")
    d, starter, clock, told = world()
    d.edge = Broken()
    d.observe_home(failing("voice-gate"))
    clock.t += doc.HEAL_AFTER_S + 1
    alive(starter)
    d.observe_home(failing("voice-gate"))
    assert d.tick() == ["voice-gate"] and told == [] and door_row(d) is None       # (everything else is looked after as before)


def test_a_redeploy_remembers_the_door_was_told_and_still_says_when_it_is_back():
    from tabletop import MemoryStore
    store = MemoryStore()
    d, starter, clock, told, door = door_world(store=store)
    door.set(True)
    watch_door(d, starter, clock, 60)
    door.set(False, "connection refused")
    watch_door(d, starter, clock, 1500)
    assert len(told) == 1 and "direct-door" in json.loads(store.get(doc.STATE_KEY))["open"]
    # the portal is deployed again (the Edge is new: it has to see the door up once before the doctor says anything about it)
    told2 = []
    d2 = doc.Doctor(Home(5), starter, notify=lambda text: told2.append(text) or True, clock=clock, store=store, log=lambda line: None)
    d2.edge = door2 = Door()
    door2.set(False, "connection refused")
    watch_door(d2, starter, clock, 3600)
    assert told2 == [] and door_row(d2) is None                          # never up in this process: silent
    door2.set(True)
    watch_door(d2, starter, clock, 60)
    assert told2 == []
    watch_door(d2, starter, clock, doc.BACK_AFTER_S)
    assert len(told2) == 1 and "Direct door is working again after" in told2[0]
    door2.set(False, "connection refused")
    watch_door(d2, starter, clock, doc.FLAP_S - 600)
    assert len(told2) == 1                                               # it broke again within the hour of the last word: not news yet
    watch_door(d2, starter, clock, 1200)
    assert len(told2) == 2 and "has been down for" in told2[1]           # an hour on it is


def test_the_real_edge_is_read_through_its_status_without_any_waiting():
    """edge.Edge itself, a scripted probe and the doctor's own clock: the keys the doctor reads are the keys the Edge gives."""
    import edge as edge_mod
    d, starter, clock, told = world()
    answers = [(True, "")]
    d.edge = real = edge_mod.Edge("home.makerportal.live", probe=lambda url, timeout: answers[-1], clock=clock, ttl_ok=10 ** 9, ttl_bad=10 ** 9)
    real.refresh()
    watch_door(d, starter, clock, 60)
    assert door_row(d)["ok"] is True and told == []
    answers.append((False, "its certificate is not accepted"))
    real.refresh()
    watch_door(d, starter, clock, doc.EDGE_DOWN_S + 60)
    assert len(told) == 1 and "its certificate is not accepted" in told[0] and "home.makerportal.live" in told[0]
    answers.append((True, ""))
    real.refresh()
    watch_door(d, starter, clock, doc.BACK_AFTER_S + 60)
    assert len(told) == 2 and "working again" in told[1]


def test_a_probe_that_hangs_never_holds_up_the_doctor():
    """The doctor's tick runs inside the home reporter's and the starter's requests: asking about the door must be an answer from memory."""
    import threading
    import time
    import edge as edge_mod
    d, starter, clock, told = world()
    release, asked = threading.Event(), []

    def probe(url, timeout):
        asked.append(url)
        if len(asked) > 1:
            assert release.wait(10), "the test never let the probe go"
        return True, ""
    d.edge = real = edge_mod.Edge("home.makerportal.live", probe=probe, clock=clock)
    real.refresh()
    clock.t += edge_mod.TTL_OK_S + 1                                    # its answer is a minute old: the next look starts a refresh, which hangs
    alive(starter)
    began = time.monotonic()
    d.tick()
    d.tick()
    assert time.monotonic() - began < 1.0 and door_row(d)["ok"] is True
    deadline = time.monotonic() + 5
    while len(asked) < 2 and time.monotonic() < deadline:
        time.sleep(0.002)
    assert len(asked) == 2                                              # (one refresh, however many looks)
    release.set()
    assert real.wait()


def test_an_edge_that_was_never_switched_on_is_not_a_row_either():
    import edge as edge_mod

    def no_probe(url, timeout):
        raise AssertionError("no probe for a door that is off")
    d, starter, clock, told = world()
    d.edge = edge_mod.Edge("", probe=no_probe, clock=clock)
    watch_door(d, starter, clock, 3600)
    assert told == [] and door_row(d) is None
