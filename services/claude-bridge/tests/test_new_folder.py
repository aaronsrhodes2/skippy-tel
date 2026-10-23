"""New folder (new_folder.py): the folder, a git repo with a first commit, and the private GitHub repo; when GitHub fails, a
marker says it is owed and a retry settles it. gh is a fake here: no test ever reaches GitHub."""
import json
import subprocess
import sys

import pytest

import new_folder as nf

FAKE_GH = r"""
import json, os, sys
log = os.environ["FAKE_GH_LOG"]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
mode = os.environ.get("FAKE_GH_MODE", "ok")
if mode == "fail":
    print("HTTP 502: GitHub is down", file=sys.stderr); sys.exit(1)
if mode == "exists":
    print("GraphQL: Name already exists on this account", file=sys.stderr); sys.exit(1)
"""


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "Test"), ("GIT_AUTHOR_EMAIL", "t@example.com"),
                 ("GIT_COMMITTER_NAME", "Test"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(k, v)


def test_a_name_is_cleaned_into_a_slug():
    assert nf.slug("Star Lanes!") == "star-lanes" and nf.slug("../escape") == "escape" and nf.slug("-lead") == "lead"


@pytest.fixture
def gh(tmp_path, monkeypatch):
    script = tmp_path / "fake_gh.py"
    script.write_text(FAKE_GH, encoding="utf-8")
    log = tmp_path / "gh.log"
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    return [sys.executable, str(script)], log


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


def test_it_makes_the_folder_a_repo_with_a_first_commit_and_the_private_github_repo(tmp_path, gh):
    cmd, log = gh
    made = nf.create(str(tmp_path), "Star Lanes", gh=cmd)
    assert made["name"] == "star-lanes" and made["remote"] == "created" and made["repo"] == "aaronsrhodes2/star-lanes"
    path = tmp_path / "star-lanes"
    assert (path / "CLAUDE.md").is_file() and git("log", "--oneline", cwd=path).endswith("Start star-lanes")
    assert git("branch", "--show-current", cwd=path) == "main"
    (call,) = [json.loads(l) for l in log.read_text().splitlines()]
    assert call[:3] == ["repo", "create", "aaronsrhodes2/star-lanes"] and "--private" in call and "--push" in call
    assert not nf.pending(path)


@pytest.mark.parametrize("bad", ["", "x", "!!!", "a" * 41, "9lives"])
def test_bad_names_are_refused(tmp_path, gh, bad):
    with pytest.raises(nf.Refused):
        nf.create(str(tmp_path), bad, gh=gh[0])
    assert list(tmp_path.iterdir()) == [tmp_path / "fake_gh.py"]


def test_a_taken_name_is_refused_and_nothing_is_touched(tmp_path, gh):
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "keep.txt").write_text("mine")
    with pytest.raises(nf.Refused, match="already exists"):
        nf.create(str(tmp_path), "taken", gh=gh[0])
    assert (tmp_path / "taken" / "keep.txt").read_text() == "mine"


def test_when_github_fails_the_folder_is_kept_and_marked_then_a_retry_settles_it(tmp_path, gh, monkeypatch):
    cmd, log = gh
    monkeypatch.setenv("FAKE_GH_MODE", "fail")
    made = nf.create(str(tmp_path), "offline-thing", gh=cmd)
    path = tmp_path / "offline-thing"
    assert made["remote"] == "pending" and "502" in made["why"] and nf.pending(path)
    assert ".skippy-remote-pending" in (path / ".git" / "info" / "exclude").read_text()     # never committed
    assert nf.retry(path, gh=cmd)["remote"] == "pending"
    monkeypatch.setenv("FAKE_GH_MODE", "ok")
    assert nf.retry(path, gh=cmd) == {"remote": "created", "why": ""} and not nf.pending(path)
    assert nf.retry(path, gh=cmd) is None                                                     # nothing owed any more


def test_without_gh_it_is_marked_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(nf.shutil, "which", lambda name: None)
    made = nf.create(str(tmp_path), "no-cli-here")
    assert made["remote"] == "pending" and "gh" in made["why"]


def test_the_bridge_answers_the_new_folders_id_for_the_picker(shop, tmp_path, gh, monkeypatch):
    import workshop_engine as engine
    root = tmp_path / "dev"
    root.mkdir()
    w = engine.Workshop(shop.claude_cmd, sessions=str(tmp_path / "s.json"), dev_root=str(root))
    monkeypatch.setattr(nf.shutil, "which", lambda name: None)
    got = w.new_folder("Moon Base")
    assert got["id"] == "f-moon-base" and got["remote"] == "pending" and any(p["id"] == "f-moon-base" for p in w.projects())
    with pytest.raises(ValueError):
        w.new_folder("moon-base")
