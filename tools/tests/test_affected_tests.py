"""tools/affected_tests.py: which tests a change can break, and that it never gets a narrower check than CI when it can't tell.

Planning is exercised on throw-away git repos (real git: the merge-base, renames, deletions and untracked files are the point) and on the
real repository (the couplings that exist today, and a drift guard: the FULL plan must cover every directory CI runs).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import affected_tests as at

ROOT = Path(__file__).resolve().parents[2]
ALL_DIRS = set(at.MAIN) | set(at.FLASK)


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return done.stdout


def write(repo: Path, rel: str, text: str = "") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ok_test(repo: Path, directory: str, body: str = "def test_ok():\n    assert True\n") -> None:
    write(repo, f"{directory}/test_{directory.replace('/', '_')}.py", body)       # unique names: one pytest session may hold several dirs


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """A small repo shaped like this one: every CI test directory (each with one passing test), some code, docs and scripts.
    Built once (a dozen git calls are the slow part on Windows); each test gets its own copy."""
    root = tmp_path_factory.mktemp("template") / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "core.autocrlf", "false")
    for d in sorted(ALL_DIRS):
        ok_test(root, d)
    write(root, "portal/app.py", "x = 1\n")
    write(root, "portal/templates/base.html", "<p>hi</p>\n")
    write(root, "services/flask-api/app/main.py", "y = 1\n")
    write(root, "services/home-reporter/reporter.py", "z = 1\n")
    write(root, "services/music-studio/server.py", "w = 1\n")
    write(root, "tools/helper.py", "t = 1\n")
    write(root, "docs/guide.md", "# guide\n")
    write(root, "docs/cicd.md", "# ci\n")
    write(root, "infra/gcp-games/startup.sh", "#!/bin/bash\necho hi\n")
    write(root, "scripts/odd.py", "o = 1\n")
    write(root, "README.md", "# repo\n")
    write(root, "portal/tests/test_reads_docs.py", 'from pathlib import Path\nDOC = Path("docs") / "cicd.md"\n\ndef test_it():\n    assert DOC\n')
    write(root, "portal/tests/test_reads_startup.py", 'S = "infra/gcp-games/startup.sh"\n\ndef test_it():\n    assert S\n')
    git(root, "config", "gc.auto", "0")                 # no background gc/maintenance racing the copies below
    git(root, "config", "maintenance.auto", "false")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root


@pytest.fixture
def repo(template, tmp_path):
    dest = tmp_path / "repo"
    # git may still be running background maintenance in the template (CI saw maintenance.lock vanish mid-copy):
    # lock files are transient and never part of the repo, so they are not copied
    shutil.copytree(template, dest, ignore=shutil.ignore_patterns("*.lock"))
    return dest


def plan_for(repo: Path, *files: str) -> at.Plan:
    return at.make_plan(list(files), repo)


def sessions(plan: at.Plan) -> dict:
    return {k: sorted(v) for k, v in plan.targets.items()}


# ── what changed ─────────────────────────────────────────────────────────────────────

def test_changed_files_are_the_branchs_commits_plus_uncommitted_plus_untracked_both_sides_of_a_rename_and_deletions(repo):
    git(repo, "checkout", "-qb", "feature")
    write(repo, "portal/app.py", "x = 2\n")
    git(repo, "commit", "-qam", "edit on the branch")                       # committed
    write(repo, "tools/helper.py", "t = 2\n")                               # modified, not committed
    write(repo, "docs/new.md", "# staged\n")
    git(repo, "add", "docs/new.md")                                         # staged
    write(repo, "scripts/untracked.py", "u = 1\n")                          # untracked
    (repo / "README.md").unlink()                                           # deleted
    git(repo, "mv", "services/home-reporter/reporter.py", "services/home-reporter/renamed.py")
    write(repo, ".gitignore", "ignored.txt\n")
    write(repo, "ignored.txt", "nope\n")                                    # untracked but ignored: not a change
    files, described = at.changed_files(repo, "main")
    assert files == sorted([".gitignore", "README.md", "docs/new.md", "portal/app.py", "scripts/untracked.py", "tools/helper.py",
                            "services/home-reporter/renamed.py", "services/home-reporter/reporter.py"])
    assert "changed vs main" in described


def test_a_base_that_moved_on_does_not_make_the_branch_look_like_it_changed_those_files(repo):
    git(repo, "checkout", "-qb", "feature")
    write(repo, "tools/helper.py", "t = 2\n")
    git(repo, "commit", "-qam", "mine")
    git(repo, "checkout", "-q", "main")
    write(repo, "docs/guide.md", "# someone else's change on main\n")
    git(repo, "commit", "-qam", "theirs")
    git(repo, "checkout", "-q", "feature")
    files, _ = at.changed_files(repo, "main")
    assert files == ["tools/helper.py"]                                     # diffed against the merge-base, not main's tip


def test_without_a_base_origin_main_then_main_is_tried_and_a_missing_base_is_reported(repo):
    write(repo, "tools/helper.py", "t = 2\n")
    files, described = at.changed_files(repo, None)                          # no origin here: falls back to main
    assert files == ["tools/helper.py"] and "vs main" in described
    files, why = at.changed_files(repo, "no-such-ref")
    assert files is None and "no-such-ref" in why and "not found" in why


def test_not_a_git_checkout_cannot_tell_what_changed(tmp_path):
    files, why = at.changed_files(tmp_path, None)
    assert files is None and "not found" in why


# ── the mapping ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("changed, expected", [
    ("portal/app.py", {"main": ["portal/tests"]}),
    ("portal/templates/base.html", {"main": ["portal/tests"]}),
    ("portal/tests/test_portal_tests.py", {"main": ["portal/tests"]}),
    ("memory/anything.py", {"main": ["memory/tests"]}),
    ("tools/helper.py", {"main": ["tools/tests"]}),
    ("services/home-reporter/reporter.py", {"main": ["services/home-reporter/tests"]}),
    ("services/claude-bridge/bridge.py", {"main": ["services/claude-bridge/tests"]}),
    ("mentat/wva.py", {"main": ["mentat/tests"]}),
    ("services/flask-api/app/main.py", {"flask-api": ["services/flask-api/tests"]}),
    ("services/flask-api/tests/test_x.py", {"flask-api": ["services/flask-api/tests"]}),
])
def test_each_area_maps_to_its_own_tests(repo, changed, expected):
    plan = plan_for(repo, changed)
    assert not plan.full and sessions(plan) == expected


def test_flask_api_is_always_its_own_session_never_mixed_into_the_portals(repo):
    plan = plan_for(repo, "services/flask-api/app/main.py", "portal/app.py")
    assert sessions(plan) == {"main": ["portal/tests"], "flask-api": ["services/flask-api/tests"]}


def test_docs_nothing_names_need_no_tests_and_a_doc_a_test_reads_reaches_that_test(repo):
    assert plan_for(repo, "docs/guide.md").targets == {} and not plan_for(repo, "docs/guide.md").full
    assert plan_for(repo, "docs/guide.md", "README.md", "docs/image.png").targets == {}
    plan = plan_for(repo, "docs/cicd.md")
    assert sessions(plan) == {"main": ["portal/tests/test_reads_docs.py"]}


def test_a_script_a_test_reads_by_path_reaches_that_test_and_gets_a_syntax_check(repo):
    plan = plan_for(repo, "infra/gcp-games/startup.sh")
    assert sessions(plan) == {"main": ["portal/tests/test_reads_startup.py"]} and plan.syntax_sh == ["infra/gcp-games/startup.sh"]


@pytest.mark.parametrize("changed", [
    "requirements.txt", "portal/requirements.txt", "services/flask-api/requirements.txt", "mentat/requirements-dev.txt",
    "tools/tests/conftest.py", "conftest.py", ".github/workflows/deploy.yml", ".github/workflows/new.yml",
    "portal/Dockerfile", "mentat/Dockerfile", "Dockerfile.dev", "docker-compose.yml", "docker-compose.override.yml",
    "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "portal/catalog.py",
])
def test_cross_cutting_changes_get_the_full_suite_never_a_narrower_check_than_ci(repo, changed):
    plan = plan_for(repo, "docs/guide.md", changed)
    assert plan.full and changed in plan.reason
    assert sessions(plan) == {"main": sorted(at.MAIN), "flask-api": sorted(at.FLASK)}


def test_a_file_nothing_knows_gets_the_full_suite_and_one_that_is_only_a_changed_helper_does_not(repo):
    plan = plan_for(repo, "scripts/odd.py")                                  # no rule, no test names it
    assert plan.full and "no rule for it and no test names it" in plan.reason
    assert not plan_for(repo, "portal/app.py").full


def test_full_beats_everything_else_in_the_same_change(repo):
    plan = plan_for(repo, "portal/app.py", "docs/guide.md", "requirements.txt", "tools/helper.py")
    assert plan.full and sessions(plan)["main"] == sorted(at.MAIN)


def test_a_service_without_tests_that_no_test_names_has_nothing_to_run(repo):
    plan = plan_for(repo, "services/music-studio/server.py")
    assert not plan.full and plan.targets == {}


def test_a_test_folder_ci_does_not_run_gets_its_own_session_not_the_main_one(repo):
    ok_test(repo, "services/music-gate/tests")
    write(repo, "services/music-gate/gate.py", "g = 1\n")
    plan = plan_for(repo, "services/music-gate/gate.py")
    assert sessions(plan) == {"music-gate": ["services/music-gate/tests"]}


def test_agent_settings_and_build_litter_are_not_changes(repo):
    plan = plan_for(repo, ".claude/settings.local.json", "state/x.json", "portal/__pycache__/app.cpython-313.pyc")
    assert not plan.full and plan.targets == {} and set(plan.why.values()) == {"not source"}


def test_generic_names_must_stand_next_to_their_folder_to_count_as_named(repo):
    write(repo, "mentat/tests/test_mentions_app.py", 'WHERE = "the portal is elsewhere"\nNAME = "app.py"\n\ndef test_it():\n    assert NAME\n')
    assert plan_for(repo, "portal/app.py").targets == {"main": {"portal/tests"}}                    # "app.py" and "portal" apart: not named
    write(repo, "mentat/tests/test_loads_portal.py", 'from pathlib import Path\nP = Path("..") / "portal" / "app.py"\n\ndef test_it():\n    assert P\n')
    assert sessions(plan_for(repo, "portal/app.py")) == {"main": ["mentat/tests/test_loads_portal.py", "portal/tests"]}   # a real reader


def test_a_module_a_test_in_another_folder_loads_by_path_pulls_that_test_in(repo):
    write(repo, "services/flask-api/tests/test_loads_telemetry.py",
          'from pathlib import Path\nSPEC = Path("..") / "portal" / "telemetry.py"\n\ndef test_it():\n    assert SPEC\n')
    write(repo, "portal/telemetry.py", "t = 1\n")
    plan = plan_for(repo, "portal/telemetry.py")
    assert sessions(plan) == {"main": ["portal/tests"], "flask-api": ["services/flask-api/tests/test_loads_telemetry.py"]}


def test_a_selection_inside_a_selected_folder_is_not_run_twice():
    assert at.prune({"portal/tests", "portal/tests/test_x.py", "tools/tests/test_y.py"}) == {"portal/tests", "tools/tests/test_y.py"}


# ── xdist ────────────────────────────────────────────────────────────────────────────

def test_xdist_is_used_when_available_and_the_selection_is_big_enough(repo):
    big = {"portal/tests", "tools/tests"}
    for n in range(6):
        write(repo, f"portal/tests/test_more_{n}.py", "def test_x():\n    pass\n")
    n = at.count_tests(repo, big)
    assert n >= at.XDIST_MIN_FILES
    argv = at.pytest_argv(big, xdist=True, files=n)
    assert argv[:3] == [sys.executable, "-m", "pytest"] and argv[3:5] == ["portal/tests", "tools/tests"]
    assert argv[-4:] == ["-n", "auto", "--dist", "loadfile"]
    assert "-n" not in at.pytest_argv(big, xdist=False, files=n)                          # not installed: plain pytest
    assert "-n" not in at.pytest_argv({"portal/tests/test_x.py"}, xdist=True, files=1)    # one file: workers would cost more than they save


def test_the_plan_says_when_xdist_is_missing(repo, monkeypatch, capfd):
    monkeypatch.setattr(at, "has_xdist", lambda: False)
    assert at.main(["--root", str(repo), "--files", "portal/app.py", "--list"]) == 0
    assert "pytest-xdist isn't installed" in capfd.readouterr().out
    monkeypatch.setattr(at, "has_xdist", lambda: True)
    assert at.main(["--root", str(repo), "--files", "portal/app.py", "--list"]) == 0
    assert "pytest-xdist isn't installed" not in capfd.readouterr().out


# ── running ──────────────────────────────────────────────────────────────────────────

def test_a_passing_selection_exits_0_and_says_ci_still_runs_everything(repo, capfd, monkeypatch):
    monkeypatch.setattr(at, "has_xdist", lambda: False)
    assert at.main(["--root", str(repo), "--files", "portal/app.py"]) == 0
    out = capfd.readouterr().out
    assert "affected_tests: OK (main)" in out and "CI still runs the full suite" in out


def test_a_failing_test_gives_pytests_exit_code_and_a_rerun_command(repo, capfd, monkeypatch):
    monkeypatch.setattr(at, "has_xdist", lambda: False)
    ok_test(repo, "tools/tests", "def test_ok():\n    assert 1 == 2, 'the sentinel failure'\n")
    code = at.main(["--root", str(repo), "--files", "tools/helper.py"])
    out = capfd.readouterr().out
    assert code == 1
    assert "affected_tests: FAILED" in out and "main (exit 1)" in out and "the sentinel failure" in out
    assert "re-run just that: python -m pytest tools/tests" in out


def test_the_exit_code_is_pytests_not_a_blanket_1(repo, capfd, monkeypatch):
    monkeypatch.setattr(at, "has_xdist", lambda: False)
    write(repo, "tools/tests/test_broken_import.py", "import a_module_that_does_not_exist\n")     # a collection error is pytest's exit 2
    assert at.main(["--root", str(repo), "--files", "tools/helper.py"]) == 2
    assert "main (exit 2)" in capfd.readouterr().out


class FakePytest:
    """Stands in for starting pytest: records each invocation and answers with a chosen exit code per session."""

    def __init__(self, codes=None):
        self.codes, self.calls = codes or {}, []

    def __call__(self, argv, cwd=None):
        self.calls.append((argv, cwd))
        for needle, code in self.codes.items():
            if needle in argv:
                return code
        return 0


def test_the_first_failing_session_wins_but_every_session_still_runs(repo, capfd, monkeypatch):
    monkeypatch.setattr(at, "has_xdist", lambda: False)
    fake = FakePytest({"services/flask-api/tests": 1})
    code = at.main(["--root", str(repo), "--files", "portal/app.py", "services/flask-api/app/main.py"], call=fake)
    out = capfd.readouterr().out
    assert code == 1 and [a[3] for a, _ in fake.calls] == ["portal/tests", "services/flask-api/tests"]      # main first, then flask-api
    assert "flask-api (exit 1)" in out and "main (exit" not in out
    fake = FakePytest({"portal/tests": 4, "services/flask-api/tests": 1})
    assert at.main(["--root", str(repo), "--files", "portal/app.py", "services/flask-api/app/main.py"], call=fake) == 4   # the first non-zero


def test_pytests_no_tests_collected_is_not_a_failure(repo, capfd, monkeypatch):
    monkeypatch.setattr(at, "has_xdist", lambda: False)
    assert at.main(["--root", str(repo), "--files", "memory/store.py"], call=FakePytest({"memory/tests": 5})) == 0
    assert "affected_tests: OK" in capfd.readouterr().out


def test_pytest_runs_from_the_repo_root_with_the_same_python_and_xdist_when_it_pays(repo, capfd, monkeypatch):
    monkeypatch.setattr(at, "has_xdist", lambda: True)
    for n in range(7):
        write(repo, f"portal/tests/test_many_{n}.py", "def test_x():\n    pass\n")
    fake = FakePytest()
    assert at.main(["--root", str(repo), "--files", "portal/app.py"], call=fake) == 0
    (argv, cwd), = fake.calls
    assert cwd == repo.resolve() and argv[0] == sys.executable and argv[-4:] == ["-n", "auto", "--dist", "loadfile"]


def test_a_syntax_error_stops_before_any_test_runs(repo, capfd, monkeypatch):
    monkeypatch.setattr(at, "has_xdist", lambda: False)
    write(repo, "portal/app.py", "def broken(:\n")
    fake = FakePytest()
    assert at.main(["--root", str(repo), "--files", "portal/app.py"], call=fake) == 1
    out = capfd.readouterr().out
    assert "SYNTAX ERROR, nothing run" in out and "portal/app.py" in out and fake.calls == []


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_a_shell_script_that_does_not_parse_is_caught_by_bash_n(repo, capfd, monkeypatch):
    monkeypatch.setattr(at, "has_xdist", lambda: False)
    write(repo, "infra/gcp-games/startup.sh", "#!/bin/bash\nif then fi (\n")
    fake = FakePytest()
    assert at.main(["--root", str(repo), "--files", "infra/gcp-games/startup.sh"], call=fake) == 1
    assert "infra/gcp-games/startup.sh" in capfd.readouterr().out and fake.calls == []


def test_list_shows_the_plan_and_runs_nothing(repo, capfd):
    ok_test(repo, "portal/tests", "def test_ok():\n    assert False\n")                           # would fail if it ran
    assert at.main(["--root", str(repo), "--files", "portal/app.py", "--list"]) == 0
    out = capfd.readouterr().out
    assert "portal/app.py  ->  portal tests" in out and "main: portal/tests" in out and "--- main" not in out


def test_nothing_differing_from_the_base_is_nothing_to_test(repo, capfd):
    assert at.main(["--root", str(repo), "--base", "main"]) == 0
    assert "nothing differs from the base, nothing to test" in capfd.readouterr().out


def test_the_checkout_itself_is_planned_from_its_diff(repo, capfd, monkeypatch):
    monkeypatch.setattr(at, "has_xdist", lambda: False)
    git(repo, "checkout", "-qb", "feature")
    write(repo, "tools/helper.py", "t = 2\n")
    fake = FakePytest()
    assert at.main(["--root", str(repo), "--base", "main"], call=fake) == 0
    out = capfd.readouterr().out
    assert "1 changed vs main" in out and "tools/helper.py  ->  tools tests" in out and "affected_tests: OK (main)" in out
    assert [a[3] for a, _ in fake.calls] == ["tools/tests"]


def test_an_unknown_base_runs_everything_because_it_cannot_tell(repo, capfd):
    assert at.main(["--root", str(repo), "--base", "nope", "--list"]) == 0
    out = capfd.readouterr().out
    assert "FULL suite (the two CI commands)" in out and "nope" in out and "services/flask-api/tests" in out


def test_full_runs_the_two_ci_commands_whatever_changed(repo, capfd):
    assert at.main(["--root", str(repo), "--files", "docs/guide.md", "--full", "--list"]) == 0
    out = capfd.readouterr().out
    assert "FULL suite" in out and "main: " in out and "flask-api: services/flask-api/tests" in out


def test_the_report_stays_short_for_a_big_change(repo, capfd):
    files = [f"docs/page{n}.md" for n in range(40)]
    at.main(["--root", str(repo), "--files", *files, "--list"])
    out = capfd.readouterr().out
    assert len(out.splitlines()) <= 14 and "... and 32 more" in out


# ── the real repository ──────────────────────────────────────────────────────────────

def ci_test_dirs() -> set[str]:
    """Every test directory the CI workflows hand to pytest, read from the workflow files themselves."""
    found = set()
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "pytest" in line and "pip install" not in line:
                found |= set(re.findall(r"(?<![\w/.-])((?:[\w-]+/)+tests)\b", line))
    return found


def test_the_full_plan_covers_every_directory_ci_runs():
    """Drift guard: if CI starts running a folder this tool doesn't know, FULL would be narrower than the gate."""
    ci = ci_test_dirs()
    assert {"portal/tests", "services/flask-api/tests"} <= ci, f"the scan of the workflows found {ci}"
    assert ci <= ALL_DIRS, f"CI runs {sorted(ci - ALL_DIRS)}: add it to affected_tests.MAIN (or FLASK)"
    plan = at.make_plan(["requirements.txt"], ROOT)
    assert {d for paths in plan.targets.values() for d in paths} == ALL_DIRS


def test_flask_api_stays_out_of_the_main_session_as_in_ci():
    assert "services/flask-api/tests" not in at.MAIN and set(at.FLASK) == {"services/flask-api/tests"}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "pytest" in line and "services/flask-api/tests" in line:
                assert "portal/tests" not in line, "flask-api's tests can't share a session with the portal's (its `app` package vs app.py)"


def test_every_test_folder_in_the_repo_has_a_session():
    dirs = at.test_dirs(ROOT)
    for d in dirs:
        assert at.session_of(d, dirs), d
        assert at.session_of(d + "/test_x.py", dirs) == at.session_of(d, dirs)
    assert at.session_of("portal/tests", dirs) == "main" and at.session_of("services/flask-api/tests", dirs) == "flask-api"
    if (ROOT / "services/music-gate/tests").is_dir():
        assert at.session_of("services/music-gate/tests", dirs) == "music-gate"             # CI doesn't run it: not in the main session


def test_couplings_that_exist_today_are_found():
    """The tests that read a file outside their own folder, as they are in this repository."""
    assert any("test_site_telemetry" in p for p in at.make_plan(["portal/telemetry.py"], ROOT).targets.get("flask-api", ())), \
        "flask-api's test loads portal/telemetry.py by path"
    assert any("test_reporter_telemetry" in p for p in at.make_plan(["portal/telemetry.py"], ROOT).targets.get("main", ()))
    recovery = at.make_plan(["docs/portal/remote-recovery.md"], ROOT)
    assert not recovery.full and any(p.startswith("tools/tests/") for p in recovery.targets.get("main", ()))      # a test reads the runbook
    startup = at.make_plan(["infra/gcp-games/startup.sh"], ROOT)
    assert not startup.full and any(p.startswith("portal/tests/") for p in startup.targets.get("main", ()))
    unnamed = "docs/this-page-does-not-exist-" + "and-nothing-names-it.md"           # built in pieces: this very file must not name it
    assert at.make_plan([unnamed], ROOT).targets == {}


def test_the_ordinary_changes_get_a_narrow_plan_on_the_real_repo():
    for changed, dirs in (("portal/chat.py", {"portal/tests"}), ("tools/rollback_pick.py", {"tools/tests"}),
                          ("services/home-reporter/reporter.py", {"services/home-reporter/tests"})):
        plan = at.make_plan([changed], ROOT)
        assert not plan.full, changed
        assert dirs <= {p for paths in plan.targets.values() for p in paths}, changed
        assert "mentat/tests" not in {p for paths in plan.targets.values() for p in paths}, changed
    assert at.make_plan(["portal/chat.py"], ROOT).targets.get("main", set()) >= {"portal/tests"}
