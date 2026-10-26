"""run_state: the facts a long run hands to its next segment, and the loose ends its stop gate points at."""
import subprocess

import run_state as rs


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("a")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "first commit")
    return str(tmp_path)


def test_a_handoff_opens_with_the_request_word_for_word_and_then_only_facts(tmp_path):
    clone = _repo(tmp_path)
    (tmp_path / "new.mjs").write_text("x")
    j = rs.Journal()
    j.said("There's the bug: quest items are never placed.")
    j.did("Edit", "src/world.mjs")
    text = rs.handoff("Turn 1: build the generator.\nCommit when the tests pass.", 2, clone, j)
    assert text.startswith("Turn 1: build the generator.\nCommit when the tests pass.\n")
    assert "segment 2 of the job above" in text
    assert "?? new.mjs" in text and "first commit" in text
    assert ">>>\n  There's the bug: quest items are never placed.\n  <<<" in text
    assert "  Edit: src/world.mjs" in text


def test_a_clean_repo_and_an_empty_journal_say_so_and_add_nothing_else(tmp_path):
    text = rs.handoff("Do it.", 3, _repo(tmp_path), rs.Journal())
    assert "git status: clean" in text and "last messages" not in text and "last tool calls" not in text


def test_a_folder_that_is_not_a_repo_still_hands_off(tmp_path):
    text = rs.handoff("Do it.", 2, str(tmp_path), rs.Journal())
    assert "git status: clean" in text and "git log: no commits" in text


def test_the_journal_keeps_only_the_latest_and_counts_everything():
    j = rs.Journal()
    for i in range(30):
        j.did("Bash", f"cmd {i}")
    for i in range(10):
        j.said(f"msg {i}")
    j.said("   ")
    assert j.tool_calls == 30 and len(j.tools) == rs.LAST_TOOLS and j.tools[-1] == "Bash: cmd 29"
    assert list(j.words) == [f"msg {i}" for i in range(6, 10)]


def test_a_long_message_is_cut_not_summarized():
    j = rs.Journal()
    j.said("x" * 10_000)
    assert len(j.words[0]) == rs.MAX_WORDS_CHARS


def test_dirty_paths_reads_porcelain_including_renames_and_quoted_names():
    out = " M src/a.mjs\n?? new file.txt\nR  old.txt -> new.txt\n?? \"odd name.txt\"\nA  b.txt"
    assert rs.dirty_paths(out) == ["src/a.mjs", "new file.txt", "new.txt", "odd name.txt", "b.txt"]


def test_the_gate_names_only_what_this_run_left_not_what_was_there_before(tmp_path):
    clone = _repo(tmp_path)
    (tmp_path / "old-leftover.txt").write_text("from an earlier task")
    before = set(rs.dirty_paths(rs.git_text(clone, "status", "--porcelain", "--untracked-files=all")))
    assert rs.uncommitted_since(clone, before) == []
    (tmp_path / "gen.mjs").write_text("mine")
    (tmp_path / "a.txt").write_text("changed")
    assert sorted(rs.uncommitted_since(clone, before)) == ["a.txt", "gen.mjs"]
    _git(tmp_path, "add", "a.txt", "gen.mjs")
    _git(tmp_path, "commit", "-q", "-m", "mine")
    assert rs.uncommitted_since(clone, before) == []


def test_gate_reason_is_facts_and_short_for_many_paths():
    assert rs.gate_reason(["a", "b"]) == "These paths changed during this run and are not committed: a, b."
    many = rs.gate_reason([f"f{i}" for i in range(20)])
    assert "(and 8 more)" in many and "f11" in many and "f12" not in many


def test_session_files_are_found_where_claude_code_keeps_them(tmp_path):
    cwd = r"D:\Aaron\development\shifting-prophesy-purple"
    f = rs.session_file(str(tmp_path), cwd, "abc")
    assert f.endswith("D--Aaron-development-shifting-prophesy-purple" + __import__("os").sep + "abc.jsonl") or f.endswith("abc.jsonl")
    assert "D--Aaron-development-shifting-prophesy-purple" in f
    assert rs.can_resume(str(tmp_path), cwd, "abc") is False
    __import__("os").makedirs(__import__("os").path.dirname(f))
    open(f, "w").write("{}")
    assert rs.can_resume(str(tmp_path), cwd, "abc") is True
    assert rs.can_resume(str(tmp_path), cwd, None) is False
