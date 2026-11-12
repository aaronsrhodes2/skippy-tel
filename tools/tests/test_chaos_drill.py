"""The chaos drill's safety: dry run by default, an allow-list of targets, and the gates that keep it off a stack someone is using."""
from datetime import datetime

import pytest

import chaos_drill as cd


@pytest.fixture(autouse=True)
def _nothing_found(monkeypatch):
    """No test reaches Docker or skippy-core: discovery finds nothing unless a test says otherwise."""
    monkeypatch.setattr(cd, "_FOUND", {})


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


# ── the weekly check and discovery (the Captain, 2026-11-01: "once a week, but it also needs to auto-discover new additions") ──
PS_PC = "\n".join([
    "skippytel-home-reporter|home-reporter|skippytel|127.0.0.1:47450->47450/tcp",
    "skippytel-flask-api|flask-api|skippytel|0.0.0.0:3003->5000/tcp",
    "skippytel-new-gate|new-gate|skippytel|127.0.0.1:3011->8080/tcp",
    "skippytel-quiet|quiet|skippytel|127.0.0.1:3012->8080/tcp",
    "skippytel-image-studio|image-studio|skippytel|8002/tcp",
    "skippycraft|skippycraft|dev-server|127.0.0.1:42565->25565/tcp",
])
PS_CORE = "\n".join([
    "skippycore-llm-1|llm|skippycore|Up 2 hours (healthy)",
    "skippycore-routes-1|routes|skippycore|Up 2 hours (healthy)",
    "skippycore-car-fleet-1|car-fleet|skippycore|Up 2 hours (healthy)",
    "skippycore-teslamate-db-1|teslamate-db|skippycore|Up 2 hours (healthy)",
    "skippycore-starting-1|starting|skippycore|Up 5 seconds (health: starting)",
])


def test_discovery_finds_new_services_on_both_machines_and_leaves_the_rest_alone(monkeypatch):
    monkeypatch.setattr(cd, "run", lambda cmd, timeout=60: (0, PS_PC))
    monkeypatch.setattr(cd, "ssh", lambda script, timeout=60: (0, PS_CORE))
    monkeypatch.setattr(cd, "get_json", lambda url, timeout=5: {"status": "ok"} if ":3011/" in url else {"status": "starting"})
    found = cd.discover()
    assert set(found) == {"new-gate", "core-routes"}       # not the reporter, a studio, a fixed target, an unready one, the db, the guard
    assert found["new-gate"]["probe"] == "http://127.0.0.1:3011/health" and found["core-routes"]["kind"] == "core-container"


def test_the_week_picks_a_never_drilled_target_first_then_the_one_drilled_longest_ago():
    hist = [{"target": "flask-api", "at": "2026-11-01T10:00:00"}, {"target": "bridge", "at": "2026-10-01T10:00:00"}]
    assert cd.pick(["flask-api", "bridge", "new-gate"], hist) == "new-gate"
    assert cd.pick(["flask-api", "bridge"], hist) == "bridge"


def test_the_weekly_run_drills_one_target_and_writes_the_morning_report(monkeypatch, tmp_path):
    monkeypatch.setattr(cd, "WEEKLY", tmp_path / "weekly.json")
    monkeypatch.setattr(cd, "_FOUND", {"new-gate": {"kind": "container", "container": "skippytel-new-gate", "found": True}})
    monkeypatch.setattr(cd, "history", lambda: [{"target": n, "at": "2026-10-01"} for n in cd.TARGETS])
    monkeypatch.setattr(cd, "gates", lambda name: [])
    monkeypatch.setattr(cd.time, "sleep", lambda s: None)
    ran = []
    monkeypatch.setattr(cd, "drill", lambda name, say=print: ran.append(name) or {"heal_s": 40, "human_needed": False})
    res = cd.weekly(wait_s=0)
    assert ran == ["new-gate"] and res["new"] == ["new-gate"] and res["heal_s"] == 40
    assert (tmp_path / "weekly.json").exists()


def test_the_weekly_run_waits_when_the_house_is_busy(monkeypatch, tmp_path):
    monkeypatch.setattr(cd, "WEEKLY", tmp_path / "weekly.json")
    monkeypatch.setattr(cd, "history", lambda: [])
    monkeypatch.setattr(cd, "gates", lambda name: ["a voice turn"])
    monkeypatch.setattr(cd, "drill", lambda name, say=print: pytest.fail("it ran while the house was busy"))
    assert cd.weekly(wait_s=0)["skipped"] == ["a voice turn"]


def test_the_morning_report_says_how_the_week_went():
    import nightly
    assert "came back by itself in 40 seconds" in nightly.healing_words({"target": "voice-gate", "heal_s": 40, "new": []})
    assert "found a gap: core-routes" in nightly.healing_words({"target": "core-routes", "human_needed": True, "new": ["core-routes"]})
    assert "1 new service to test" in nightly.healing_words({"target": "x", "heal_s": 3, "new": ["core-routes"]})
    assert "waited" in nightly.healing_words({"target": "x", "skipped": ["busy"]})
    assert nightly.healing_words(None) == ""


def test_the_healing_check_runs_only_on_its_night(monkeypatch):
    import nightly
    monkeypatch.setattr(nightly, "CHAOS_DAY", "sun")
    monkeypatch.setattr(nightly.time, "localtime", lambda *a: __import__("time").struct_time((2026, 11, 2, 1, 30, 0, 0, 306, 0)))   # a Monday
    monkeypatch.setattr(nightly, "sh", lambda *a, **k: pytest.fail("drilled on the wrong night"))
    assert nightly.healing_check(False, print) is None
