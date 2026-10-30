"""Projects: the private Skippy works on any of the Captain's repos (projects.json), each in its OWN clone, never his
checkout, one run at a time across all of them, and each conversation keeps to its project (its own Claude session,
its own prompt)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import bridge
import projects as projects_mod
import workshop_engine as engine
from bridge_testkit import calls, finish, wait_for


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def origin_repo(path: Path, marker: str) -> Path:
    path.mkdir()
    git("init", "-q", cwd=path)
    (path / "README.md").write_text(marker, encoding="utf-8")
    git("add", "README.md", cwd=path)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "first", cwd=path)
    return path


def registry(tmp_path, shop) -> dict:
    """SkippyTel (the shop's own clone) and a second project, `other`, whose clone does not exist yet."""
    other_origin = origin_repo(tmp_path / "other-origin", "other project")
    primary = tmp_path / "other-primary"                 # the Captain's checkout of `other`: a real clone with his work in it
    git("clone", "-q", str(other_origin), str(primary), cwd=tmp_path)
    (primary / "his-work-in-progress.txt").write_text("not committed yet", encoding="utf-8")
    return {
        "skippytel": projects_mod.Project("skippytel", "SkippyTel", "aaronsrhodes2/skippy-tel", "main", shop.primary,
                                          shop.repo, "skippytel", str(shop.origin)),
        "other": projects_mod.Project("other", "Other thing", "aaronsrhodes2/other", "main", str(primary),
                                      str(tmp_path / "other-workshop"), "", str(other_origin)),
    }


@pytest.fixture
def multi(shop, tmp_path):
    w = engine.Workshop(shop.claude_cmd, sessions=str(tmp_path / "sessions.json"), projects=registry(tmp_path, shop))
    w.record = shop.record
    return w


def snapshot(path: Path) -> dict:
    return {str(p.relative_to(path)): (p.stat().st_mtime_ns, p.read_bytes()) for p in path.rglob("*") if p.is_file()}


# ── the registry ────────────────────────────────────────────────────────────
def test_the_committed_registry_loads_and_keeps_every_clone_apart_from_the_captains_checkouts():
    found = projects_mod.load()
    assert "skippytel" in found and found["skippytel"].deploy == "skippytel"
    assert found["skippytel"].clone.endswith("SkippyTel-workshop") and found["skippytel"].primary.endswith("development/SkippyTel")
    assert not any("zphone" in p.id or "zPhone" in p.primary for p in found.values())        # shelved: never offered
    for p in found.values():
        assert p.clone != p.primary and p.clone.endswith("-workshop") and p.repo.startswith("aaronsrhodes2/")
        assert p.deploy in ("", "skippytel")
    assert len({p.repo for p in found.values()}) == len(found)                               # one project per GitHub repo


def base(**over):
    item = {"id": "skippytel", "name": "SkippyTel", "repo": "a/b", "branch": "main", "primary": "D:/dev/SkippyTel",
            "clone": "D:/dev/SkippyTel-workshop", "deploy": "skippytel"}
    item.update(over)
    return item


@pytest.mark.parametrize("bad, why", [
    (base(clone="D:/dev/SkippyTel"), "separate"),                         # the clone IS his checkout
    (base(clone="d:\\DEV\\skippytel\\"), "separate"),                     # ... spelled the Windows way
    (base(clone="D:/dev/SkippyTel/work"), "separate"),                    # inside it
    (base(primary="D:/dev/SkippyTel-workshop/x"), "separate"),            # around it
    (base(id="Bad Id"), "bad project id"),
    (base(clone="relative/path"), "absolute"),
    (base(primary="D:/dev/x\nmore"), "one line"),
    (base(repo="no-slash"), "owner/name"),
    (base(branch="a..b"), "bad branch"),
    (base(deploy="rm -rf"), "unknown deploy"),
])
def test_a_bad_entry_is_refused_at_load(bad, why):
    with pytest.raises(projects_mod.ProjectError, match=why):
        projects_mod.parse({"projects": [bad]})


def test_the_registry_as_a_whole_is_checked():
    one = base()
    with pytest.raises(projects_mod.ProjectError, match="twice"):
        projects_mod.parse({"projects": [one, one]})
    with pytest.raises(projects_mod.ProjectError, match="share one clone"):
        projects_mod.parse({"projects": [one, base(id="x", primary="D:/dev/x")]})
    with pytest.raises(projects_mod.ProjectError, match="overlaps"):        # one project's clone is another's checkout
        projects_mod.parse({"projects": [one, base(id="x", primary="D:/dev/SkippyTel-workshop2",
                                                   clone="D:/dev/SkippyTel/inner")]})
    with pytest.raises(projects_mod.ProjectError, match="must have 'skippytel'"):
        projects_mod.parse({"projects": [base(id="x")]})
    with pytest.raises(projects_mod.ProjectError):
        projects_mod.parse({"projects": []})
    assert list(projects_mod.parse({"projects": [one], "_comment": "ignored"})) == ["skippytel"]


def test_what_the_portal_is_told_has_no_paths(multi):
    listed = multi.projects()
    assert [p["id"] for p in listed] == ["skippytel", "other"]
    assert all(set(p) == {"id", "name", "repo", "branch"} for p in listed)
    assert not any(":" in v or "\\" in v or v.startswith("/") for p in listed for v in p.values())


def test_health_carries_the_projects(door, shop):
    status, body = door("GET", "/health", token=None)
    assert status == 200 and body["workshop"]["projects"] == [
        {"id": "skippytel", "name": "SkippyTel", "repo": "aaronsrhodes2/skippy-tel", "branch": "main"}]


# ── a run in another project ────────────────────────────────────────────────
def test_a_run_in_another_project_clones_it_works_there_and_never_touches_the_captains_checkout(multi, tmp_path):
    primary = Path(multi.project("other").primary)
    before = snapshot(primary)
    run = finish(multi.start("aaaaaaaa", "conv-12345678", "fix the readme", project="other"))
    assert run.ok is True and run.project == "other"
    assert run.events[0] == {"t": "tool", "name": "git", "detail": "cloning aaronsrhodes2/other for the first time"}
    clone = Path(multi.project("other").clone)
    assert (clone / ".git").exists() and (clone / "README.md").read_text(encoding="utf-8") == "other project"
    seen = calls(multi)[-1]
    assert Path(seen["cwd"]).resolve() == clone.resolve()                       # claude runs in the clone ...
    assert seen["argv"][seen["argv"].index("--add-dir") + 1] == str(primary)    # ... and may only read his checkout
    assert snapshot(primary) == before                                          # not one byte of it changed
    assert "his-work-in-progress.txt" in git("status", "--porcelain", "--untracked-files=all", cwd=primary)


def test_the_default_project_is_skippytel_and_an_unknown_one_is_refused(multi):
    with pytest.raises(ValueError, match="unknown project"):
        multi.start("aaaaaaaa", "conv-12345678", "hi", project="zphone")
    assert multi.get("aaaaaaaa") is None and not multi.busy()
    run = finish(multi.start("bbbbbbbb", "conv-12345678", "hi"))
    assert run.project == "skippytel"
    assert Path(calls(multi)[-1]["cwd"]).resolve() == Path(multi.repo).resolve()


def test_one_run_at_a_time_across_all_projects(multi, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")
    first = multi.start("aaaaaaaa", "conv-12345678", "long one", project="skippytel")
    with pytest.raises(engine.Busy):
        multi.start("bbbbbbbb", "conv-87654321", "meanwhile elsewhere", project="other")
    assert multi.get("bbbbbbbb") is None
    multi.cancel("aaaaaaaa")
    finish(first)
    monkeypatch.setenv("FAKE_MODE", "ok")
    assert finish(multi.start("bbbbbbbb", "conv-87654321", "now", project="other")).ok is True


def test_each_project_has_its_own_rules_file_and_rules(multi):
    own, other = multi.prompt("skippytel"), multi.prompt("other")
    assert multi.prompt_path("skippytel").name == engine.PROMPT_NAME
    assert multi.prompt_path("other").name == ".skippy-workshop-prompt-other.txt"
    assert "core/ci" in own and "home-reporter" in own and "CLAUDE.md" in own
    assert "core/ci" not in other and "home-reporter" not in other and "docker compose" not in other
    assert "follow it" in other and "not deployed" in other and "Other thing" in other
    assert multi.project("other").clone in other and multi.project("other").primary in other
    for text in (own, other):
        assert "{{" not in text and "{repo}" not in text and "{branch}" not in text
        assert "## Guardrails" in text and "Never print or paste a secret" in text
    assert "services/claude-bridge/" in own.split("## Guardrails")[1]
    assert "services/claude-bridge/" not in other.split("## Guardrails")[1]
    finish(multi.start("aaaaaaaa", "conv-12345678", "x", project="other"))
    seen = calls(multi)[-1]
    assert seen["prompt_file"] == other
    assert seen["argv"][seen["argv"].index("--append-system-prompt-file") + 1] == str(multi.prompt_path("other"))


def test_a_conversation_resumes_its_own_projects_session_only(multi):
    finish(multi.start("aaaaaaaa", "conv-12345678", "one", project="skippytel"))
    finish(multi.start("bbbbbbbb", "conv-12345678", "two", project="skippytel"))
    assert calls(multi)[-1]["argv"][-2:] == ["--resume", "sess-1"]
    stored = json.loads(Path(multi.sessions_path).read_text(encoding="utf-8"))
    assert stored == {"conv-12345678": "sess-1"}                          # SkippyTel keeps the old key: old chats go on
    finish(multi.start("cccccccc", "conv-12345678", "three", project="other"))
    assert "--resume" not in calls(multi)[-1]["argv"]                     # never another project's session
    assert json.loads(Path(multi.sessions_path).read_text(encoding="utf-8"))["other:conv-12345678"] == "sess-1"


def test_the_project_is_listed_saved_and_restored(multi, tmp_path):
    finish(multi.start("aaaaaaaa", "conv-12345678", "x", project="other"))
    assert multi.listing()[0]["project"] == "other"
    again = engine.Workshop(multi.claude_cmd, sessions=multi.sessions_path, projects=multi._projects)
    assert again.load() == 1 and again.get("aaaaaaaa").project == "other"


def test_the_door_passes_the_project_and_refuses_an_unknown_one(door, shop):
    assert door("POST", "/workshop", {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "hi",
                                      "project": "nope"})[0] == 400
    assert shop.get("aaaaaaaa") is None
    assert door("POST", "/workshop", {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "hi",
                                      "project": "skippytel"})[0] == 200
    assert finish(shop.get("aaaaaaaa")).project == "skippytel"


def test_an_untracked_file_never_stops_the_pull_but_a_tracked_change_does(multi, tmp_path):
    # 2026-10-29: the agent's own `.purple-out/` scratch counted as "uncommitted changes" and the clone sat 7 commits behind main.
    finish(multi.start("aaaaaaaa", "conv-12345678", "x", project="other"))
    clone = Path(multi.project("other").clone)
    origin = Path(multi.project("other").url)             # a plain repo standing in for the house git: main moves on
    (origin / "NEW.md").write_text("newer", encoding="utf-8")
    git("add", "NEW.md", cwd=origin)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "newer", cwd=origin)
    (clone / ".purple-out").mkdir()
    (clone / ".purple-out" / "scratch.txt").write_text("tool output", encoding="utf-8")
    run = finish(multi.start("bbbbbbbb", "conv-12345678", "y", project="other"))
    assert not any("not pulling" in (e.get("text") or "") for e in run.events) and (clone / "NEW.md").exists()
    (clone / "README.md").write_text("half-done edit", encoding="utf-8")
    run = finish(multi.start("cccccccc", "conv-12345678", "z", project="other"))
    assert any("not pulling" in (e.get("text") or "") for e in run.events)
    assert (clone / "README.md").read_text(encoding="utf-8") == "half-done edit"


def test_a_clone_path_that_holds_something_else_is_never_cloned_into(multi):
    clone = Path(multi.project("other").clone)
    clone.mkdir()
    (clone / "somebody's.txt").write_text("keep me", encoding="utf-8")
    run = finish(multi.start("aaaaaaaa", "conv-12345678", "x", project="other"))
    assert run.ok is False and "isn't a git clone" in run.error
    assert (clone / "somebody's.txt").read_text(encoding="utf-8") == "keep me" and not (clone / ".git").exists()


def test_a_broken_registry_still_lets_the_bridge_start_with_skippytel(monkeypatch, capsys):
    def broken(path=None):
        raise projects_mod.ProjectError("bad")
    monkeypatch.setattr(projects_mod, "load", broken)
    assert bridge._registry() is None
    assert "only SkippyTel is offered" in capsys.readouterr().err
