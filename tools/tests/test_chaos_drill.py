"""The chaos drill's safety: dry run by default, an allow-list of targets, and the gates that keep it off a stack someone is using."""
from datetime import datetime

import pytest

import chaos_drill as cd


def test_only_the_allow_listed_targets_exist_and_none_is_the_hub_or_the_games_vm():
    assert set(cd.TARGETS) == {"flask-api", "voice-gate", "music-gate", "bridge", "core"}
    with pytest.raises(SystemExit):
        cd.main(["hub", "--go"])
    with pytest.raises(SystemExit):
        cd.main(["games", "--go"])


def test_without_go_nothing_is_stopped(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(cd, "drill", lambda name, say=print: ran.append(name))
    monkeypatch.setattr(cd, "plan", lambda name: "would stop it")
    monkeypatch.setattr(cd, "gates", lambda name: [])
    assert cd.main(["flask-api"]) == 0
    assert cd.main([]) == 0
    assert ran == []
    assert "DRY RUN" in capsys.readouterr().out


def test_a_closed_gate_refuses_the_drill(monkeypatch):
    ran = []
    monkeypatch.setattr(cd, "drill", lambda name, say=print: ran.append(name))
    monkeypatch.setattr(cd, "gates", lambda name: ["a purple run is going"])
    assert cd.main(["bridge", "--go"]) == 3
    assert ran == []


def test_the_gates_are_checked_twice_before_it_goes(monkeypatch):
    looks = iter([[], ["a voice turn"]])                 # quiet at first, someone starts talking in the 20 s between
    monkeypatch.setattr(cd, "gates", lambda name: next(looks))
    monkeypatch.setattr(cd.time, "sleep", lambda s: None)
    monkeypatch.setattr(cd, "drill", lambda name, say=print: pytest.fail("it ran while someone was talking"))
    assert cd.main(["voice-gate", "--go"]) == 3


def test_the_hub_counts_as_busy_for_three_minutes_after_any_conversation_or_routine_line():
    now = datetime(2026, 11, 1, 10, 0, 0)
    talk = ["09:58:30 [office] heard: what time is it", "09:59:50 [office] face stream: 20 frames/s"]
    assert "conversation" in cd.hub_busy(now, talk)
    assert cd.hub_busy(now, ["09:55:00 [office] said: it's ten", "09:59:50 [office] face stream: 20 frames/s"]) == ""
    assert cd.hub_busy(now, ["09:59:00 [office] routine: morning"]) != ""
    assert cd.hub_busy(datetime(2026, 11, 1, 0, 0, 30), ["23:59:40 [office] woke"]) != ""     # across midnight


def test_a_bridge_generation_is_only_a_target_when_its_pid_really_is_bridge_py(monkeypatch, tmp_path):
    state = tmp_path / "front.json"
    state.write_text('{"gens": [{"n": 3, "port": 9341, "pid": 111, "draining": true}, {"n": 4, "port": 9342, "pid": 222}]}')
    monkeypatch.setattr(cd, "FRONT_STATE", state)
    monkeypatch.setattr(cd.holders, "processes", lambda: [{"pid": 222, "name": "python.exe", "cmd": "python.exe bridge.py"}])
    assert cd.bridge_generation()["pid"] == 222
    monkeypatch.setattr(cd.holders, "processes", lambda: [{"pid": 222, "name": "python.exe", "cmd": "python.exe hub.py"}])
    assert cd.bridge_generation() is None                # a reused PID is never killed
