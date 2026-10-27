"""Purple Skippy's compressed headers (tools/purple_headers.py): under their caps, carrying the rules that matter, and identical to what the
generator makes now, so the committed files can't drift from their sources."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("purple_headers", ROOT / "tools" / "purple_headers.py")
ph = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ph)

# Phrases each header must keep (the rules the Captain named for purple; drop one and this fails).
DEV_MUST = [
    "Bash first, even on Windows", "§20",
    "Deploy and verify after every runtime change", "1-3 sentences",
    "Push straight to main, no PRs", "§17",
    "`ask_mentat`", "source cited", "§21",
    "Ready means ready", "§24",
    "No secrets in chat",
    "Kill processes by PID only",
    "Never work around a permission or classifier denial",
    "Lane rule", "only in this conversation's folder",
    "skippy-pc", "skippy-core", "makerportal.live", "D:/trunk/git", "/mnt/pc/trunk",
    "ssh skippy-core", "cd.py ops <command> <args>", "cd.py deploy|rollback",
    "web_search", "ask_mentat", "mentat_tool", "mentat_tools", "read_pdf", "browse", "home_control",
    "docker-redeploy", "ci-tests",
    "/docker-redeploy/SKILL.md", "/ci-tests/SKILL.md", "no Skill tool",
    "knowledge dump", "docs/pivot.md",
]
STM_MUST = [
    "Skippy the Magnificent", "Captain", "Complain first, deliver second", "No apologies", "Heh heh",
    "Never lie, cheat or steal", "spoken aloud", "1-3 sentences", "No markdown",
]


def _committed(name):
    return (ph.OUT / name).read_text(encoding="utf-8")


def test_headers_stay_under_their_caps():
    out = ph.render()
    assert ph.tokens(out["dev.md"]) <= ph.DEV_CAP
    assert ph.tokens(out["stm.md"]) <= ph.STM_CAP


def test_dev_header_keeps_every_required_rule():
    text = ph.render()["dev.md"]
    missing = [m for m in DEV_MUST if m not in text]
    assert not missing, f"dev.md lost: {missing}"


def test_stm_header_keeps_the_voice_and_speech_rules():
    text = ph.render()["stm.md"]
    missing = [m for m in STM_MUST if m not in text]
    assert not missing, f"stm.md lost: {missing}"


def test_committed_headers_match_the_generator():
    for name, text in ph.render().items():
        assert _committed(name) == text, f"services/purple-headers/{name} is stale: run python tools/purple_headers.py"


def test_every_anchor_is_still_in_its_source():
    # repo sources always; the dev-root ones (PLATINUM_RULES.md, the dev-root CLAUDE.md) only where they exist (not in CI)
    assert ph.check_anchors() == []


def test_extracted_lists_come_from_the_repo():
    assert ph.purple_tool_names()[:1] and "ask_mentat" in ph.purple_tool_names()
    assert ph.house_tool_names() == ["home_devices", "home_state", "home_control"]
    assert "restart-game" in ph.ops_commands()
    assert "ci-tests" in ph.repo_skills()


def test_every_skill_says_when_and_where_and_the_repo_ones_exist():
    text = ph.render()["dev.md"]
    for name in ph.repo_skills() + ph.USER_SKILLS:
        assert f"/{name}/SKILL.md" in text and name in ph.SKILL_WHEN
    for name in ph.repo_skills():                                  # the user-scope ones live only on skippy-pc, not in CI
        assert (ph.ROOT / ".claude/skills" / name / "SKILL.md").is_file()


def test_a_new_skill_without_a_when_stops_the_build(monkeypatch):
    monkeypatch.setattr(ph, "USER_SKILLS", ph.USER_SKILLS + ["brand-new"])
    try:
        ph.skill_lines()
        raise AssertionError("should have stopped")
    except SystemExit as e:
        assert "brand-new" in str(e)


def test_a_missing_anchor_is_reported(monkeypatch):
    monkeypatch.setattr(ph, "ANCHORS", [("repo:docs/pivot.md", "a phrase nobody wrote")])
    assert ph.check_anchors() == ["repo:docs/pivot.md: 'a phrase nobody wrote' not found"]


def test_an_absent_dev_root_source_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "DEV_ROOT", tmp_path)
    monkeypatch.setattr(ph, "ANCHORS", [("dev:PLATINUM_RULES.md", "## §20 — Bash First")])
    assert ph.check_anchors() == []
