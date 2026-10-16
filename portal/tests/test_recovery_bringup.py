"""The Start-or-stop card at the top of Admin > Recovery: each step of bringing SkippyTel up, from the starter's answer, skippy-tel-core's health and
skippy-pc's report; the JSON the card polls is the Captain's alone; and the public chat is named Mentat for everyone but the Captain."""
import pytest

from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login

import app as portal
import catalog


@pytest.fixture
def world():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    return app, app.test_client(), app.extensions["starter"]


def steps_of(client):
    return {s["id"]: s for s in client.get("/admin/recovery/status.json").get_json()["steps"]}


def test_each_step_is_ok_wait_warn_or_bad_from_the_starter_and_the_core(world):
    app, client, starter = world
    login(client, ADMIN)
    starter.poll({"status": {"docker": True, "containers_up": 2, "containers_total": 10, "bridge": False, "busy": True, "working": "starting containers"}})
    app.extensions["home"].accept({"health": {"status": "ok", "ollama": {"available": True, "default": "qwen3:8b", "default_present": False},
                                              "tiers": {"claude": True, "nagatha": True, "elevenlabs": True}, "bridge": True},
                                   "capabilities": {"workshop": {"ready": True}}})
    doc = client.get("/admin/recovery/status.json").get_json()
    st = {s["id"]: s for s in doc["steps"]}
    assert st["starter"]["state"] == "ok" and st["docker"] == {"id": "docker", "label": "Docker Desktop", "state": "ok", "detail": "up"}
    assert st["containers"]["state"] == "wait" and st["containers"]["detail"] == "2 of 10"           # still coming up: not an error yet
    assert st["bridge"]["state"] == "bad" and st["bridge"]["detail"] == "DOWN"
    assert st["models"]["state"] == "bad" and "qwen3:8b" in st["models"]["detail"]                  # Ollama is up but missing its default model
    assert st["services"]["state"] == "warn" and "Mentat" in st["services"]["detail"] and "Private Skippy" not in st["services"]["detail"]
    assert doc["working"] == "starting containers" and doc["online"] is True


def test_with_no_report_yet_the_model_and_service_steps_say_so_instead_of_failing(world):
    app, client, starter = world
    login(client, ADMIN)
    doc = client.get("/admin/recovery/status.json").get_json()
    st = {s["id"]: s for s in doc["steps"]}
    assert doc["online"] is False and st["starter"]["state"] == "bad" and "not answering" in st["starter"]["detail"]
    assert st["docker"]["state"] == "wait" and st["models"]["state"] == "wait" and st["services"]["state"] == "wait"


def test_a_healthy_stack_is_all_ok(world):
    app, client, starter = world
    login(client, ADMIN)
    starter.poll({"status": {"docker": True, "containers_up": 10, "containers_total": 10, "bridge": True, "busy": False}})
    app.extensions["home"].accept({"health": {"status": "ok", "ollama": {"available": True, "default": "qwen3:8b", "default_present": True},
                                              "tiers": {"claude": True, "nagatha": True, "elevenlabs": True}, "bridge": True},
                                   "capabilities": {c: {"ready": True} for c in ("workshop", "make_music", "music_gate", "video_gate", "voice_gate")},
                                   "mentat_home": True})
    st = steps_of(client)
    assert all(st[k]["state"] == "ok" for k in ("starter", "docker", "containers", "bridge", "models", "services")), st


def test_the_status_is_the_captains_alone(world):
    app, client, starter = world
    assert client.get("/admin/recovery/status.json").status_code in (302, 401, 403)     # not signed in
    login(client, FRIEND)
    assert client.get("/admin/recovery/status.json").status_code in (302, 401, 403)     # a friend
    assert b"steps" not in client.get("/admin/recovery/status.json").data


def test_the_recovery_card_is_at_the_top_and_polls_its_status(world):
    app, client, starter = world
    login(client, ADMIN)
    page = client.get("/admin").data.decode()
    recovery = page.split('<section id="recovery">', 1)[1]
    assert recovery.index('id="starter"') < recovery.index('id="live-version"')                  # the card is first
    assert 'data-poll-url="/admin/recovery/status.json"' in recovery and 'data-step="models"' in recovery
    assert "/static/recovery-live.js" in page


def test_the_public_chat_is_named_mentat_and_the_captains_is_skippy(world):
    app, client, starter = world
    login(client, ADMIN)
    assert "<title>Skippy the Magnificent — Maker Portal</title>" in client.get("/chat").data.decode()
    friend = app.test_client()
    login(friend, FRIEND)
    page = friend.get("/chat").data.decode()
    assert "<title>Mentat — Maker Portal</title>" in page and "Skippy the Magnificent" not in page.split("<title>")[1].split("</title>")[0]
    assert catalog.PORTALS["skippy"] == "Mentat (chat)"
