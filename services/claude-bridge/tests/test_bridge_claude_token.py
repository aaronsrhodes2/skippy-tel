"""The year-long Claude token (tools/claude_token.sh puts it in the vault): every Claude run and the login check get it, a Core run never does,
and /health counts down its year."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import workshop_engine as we  # noqa: E402


def vault(tmp_path, *lines):
    f = tmp_path / ".env"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (str(f),)


def test_the_token_comes_from_the_environment_first_then_the_vault(tmp_path, monkeypatch):
    files = vault(tmp_path, "OTHER=1", "CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat-vault'")
    assert we.vault_value("CLAUDE_CODE_OAUTH_TOKEN", {}, files) == "sk-ant-oat-vault"
    assert we.vault_value("CLAUDE_CODE_OAUTH_TOKEN", {"CLAUDE_CODE_OAUTH_TOKEN": "from-env"}, files) == "from-env"
    assert we.vault_value("CLAUDE_CODE_OAUTH_TOKEN", {}, (str(tmp_path / "missing"),)) == ""


def test_child_env_carries_it_and_never_the_doors_key(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "ENV_FILES", vault(tmp_path, "CLAUDE_CODE_OAUTH_TOKEN=tok-123"))
    env = we.child_env({"SKIPPY_HOME_TOKEN": "door", "PATH": "x"})
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-123" and "SKIPPY_HOME_TOKEN" not in env
    monkeypatch.setattr(we, "ENV_FILES", (str(tmp_path / "none"),))
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in we.child_env({"PATH": "x"})               # no token anywhere: claude's own sign-in, as before


def test_the_year_counts_down_and_says_nothing_without_a_token(tmp_path):
    files = vault(tmp_path, "CLAUDE_CODE_OAUTH_TOKEN=t", "CLAUDE_CODE_OAUTH_TOKEN_SET=2026-10-27")
    assert we.token_days_left({}, files, today=dt.date(2026, 10, 27)) == 365
    assert we.token_days_left({}, files, today=dt.date(2027, 9, 27)) == 30
    assert we.token_days_left({}, vault(tmp_path, "CLAUDE_CODE_OAUTH_TOKEN_SET=2026-10-27"), today=dt.date(2026, 10, 27)) is None
    assert we.token_days_left({}, vault(tmp_path, "CLAUDE_CODE_OAUTH_TOKEN=t", "CLAUDE_CODE_OAUTH_TOKEN_SET=junk")) is None


def test_a_core_run_never_gets_the_captains_claude_token(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "child_env", lambda base=None: {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "PATH": "x"})
    shop = we.Workshop.__new__(we.Workshop)
    shop.sessions_path = str(tmp_path / "sessions.json")
    monkeypatch.setattr(we.Workshop, "_core_settings", lambda self: ("http://core", "key"))
    monkeypatch.setattr(we.Workshop, "_share_claude_setup", lambda self, config: None)
    env = shop.core_env()
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and env["ANTHROPIC_API_KEY"] == "key"
