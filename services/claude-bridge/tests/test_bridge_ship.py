"""Proof it's live, the bridge's half: a run that pushed says so with ONE `ship` event (project, repo, branch, sha,
subject, workflow), emitted before the run reads as done so the relay delivers it with the end. A commit that wasn't
pushed, or no commit, says nothing."""
import subprocess
from pathlib import Path

import pytest

import conversation_log as cl
import projects as projects_mod
import workshop_engine as engine
from bridge_testkit import finish, wait_for


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def other(shop, tmp_path):
    """A Workshop whose `other` project has an origin that accepts pushes (bare), branch main, a deploy workflow."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    (seed / ".github" / "workflows").mkdir(parents=True)
    (seed / ".github" / "workflows" / "deploy.yml").write_text("on: push\n", encoding="utf-8")
    git("add", ".", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "first", cwd=seed)
    origin = tmp_path / "other-origin.git"
    git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)
    reg = {"skippytel": projects_mod.Project("skippytel", "SkippyTel", "aaronsrhodes2/skippy-tel", "main", shop.primary,
                                             shop.repo, "skippytel", str(shop.origin)),
           "other": projects_mod.Project("other", "Other", "aaronsrhodes2/other", "main", str(tmp_path / "other-primary"),
                                         str(tmp_path / "other-workshop"), "", str(origin))}
    w = engine.Workshop(shop.claude_cmd, sessions=str(tmp_path / "sessions.json"), projects=reg)
    w.origin = origin
    return w


def ships(run):
    return [e for e in run.events if e.get("t") == "ship"]


def test_a_run_that_pushed_ends_with_one_ship_event_before_it_reads_as_done(other, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ship")
    run = other.start("aaaaaaaa", "conv-12345678", "make it green", project="other")
    finish(run)
    [ship] = ships(run)
    head = git("rev-parse", "HEAD", cwd=other.project("other").clone)
    assert ship == {"t": "ship", "project": "other", "repo": "aaronsrhodes2/other", "branch": "main", "sha": head,
                    "subject": "Make the tile green [redacted]", "workflow": "deploy.yml"}
    assert git("rev-parse", "main", cwd=other.origin) == head                       # it really is on origin
    assert run.events[-1] == ship and run.ok is True                               # the last event, part of the final read
    assert cl.ship_event(ship) == ship                                             # the conversation log keeps it as is


def test_a_commit_that_was_not_pushed_or_no_commit_says_nothing(other, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "commit")
    assert ships(finish(other.start("aaaaaaaa", "conv-12345678", "x", project="other"))) == []
    monkeypatch.setenv("FAKE_MODE", "ok")
    assert ships(finish(other.start("bbbbbbbb", "conv-12345678", "y", project="other"))) == []


def test_a_run_that_pushed_and_then_failed_still_shows_the_push(other, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ship-fail")
    run = finish(other.start("aaaaaaaa", "conv-12345678", "x", project="other"))
    assert run.ok is False and len(ships(run)) == 1


def test_a_broken_ship_check_never_stops_the_run_from_ending(other, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ship")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("git", 30)
    run = other.start("aaaaaaaa", "conv-12345678", "x", project="other")
    monkeypatch.setattr(other, "_head", boom)
    finish(run)
    assert run.done and ships(run) == []


def test_the_turns_index_keeps_the_ship_event_and_the_markdown_says_it(other, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ship")
    run = finish(other.start("aaaaaaaa", "conv-12345678", "x", project="other", who="c@example.com"))
    # the index is written in the run's thread just after it ends
    wait_for(lambda: other.convlog._written.get("aaaaaaaa") is None, what="the turns index to be written")
    turn = other.conversation("conv-12345678")["turns"][0]
    assert ships(run)[0] in turn["events"]
    md = next(Path(other.convlog.root).glob("*_conv-12345678.md")).read_text(encoding="utf-8")
    assert f"shipped `{ships(run)[0]['sha']}`" in md


@pytest.mark.parametrize("bad", [
    {"sha": "abc"}, {"sha": "G" * 40}, {"repo": "evil"}, {"project": "Bad"}, {"workflow": "rm.yml"},
    {"subject": 7}, {"branch": "a b"},
])
def test_a_ship_event_out_of_shape_is_dropped(bad):
    good = {"t": "ship", "project": "p", "repo": "a/b", "branch": "main", "sha": "a" * 40, "subject": "s", "workflow": ""}
    assert cl.ship_event(good) == good
    assert cl.ship_event({**good, **bad}) is None
