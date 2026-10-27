"""The house's tools in the portal chat: the Captain's alone, a copy of services/satellite/house.py's TOOLS that cannot drift, run on
skippy-pc as a "house" job, and a confirm never leaves the portal (a chat can't prove he said yes out loud)."""
import importlib.util
from pathlib import Path

import pytest

import chat_tools
from home import Home

SATELLITE_HOUSE = Path(__file__).resolve().parents[2] / "services" / "satellite" / "house.py"


def satellite_tools():
    spec = importlib.util.spec_from_file_location("satellite_house", SATELLITE_HOUSE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.TOOLS


class FakeHome:
    def __init__(self, answer="Done: kitchen under 1, on.", fail=None):
        self.answer, self.fail, self.calls = answer, fail, []

    def house_tool(self, tool, args):
        self.calls.append((tool, args))
        if self.fail:
            raise self.fail
        return self.answer


def test_the_portal_copy_matches_the_satellite_tools_exactly():
    assert chat_tools.HOUSE_TOOLS == satellite_tools()


def test_only_the_captain_gets_the_house():
    home = FakeHome()
    assert set(chat_tools.HOUSE_NAMES) <= set(chat_tools.ChatTools(None, is_admin=True, home_house=home).available())
    friend = chat_tools.ChatTools(None, is_admin=False, home_house=home)
    assert not set(chat_tools.HOUSE_NAMES) & set(friend.available())
    assert not set(chat_tools.HOUSE_NAMES) & {t["name"] for t in friend.schemas()}
    with pytest.raises(chat_tools.ToolError):
        friend.run("home_control", {"name": "kitchen under 1", "action": "on"})


def test_a_control_runs_at_home_and_a_confirm_never_leaves():
    home = FakeHome()
    tools = chat_tools.ChatTools(None, is_admin=True, home_house=home)
    assert "Done" in tools.run("home_control", {"name": "front door", "action": "unlock", "confirm": True})
    assert home.calls == [("home_control", {"name": "front door", "action": "unlock"})]


def test_a_silent_home_is_said_plainly():
    tools = chat_tools.ChatTools(None, is_admin=True, home_house=FakeHome(fail=TimeoutError("skippy-pc didn't answer in time")))
    with pytest.raises(chat_tools.ToolError, match="didn't answer"):
        tools.run("home_devices", {})


def test_home_sends_a_house_job_and_returns_its_sentence():
    home = Home(None)
    import threading
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("text", home.house_tool("home_devices", {}, timeout=5)))
    t.start()
    for _ in range(200):
        jobs = [j["job"] for j in home._jobs.values()]
        if jobs:
            break
        threading.Event().wait(0.01)
    assert jobs[0]["kind"] == "house" and jobs[0]["tool"] == "home_devices"
    home.finish(jobs[0]["id"], {"result": {"text": "kitchen under 1 (off)"}})
    t.join(5)
    assert out["text"] == "kitchen under 1 (off)"
