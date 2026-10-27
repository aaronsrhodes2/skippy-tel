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


# ── the file map: where things are, so a fresh session reads a part, not every file whole ──────────────────────────────

def _tree(tmp_path):
    repo = _repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "solver.mjs").write_text("// solver\nexport function solve(w) {\n  return 1;\n}\nexport class Search {}\nconst hidden = 1;\n")
    (tmp_path / "tools.py").write_text("def run():\n    pass\nclass Thing:\n    def method(self):\n        pass\n")
    (tmp_path / "SPEC.md").write_text("# Spec\n\ntext\n## 3. The world\n### 3.1 Shape\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    (tmp_path / "ignored.log").write_text("x")
    (tmp_path / ".gitignore").write_text("ignored.log\n")
    (tmp_path / ".skippy-goal").mkdir()
    (tmp_path / ".skippy-goal" / "GOAL.md").write_text("# goal\n")
    return repo


def test_the_map_gives_each_files_length_and_where_its_parts_are(tmp_path):
    repo = _tree(tmp_path)
    lines = rs.file_map(repo, rs.Journal())
    joined = "\n".join(lines)
    assert "src/solver.mjs (6 lines): solve:2, Search:5" in joined                 # exported functions and classes, by line; not a plain const
    assert "tools.py (5 lines): run:1, Thing:3" in joined
    assert "SPEC.md (5 lines): Spec:1, 3. The world:4, 3.1 Shape:5" in joined      # headings
    assert "logo.png" not in joined and "ignored.log" not in joined and ".skippy-goal" not in joined


def test_the_files_the_run_edited_come_first_and_the_references_it_read_are_added(tmp_path):
    repo = _tree(tmp_path)
    ref = tmp_path.parent / (tmp_path.name + "-ref")
    ref.mkdir()
    (ref / "Gen.java").write_text("public class Gen {\n  public void build(int a) {\n  }\n}\n")
    j = rs.Journal()
    j.touched("Edit", {"file_path": str(tmp_path / "tools.py")})
    j.touched("Read", {"file_path": str(ref / "Gen.java")})
    j.touched("Read", {"file_path": str(tmp_path / "SPEC.md")})                 # inside the repo: already in the map, not repeated outside it
    lines = rs.file_map(repo, j)
    assert lines[0].startswith("tools.py")
    assert sum("SPEC.md" in l for l in lines) == 1
    assert any(l.startswith(str(ref).replace("\\", "/")) and "Gen:1" in l and "build:2" in l for l in lines)


def test_a_big_repository_is_cut_and_says_so_and_a_huge_file_is_listed_without_its_parts(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    for i in range(rs.MAP_FILES + 5):
        (tmp_path / f"f{i:03d}.py").write_text("def a():\n    pass\n")
    (tmp_path / "big.py").write_text("def x():\n    pass\n" * 3)
    monkeypatch.setattr(rs, "MAX_MAP_BYTES", 20)
    lines = rs.file_map(repo, rs.Journal())
    assert lines[-1] == "... and 7 more files" or lines[-1].startswith("... and ")
    assert any(l.startswith("big.py (large)") for l in lines) or not any(l.startswith("big.py") for l in lines[:-1])


def test_the_handoff_carries_the_map_within_its_budget(tmp_path):
    repo = _tree(tmp_path)
    text = rs.handoff("Build it.", 2, repo, rs.Journal())
    assert "The files, with their length and where their parts are" in text and "  src/solver.mjs (6 lines): solve:2, Search:5" in text
    for i in range(300):
        (tmp_path / f"long_{i:03d}.py").write_text("\n".join(f"def function_number_{j}():\n    pass" for j in range(30)))
    big = rs.handoff("Build it.", 2, repo, rs.Journal())
    assert "(the map is cut here)" in big or "more files" in big
    assert len(big.split("where their parts are")[1]) < rs.MAP_CHARS + 400            # the map has a budget, however many files there are


def test_the_journal_remembers_what_was_edited_and_read(tmp_path):
    j = rs.Journal()
    j.touched("Write", {"file_path": "a.py"})
    j.touched("Edit", {"file_path": "a.py"})
    j.touched("Edit", {"file_path": "b.py"})
    j.touched("Read", {"file_path": "c.py"})
    j.touched("Bash", {"command": "ls"})
    j.touched("Read", {})
    assert list(j.edited) == ["a.py", "b.py"] and list(j.read) == ["c.py"]
