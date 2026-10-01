"""tools/ci_changes.py: deploy.yml's answer to "does Mentat need testing and deploying on this run?".

The rule under test: "false" only when it is PROVEN that nothing Mentat is built from differs from the last successful deploy;
every doubt is "true". Real git against a throwaway origin and clone (shallow, as actions/checkout leaves it), plus a fake seam
for the failures that are hard to stage.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_changes  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def run(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class Repo:
    """origin + a depth-1 clone of its tip, like the runner's checkout."""

    def __init__(self, tmp: Path):
        self.origin = tmp / "origin"
        self.origin.mkdir()
        run(self.origin, "init", "-q", "-b", "main")
        run(self.origin, "config", "uploadpack.allowAnySHA1InWant", "true")     # what GitHub's servers allow: fetch by SHA
        self.clone_dir = tmp / "clone"

    def commit(self, files: dict[str, str]) -> str:
        for name, text in files.items():
            f = self.origin / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        run(self.origin, "add", "-A")
        run(self.origin, "commit", "-q", "-m", "c")
        return run(self.origin, "rev-parse", "HEAD")

    def checkout_tip(self) -> None:
        if self.clone_dir.exists():
            shutil.rmtree(self.clone_dir, onerror=lambda f, p, e: (Path(p).chmod(0o700), f(p)))
        # file:// so --depth is honoured (a plain path would be a full local clone)
        subprocess.run(["git", "clone", "-q", "--depth=1", self.origin.as_uri(), str(self.clone_dir)], check=True,
                       capture_output=True, env={**os.environ, **GIT_ENV})

    def decide(self, event: str, base: str, monkeypatch):
        monkeypatch.chdir(self.clone_dir)
        return ci_changes.decide(event, base)


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path)
    r.base = r.commit({"mentat/app.py": "1", "portal/app.py": "1", "docs/a.md": "1", ".github/workflows/deploy.yml": "1"})
    return r


def test_a_change_outside_mentat_skips_it(repo, monkeypatch):
    repo.commit({"portal/app.py": "2", "docs/a.md": "2", "tools/x.py": "1"})
    repo.checkout_tip()
    run_it, reason = repo.decide("push", repo.base, monkeypatch)
    assert run_it is False and "none that Mentat is built from" in reason


@pytest.mark.parametrize("path", ["mentat/app.py", "mentat/requirements.txt", "mentat/tests/test_new.py", "mentat/Dockerfile",
                                  ".github/workflows/deploy.yml", ".github/workflows/run-tests.yml", ".dockerignore"])
def test_a_change_to_anything_mentat_is_built_from_runs_it(repo, monkeypatch, path):
    repo.commit({"portal/app.py": "2"})
    repo.commit({path: "changed"})
    repo.checkout_tip()
    run_it, reason = repo.decide("push", repo.base, monkeypatch)
    assert run_it is True and path in reason


def test_a_mentat_change_in_an_EARLIER_push_still_counts_because_the_base_is_the_last_green_deploy(repo, monkeypatch):
    """The reason the base is not the push's `before`: the push that changed Mentat was cancelled or red."""
    repo.commit({"mentat/app.py": "2"})             # push A: cancelled / red, never deployed
    repo.commit({"portal/app.py": "9"})             # push B: portal only
    repo.checkout_tip()
    run_it, _ = repo.decide("push", repo.base, monkeypatch)       # base = the last SUCCESSFUL deploy, before A
    assert run_it is True


def test_an_identical_tree_skips_it(repo, monkeypatch):
    repo.checkout_tip()
    run_it, reason = repo.decide("push", repo.base, monkeypatch)
    assert run_it is False and "no file differs" in reason


@pytest.mark.parametrize("event", ["workflow_dispatch", "", "schedule"])
def test_anything_but_a_push_runs_everything(repo, monkeypatch, event):
    repo.checkout_tip()
    assert repo.decide(event, repo.base, monkeypatch)[0] is True


@pytest.mark.parametrize("base", ["", "0" * 40])
def test_no_earlier_successful_deploy_runs_everything(repo, monkeypatch, base):
    repo.checkout_tip()
    run_it, reason = repo.decide("push", base, monkeypatch)
    assert run_it is True and "no earlier successful deploy" in reason


def test_a_base_that_cannot_be_fetched_runs_everything(repo, monkeypatch):
    repo.checkout_tip()
    run_it, reason = repo.decide("push", "1" * 40, monkeypatch)       # e.g. a force push removed it
    assert run_it is True and "could not fetch" in reason


def seam(fetch_rc=0, diff_rc=0, diff_out="", raises=None):
    def fake(*args):
        if raises:
            raise raises
        if args[0] == "fetch":
            return SimpleNamespace(returncode=fetch_rc, stdout="", stderr="fatal: nope\nsecond line")
        return SimpleNamespace(returncode=diff_rc, stdout=diff_out, stderr="boom")
    return fake


def test_a_failing_diff_a_timeout_or_a_missing_git_run_everything():
    assert ci_changes.decide("push", "a" * 40, git_run=seam(diff_rc=128))[0] is True
    assert ci_changes.decide("push", "a" * 40, git_run=seam(raises=subprocess.TimeoutExpired("git", 1)))[0] is True
    assert ci_changes.decide("push", "a" * 40, git_run=seam(raises=FileNotFoundError("git")))[0] is True


def test_the_seam_says_skip_only_when_the_diff_is_clean_of_mentat_inputs():
    assert ci_changes.decide("push", "a" * 40, git_run=seam(diff_out="portal/app.py\ndocs/x.md\n"))[0] is False
    assert ci_changes.decide("push", "a" * 40, git_run=seam(diff_out="portal/app.py\nmentat/wva.py\n"))[0] is True
    assert ci_changes.decide("push", "a" * 40, git_run=seam(diff_out="mentat_notes.md\n"))[0] is False      # a prefix is a folder, not a name


def test_the_output_is_two_github_output_lines_and_exits_zero_even_when_git_blows_up(capsys, monkeypatch):
    monkeypatch.setattr(ci_changes, "decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert ci_changes.main(["--event", "push", "--base", "a" * 40]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "mentat=true" and out[1].startswith("reason=") and len(out) == 2


def test_a_multi_line_git_error_cannot_break_the_output_format(capsys, monkeypatch):
    monkeypatch.setattr(ci_changes, "decide", lambda e, b, h="HEAD": (True, "a\nb\r\nc"))
    ci_changes.main(["--event", "push", "--base", "a" * 40])
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and out[1] == "reason=a b c"
