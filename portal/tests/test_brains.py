"""brains.py: the chat's three modes (Public for everyone on Skippy Core; Private and Auto, the agent, the Captain only),
the one brain /chat/send answers with, and when it counts as available. Gemini and the paid quick-chat brains are gone."""
import json

import pytest

import brains


def test_a_guest_has_public_only_and_the_captain_has_all_three_in_order():
    assert [m["id"] for m in brains.modes(False)] == ["public"]
    assert [(m["id"], m["theme"], m["agent"]) for m in brains.modes(True)] == [
        ("public", "green", None), ("private", "purple", "core"), ("auto", "orange", "claude")]


def test_chat_send_answers_with_skippy_core_only_whoever_asks():
    for admin in (False, True):
        for asked in (None, "", "core", "public"):
            assert brains.pick(asked, admin) is brains.CORE
        for asked in ("haiku", "opus", "gemini", "private", "auto", "agent-claude"):
            with pytest.raises(PermissionError):
                brains.pick(asked, admin)
    assert list(brains.BRAINS) == ["core"] and brains.BARNEY == brains.DEFAULT == "core"


def test_a_brain_is_ready_only_with_its_key_and_base(monkeypatch):
    for v in ("SKIPPY_CORE_URL", "SKIPPY_CORE_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    core = brains.CORE
    assert not core.ready()
    monkeypatch.setenv("SKIPPY_CORE_API_KEY", "sk-core-SECRET")
    assert not core.ready()                                         # a key with nowhere to send it
    monkeypatch.setenv("SKIPPY_CORE_URL", "https://core.example/")
    assert core.ready() and core.base_url() == "https://core.example"
    monkeypatch.setenv("SKIPPY_CORE_URL", "unset")                  # bootstrap's placeholder for a skipped secret
    assert not core.ready()
    assert "SECRET" not in json.dumps(brains.modes(True))           # the mode key never sees a key
