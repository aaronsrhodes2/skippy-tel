"""Folders opened in place and the brain a run thinks with (2026-10-20): the Captain picks any folder directly under the
development root and the agent works RIGHT IN IT (no clone, git is the undo); a run thinks with Claude (Sonnet) or with
Skippy Core's coder through its Claude-compatible API, in its own Claude Code config folder, and a conversation's
sessions never cross between the two."""
import subprocess
from pathlib import Path

import pytest

import projects as projects_mod
import workshop_engine as engine
from bridge_testkit import calls, finish


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "dev"
    for name in ("My Game", "notes", ".hidden", "_moved_from_C", "thing-workshop"):
        (r / name).mkdir(parents=True)
    (r / "My Game" / "game.py").write_text("print('hi')\n", encoding="utf-8")
    return r


@pytest.fixture
def shopf(shop, root, tmp_path):
    w = engine.Workshop(shop.claude_cmd, sessions=str(tmp_path / "sessions.json"), dev_root=str(root))
    w.record = shop.record
    return w


def test_only_plain_direct_children_of_the_root_are_folders(root):
    found = projects_mod.folders(root)
    assert sorted(found) == ["f-my-game", "f-notes"]                      # no hidden, _..., or -workshop clones
    assert found["f-my-game"].inplace and found["f-my-game"].clone == found["f-my-game"].primary


def test_folders_are_offered_only_when_the_bridge_names_a_root(shop, shopf, tmp_path):
    assert any(p["id"] == "f-notes" and p["name"] == "notes · folder" for p in shopf.projects())
    import json
    told = json.dumps(shopf.projects())
    assert "dev" not in [p["name"] for p in shopf.projects()] and "My Game\\" not in told and str(shopf.dev_root) not in told   # never a path
    bare = engine.Workshop(shop.claude_cmd, sessions=str(tmp_path / "s2.json"))
    assert not any(p["id"].startswith("f-") for p in bare.projects())
    with pytest.raises(ValueError, match="unknown project"):
        bare.start("aaaaaaaa", "conv-12345678", "hi", project="f-notes")


def test_a_folder_run_works_in_the_folder_itself_with_no_clone(shop, shopf, root):
    finish(shopf.start("aaaaaaaa", "conv-12345678", "fix the game", project="f-my-game"))
    (call,) = calls(shop)
    assert Path(call["cwd"]).resolve() == (root / "My Game").resolve()   # right in the folder
    assert not (root / "My Game-workshop").exists() and not (root.parent / "My Game-workshop").exists()


def test_a_folder_that_is_a_registered_checkout_keeps_that_projects_deploy_rules(shop, root, tmp_path):
    checkout = root / "SkippyTel"
    checkout.mkdir()
    reg = {"skippytel": projects_mod.Project("skippytel", "SkippyTel", "aaronsrhodes2/skippy-tel", "main", str(checkout),
                                             str(tmp_path / "SkippyTel-workshop"), "skippytel", str(shop.origin))}
    w = engine.Workshop(shop.claude_cmd, sessions=str(tmp_path / "s3.json"), projects=reg, dev_root=str(root))
    p = w.project("f-skippytel")
    assert p.inplace and p.deploy == "skippytel" and p.repo == "aaronsrhodes2/skippy-tel" and p.clone == p.primary


def test_a_core_run_uses_the_coder_its_own_config_folder_and_the_key(shop, shopf, monkeypatch):
    monkeypatch.setenv("SKIPPY_CORE_URL", "https://core.example/")
    monkeypatch.setenv("SKIPPY_CORE_API_KEY", "sk-core-test")
    monkeypatch.setattr(engine.Workshop, "core_ready", lambda self: True)     # (no real skippy-core to answer the probe)
    finish(shopf.start("aaaaaaaa", "conv-12345678", "hi", project="f-notes", brain="core"))
    (call,) = calls(shop)
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == "coder" and "--effort" not in argv
    for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CONFIG_DIR",
                 "CLAUDE_CODE_MAX_CONTEXT_TOKENS"):
        assert name in call["env"], name
    assert "SKIPPY_HOME_TOKEN" not in call["env"]


def test_a_claude_run_is_unchanged(shop, shopf, monkeypatch):
    for v in ("ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"):          # (a Claude Code shell running these tests sets them)
        monkeypatch.delenv(v, raising=False)
    finish(shopf.start("aaaaaaaa", "conv-12345678", "hi", project="f-notes"))
    (call,) = calls(shop)
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == engine.MODEL and argv[argv.index("--effort") + 1] == engine.EFFORT
    assert "CLAUDE_CONFIG_DIR" not in call["env"] and "ANTHROPIC_BASE_URL" not in call["env"]


def test_core_is_refused_until_skippy_core_is_configured_and_a_stranger_brain_always(shop, shopf, monkeypatch):
    for v in ("SKIPPY_CORE_URL", "SKIPPY_CORE_API_KEY", "WORKSHOP_CORE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(engine, "VAULT_ENV", str(Path(shopf.sessions_path).with_name("no-vault.env")))
    with pytest.raises(ValueError, match="Skippy Core isn't reachable"):
        shopf.start("aaaaaaaa", "conv-12345678", "hi", brain="core")
    with pytest.raises(ValueError, match="unknown brain"):
        shopf.start("bbbbbbbb", "conv-12345678", "hi", brain="gpt-9")


def test_the_two_brains_never_share_a_conversations_session():
    assert engine.Workshop.session_key("skippytel", "c1") == "c1"
    assert engine.Workshop.session_key("skippytel", "c1", "core") == "c1@core"
    assert engine.Workshop.session_key("f-notes", "c1", "core") == "f-notes:c1@core"


def test_core_is_ready_only_when_skippy_core_actually_answers(shop, shopf, monkeypatch):
    import urllib.request
    monkeypatch.setenv("SKIPPY_CORE_URL", "https://core.example")
    monkeypatch.setenv("SKIPPY_CORE_API_KEY", "k")
    calls_made = []

    class Answer:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_open(req, timeout):
        calls_made.append(req.full_url)
        if fake_open.down:
            raise OSError("no route")
        return Answer()
    fake_open.down = True
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    assert shopf.core_ready() is False                          # configured but unreachable (the Funnel not open)
    fake_open.down = False
    assert shopf.core_ready() is False                          # remembered for a minute
    shopf._core_probe = None
    assert shopf.core_ready() is True and calls_made[-1] == "https://core.example/v1/models"


def test_an_in_place_prompt_says_to_work_right_in_the_folder_and_never_discard_his_work(shopf):
    text = shopf.prompt("f-my-game")
    assert text.startswith("## This conversation works IN PLACE") and "My Game" in text
    assert "git status" in text and "git reset --hard" in text and "{repo}" not in text
    assert not shopf.prompt().startswith("## This conversation works IN PLACE")      # a clone project's prompt is unchanged


def test_a_core_run_gets_the_captains_skills_and_plugins_but_never_his_login(shop, shopf, tmp_path, monkeypatch):
    import json as _json
    home = tmp_path / "dot-claude"
    (home / "skills" / "docker-redeploy").mkdir(parents=True)
    (home / "skills" / "docker-redeploy" / "SKILL.md").write_text("redeploy safely", encoding="utf-8")
    (home / "plugins").mkdir()
    (home / "plugins" / "installed.json").write_text("{}", encoding="utf-8")
    (home / ".credentials.json").write_text('{"token": "SECRET"}', encoding="utf-8")
    (home / "settings.json").write_text(_json.dumps({"enabledPlugins": {"a@b": True}, "theme": "dark", "env": {"X": "1"}}), encoding="utf-8")
    monkeypatch.setenv("WORKSHOP_CLAUDE_HOME", str(home))
    monkeypatch.setenv("SKIPPY_CORE_URL", "https://core.example")
    monkeypatch.setenv("SKIPPY_CORE_API_KEY", "k")
    env = shopf.core_env()
    config = Path(env["CLAUDE_CONFIG_DIR"])
    assert (config / "skills" / "docker-redeploy" / "SKILL.md").read_text(encoding="utf-8") == "redeploy safely"
    assert (config / "plugins" / "installed.json").is_file()
    assert not (config / ".credentials.json").exists() and "SECRET" not in "".join(p.read_text(errors="ignore") for p in config.rglob("*") if p.is_file())
    settings = _json.loads((config / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"] == {"a@b": True} and "theme" not in settings
    assert settings["env"] == {"X": "1", "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"}          # his env, plus the prompt-cache fix
