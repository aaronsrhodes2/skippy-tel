"""The Captain removed the two Claude Code tiers on 2026-09-30 ("trust the awesomeness" = full access, "solid gold" = read-only
plan mode): the private Skippy on the portal's /me has full access, so skippy core no longer hands anything to Claude Code.
These tests keep the verbs from coming back by accident."""
import importlib.util

import pytest

from app.routes import intent


def test_no_tier_hands_work_to_claude_code():
    assert not {t.id for t in intent.TIERS} & {"trust_awesomeness", "solid_gold"}
    assert not any("awesomeness" in trig or "solid gold" in trig for t in intent.TIERS for trig in t.triggers)


def test_the_bridge_client_is_gone():
    assert importlib.util.find_spec("app.clients.claude_bridge") is None
    assert not hasattr(intent, "claude_bridge")


@pytest.mark.parametrize("phrase", ["trust the awesomeness list the files", "Solid Gold, read the repo", "please solid gold this"])
def test_the_old_phrases_are_ordinary_text_for_the_default_model(phrase):
    tier, text = intent._resolve_tier(phrase)
    assert tier is None and text == phrase                       # None = the default tier (Ollama); nothing is stripped or routed
