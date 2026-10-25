"""New folder (new_folder.py): the folder, a git repo with a first commit, and its home, a bare repo in the git home (D:\trunk\git
on skippy-pc; a temporary folder here). When the home can't be made, a marker says it is owed and a retry settles it. Nothing
here, or in new_folder.py, talks to GitHub."""
import subprocess

import pytest

import new_folder as nf


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "Test"), ("GIT_AUTHOR_EMAIL", "t@example.com"),
                 ("GIT_COMMITTER_NAME", "Test"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(k, v)


def test_a_name_is_cleaned_into_a_slug():
    assert nf.slug("Star Lanes!") == "star-lanes" and nf.slug("../escape") == "escape" and nf.slug("-lead") == "lead"


@pytest.fixture
def home(tmp_path):
    return tmp_path / "git-home"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


def test_it_makes_the_folder_a_repo_with_a_first_commit_and_its_bare_home(tmp_path, home):
    root = tmp_path / "dev"
    root.mkdir()
    made = nf.create(str(root), "Star Lanes", git_home=str(home))
    assert made["name"] == "star-lanes" and made["remote"] == "created" and made["repo"] == "aaronsrhodes2/star-lanes"
    path = root / "star-lanes"
    assert (path / "CLAUDE.md").is_file() and git("log", "--oneline", cwd=path).endswith("Start star-lanes")
    assert git("branch", "--show-current", cwd=path) == "main"
    assert git("remote", "get-url", "origin", cwd=path) == (home / "star-lanes.git").as_posix()
    assert git("log", "--oneline", "main", cwd=home / "star-lanes.git").endswith("Start star-lanes")     # pushed home
    assert "github" not in (path / "CLAUDE.md").read_text().lower().replace("never to github", "")
    assert not nf.pending(path)


@pytest.mark.parametrize("bad", ["", "x", "!!!", "a" * 41, "9lives"])
def test_bad_names_are_refused(tmp_path, home, bad):
    with pytest.raises(nf.Refused):
        nf.create(str(tmp_path), bad, git_home=str(home))
    assert list(tmp_path.iterdir()) == []


def test_a_taken_name_is_refused_and_nothing_is_touched(tmp_path, home):
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "keep.txt").write_text("mine")
    with pytest.raises(nf.Refused, match="already exists"):
        nf.create(str(tmp_path), "taken", git_home=str(home))
    assert (tmp_path / "taken" / "keep.txt").read_text() == "mine"


def test_when_the_home_is_away_the_folder_is_kept_and_marked_then_a_retry_settles_it(tmp_path, home):
    root = tmp_path / "dev"
    root.mkdir()
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("a file where the git home should be")
    made = nf.create(str(root), "offline-thing", git_home=str(blocked))
    path = root / "offline-thing"
    assert made["remote"] == "pending" and made["why"] and nf.pending(path)
    assert ".skippy-remote-pending" in (path / ".git" / "info" / "exclude").read_text()     # never committed
    assert nf.retry(path, git_home=str(blocked))["remote"] == "pending"
    assert nf.retry(path, git_home=str(home)) == {"remote": "created", "why": ""} and not nf.pending(path)
    assert nf.retry(path, git_home=str(home)) is None                                         # nothing owed any more


def test_the_bridge_answers_the_new_folders_id_for_the_picker(shop, tmp_path, home, monkeypatch):
    import workshop_engine as engine
    root = tmp_path / "dev"
    root.mkdir()
    monkeypatch.setattr(nf, "GIT_HOME", str(home))
    w = engine.Workshop(shop.claude_cmd, sessions=str(tmp_path / "s.json"), dev_root=str(root))
    got = w.new_folder("Moon Base")
    assert got["id"] == "f-moon-base" and got["remote"] == "created" and any(p["id"] == "f-moon-base" for p in w.projects())
    with pytest.raises(ValueError):
        w.new_folder("moon-base")
