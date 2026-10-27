"""The house job: the portal chat's house tools run here with services/satellite's own code (house.run_tool), and a chat never confirms a lock."""
from pathlib import Path

import pytest

import reporter

SATELLITE = str(Path(__file__).resolve().parents[2] / "satellite")


@pytest.fixture
def lamp(monkeypatch):
    monkeypatch.setattr(reporter, "SATELLITE", SATELLITE)
    monkeypatch.setattr(reporter, "_house", [])
    reporter.house.__globals__["sys"].path.insert(0, SATELLITE)
    from house import House

    class Lamp(House):
        calls: list = []

        def states(self, fresh=False):
            return [{"entity_id": "light.kitchen_under_1", "state": "off", "attributes": {"friendly_name": "kitchen under 1"}},
                    {"entity_id": "lock.front", "state": "locked", "attributes": {"friendly_name": "front door"}}]

        def _service(self, domain, service, data):
            self.calls.append((service, data["entity_id"]))
    lamp = Lamp()
    lamp.sensitive = True
    monkeypatch.setattr(reporter, "_house", [lamp])
    return lamp


def test_the_house_tools_run_here_and_answer_in_a_sentence(lamp):
    assert reporter.capabilities.__module__ == "reporter" and reporter.house_capability() == {"ready": True}
    out = reporter.run_job({"kind": "house", "tool": "home_control", "args": {"name": "kitchen under 1", "action": "on"}})
    assert out["result"]["text"].startswith("Done") and lamp.calls == [("turn_on", "light.kitchen_under_1")]
    assert "kitchen under 1 (off)" in reporter.run_job({"kind": "house", "tool": "home_devices", "args": {}})["result"]["text"]


def test_a_chat_never_confirms_a_lock(lamp):
    out = reporter.run_job({"kind": "house", "tool": "home_control", "args": {"name": "front door", "action": "unlock", "confirm": True}})
    assert "confirm" in out["result"]["text"] and lamp.calls == []


def test_no_house_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(reporter, "house", lambda: None)
    assert reporter.run_job({"kind": "house", "tool": "home_devices"}) == {"error": "the house isn't set up on skippy-pc"}
