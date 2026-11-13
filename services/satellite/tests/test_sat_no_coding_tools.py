"""A house hub must never be able to start coding (the Captain, 2026-11-13: guests may do all house and car things, but a hub must never start coding).

Skippy in the rooms thinks with `brain.Brain`; what it can DO is only the tools it is offered. This pins that none of them is an agent, a shell, an editor or a
deployer, and that the hub's code does not import the purple Workshop. (There is no speaker recognition: anyone who can talk to a hub is a guest, so the only
guarantee is that the capability does not exist here.)"""
import re
from pathlib import Path

import brain
import car
import hass as hass_mod
import hub
import news as news_mod
import physics
import weather

FORBIDDEN = re.compile(r"agent|workshop|purple|coder|coding|shell|bash|powershell|exec|run_?command|run_?code|terminal|deploy|git_|_git|edit_?file|write_?file|"
                       r"patch|compile|pytest|docker|ssh|claude|task_?(?:create|update)|spawn|subprocess", re.I)


def _names():
    names = {t["name"] for t in brain.TOOLS}
    names |= {t["name"] for t in hub.MUSIC_TOOLS} | {hub.ANNOUNCE_TOOL["name"]}
    names |= {t["name"] for t in hass_mod.TOOLS} | set(brain.ELSEWHERE)      # (car_status, ask_mentat: the tools car.py and physics.py add)
    for mod in (car, physics, weather, news_mod):
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if isinstance(obj, list) and obj and all(isinstance(t, dict) and "input_schema" in t for t in obj):
                names |= {t["name"] for t in obj}
    return names


def test_no_tool_a_hub_is_offered_can_code_run_a_shell_or_deploy():
    names = _names()
    assert {"set_timer", "home_control", "car_status"} <= names                       # the scan really sees the hub's tools
    bad = sorted(n for n in names if FORBIDDEN.search(n))
    assert not bad, f"a hub is offered tools that could start coding: {bad}"


def test_the_hub_code_does_not_reach_the_workshop_or_the_bridge():
    here = Path(hub.__file__).resolve().parent
    for name in ("hub.py", "brain.py", "esphome_link.py", "house_mcp.py", "homechat.py"):
        src = (here / name).read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M)
        assert not [i for i in imports if i.split(".")[0] in ("workshop_engine", "sdk_engine", "claude_agent_sdk", "bridge", "front", "claude_bridge")], name
