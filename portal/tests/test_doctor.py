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


def world(age=5):
    clock = Clock()
    starter = starter_mod.Starter(clock=clock)
    starter.poll({"status": {"docker": True, "containers_up": 9, "containers_total": 10, "bridge": True, "busy": False}})
    told = []
    d = doc.Doctor(Home(age), starter, notify=lambda text: told.append(text) or True, clock=clock)
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


def test_three_tries_an_hour_then_one_dm_and_it_waits():
    d, starter, clock, told = world()
    d.observe_home(failing("ollama"))
    healed = 0
    for _ in range(6):
        clock.t += doc.HEAL_AFTER_S + 1
        alive(starter)
        d.observe_home(failing("ollama"))
        healed += len(d.tick())
        for c in starter.poll({"status": {"docker": True, "bridge": True}}):
            starter.poll({"results": [{"id": c["id"], "ok": False, "summary": "still broken"}]})
    assert healed == doc.MAX_TRIES and len(told) == 1 and "Ollama" in told[0] and "3 tries" in told[0]
    clock.t += 3600
    alive(starter)
    d.observe_home(failing("ollama"))
    assert d.tick() == ["ollama"]                                    # a new hour: it tries again


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
    assert doc.core_test("https://core.example", "k", post=hang)[:2] == (False, "no answer in time")
    assert doc.core_test("", "", post=post)[:2] == (False, "Skippy Core isn't configured")


def test_the_button_sends_one_repair_with_what_is_failing_and_starts_the_allowances_afresh():
    d, starter, clock, _ = world()
    d.observe_home({**failing("ollama"), **failing("tts-studio"), "whisper": {"ok": True, "ms": 9, "why": ""}})
    d.tries["ollama"] = [clock.t] * 3
    r = d.repair("captain@example.com")
    assert r["done"] is False and d.tries == {}
    assert handed(starter) == [("repair", "ollama,tts-studio")]
    assert d.tick() == []                                            # while the repair runs, no heals on top of it
    view = d.view()
    assert {"starter", "docker", "home-reporter", "ollama", "tts-studio", "whisper"} <= {row["name"] for row in view["rows"]}
    assert view["actions"][0]["what"] == "repair"
