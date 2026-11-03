"""The reporter's `flash` job (Skippy-vision, portal/vision.py): the bridge (native Windows) takes this PC's monitors and the room boxes'
displays; each screen goes to the portal as it is, into the moment the job names; one that can't be taken is an error line."""
import base64

import pytest

import reporter
from test_music import Fake, _json

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def b64(raw):
    return base64.b64encode(raw).decode()


@pytest.fixture
def world(monkeypatch):
    seen = {"bridge": [], "portal": []}
    answer = {"value": (200, {"files": [{"name": "pc-screen-1.png", "b64": b64(PNG)}, {"name": "pod-theater.png", "b64": b64(PNG + b"1")}], "errors": []})}

    def bridge(method, path, body, token, timeout=40):
        seen["bridge"].append((method, path, body, token, timeout))
        return answer["value"]

    def portal(method, path, headers, body):
        seen["portal"].append((method, path, headers.get("Content-Type"), headers.get("Authorization"), body))
        return _json({"ok": True}) if "refuse" not in path else _json({"error": "no"}, 400)
    fake = Fake(portal)
    monkeypatch.setattr(reporter, "BRIDGE", "http://bridge")
    monkeypatch.setattr(reporter, "_bridge", bridge)
    monkeypatch.setattr(reporter, "PORTAL", fake.url)
    yield seen, answer
    fake.close()


def test_each_screen_goes_to_the_portal_under_its_name_into_the_moments_job(world):
    seen, _ = world
    out = reporter.run_job({"kind": "flash", "id": "job1", "moment": "20261102-210000"}, "tok")
    assert out == {"result": {"files": ["pc-screen-1.png", "pod-theater.png"], "errors": []}}
    assert seen["bridge"] == [("POST", "/flash/screens", {}, "tok", 90)]
    assert [(m, p, c, a) for m, p, c, a, _ in seen["portal"]] == [
        ("PUT", "/home/media/job1?name=pc-screen-1.png", "image/png", "Bearer tok"), ("PUT", "/home/media/job1?name=pod-theater.png", "image/png", "Bearer tok")]
    assert seen["portal"][1][4] == PNG + b"1"


def test_a_screen_the_bridge_could_not_take_is_said_and_the_rest_still_go(world):
    seen, answer = world
    answer["value"] = (200, {"files": [{"name": "pod-theater.png", "b64": b64(PNG)}], "errors": ["pc-screen-1: OSError: screen grab failed"]})
    out = reporter.run_job({"kind": "flash", "id": "j"}, "tok")
    assert out["result"]["files"] == ["pod-theater.png"] and out["result"]["errors"] == ["pc-screen-1: OSError: screen grab failed"]


def test_nothing_the_bridge_sends_back_is_trusted(world):
    seen, answer = world
    answer["value"] = (200, {"files": [{"name": "../etc.png", "b64": b64(PNG)}, {"name": "chat.png", "b64": b64(PNG)},
                                       {"name": "pc-screen-1.png", "b64": b64(b"not a png")}, {"name": "pc-screen-2.png", "b64": "@@@"},
                                       {"name": "pod-ok.png", "b64": b64(PNG)}], "errors": []})
    out = reporter.run_job({"kind": "flash", "id": "j"}, "tok")["result"]
    assert out["files"] == ["pod-ok.png"] and len(out["errors"]) == 4 and len(seen["portal"]) == 1


def test_a_portal_that_refuses_one_screen_does_not_stop_the_others(world, monkeypatch):
    seen, answer = world
    answer["value"] = (200, {"files": [{"name": "pod-refuse.png", "b64": b64(PNG)}, {"name": "pod-ok.png", "b64": b64(PNG)}], "errors": []})
    monkeypatch.setattr(reporter, "PORTAL", reporter.PORTAL)            # (the fake refuses a path with 'refuse' in it, with HTTP 400)
    out = reporter.run_job({"kind": "flash", "id": "j"}, "tok")["result"]
    assert out["files"] == ["pod-ok.png"] and "pod-refuse.png: couldn't hand it to the portal" in out["errors"][0]


def test_a_silent_or_failing_bridge_is_explained(world, monkeypatch):
    seen, answer = world
    answer["value"] = (None, {})
    assert "isn't answering" in reporter.run_job({"kind": "flash", "id": "j"}, "tok")["error"]
    answer["value"] = (503, {"error": "bridge_disabled"})
    assert "HTTP 503" in reporter.run_job({"kind": "flash", "id": "j"}, "tok")["error"]
    monkeypatch.setattr(reporter, "BRIDGE", "")
    assert "isn't set up" in reporter.run_job({"kind": "flash", "id": "j"}, "tok")["error"]
    assert not seen["portal"]
