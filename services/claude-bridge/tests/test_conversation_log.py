"""Every private-Skippy conversation is kept in full in the Captain's own folder (conversation_log.py)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import conversation_log as cl  # noqa: E402
import workshop_engine as engine  # noqa: E402


class FakeRun:
    def __init__(self, task="abcdef12", conv="conv-12345678", message="make the tile green\nplease", created=1_800_000_000.0):
        self.task, self.conv, self.message, self.created = task, conv, message, created
        self.events, self.ok, self.error, self.finished_at = [], None, None, None


def test_tests_never_write_the_real_folder():
    assert os.path.abspath(cl.ROOT) != os.path.abspath(r"D:\Aaron\conversations\private-skippy")


def test_a_whole_turn_is_written_as_it_happens_and_the_transcript_is_copied(tmp_path):
    projects = tmp_path / "projects"
    repo = r"D:\Aaron\development\SkippyTel-workshop"
    proj = cl.claude_project_dir(repo, projects)
    assert proj.name == "D--Aaron-development-SkippyTel-workshop"
    proj.mkdir(parents=True)
    (proj / "sess-1.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    log = cl.ConversationLog(str(tmp_path / "out"), repo=repo, projects=projects)
    run = FakeRun()
    log.started(run)
    md = log.path(run.conv)
    assert "make the tile green\nplease" in md.read_text(encoding="utf-8")       # on record before anything runs
    run.events.append({"t": "text", "text": "On it."})
    log.progress(run)
    run.events.append({"t": "tool", "name": "Bash", "detail": "git push origin main"})
    run.events.append({"t": "text", "text": "Deployed and live."})
    run.ok, run.finished_at = True, run.created + 60
    log.finished(run, "sess-1")
    text = md.read_text(encoding="utf-8")
    assert text.count("On it.") == 1 and "- `Bash`: `git push origin main`" in text and "Deployed and live." in text
    assert "done_" in text.replace(", done", "done")
    assert (md.parent / f"{md.stem}.claude-sess-1.jsonl").read_text(encoding="utf-8") == '{"type":"user"}\n'
    run2 = FakeRun(task="abcdef13", message="and now blue")                       # same conversation: same file
    log.started(run2)
    assert log.path(run2.conv) == md and md.read_text(encoding="utf-8").count("## The Captain") == 2


def test_a_failure_is_recorded_and_a_broken_disk_never_breaks_the_run(tmp_path, capsys):
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("x")
    log = cl.ConversationLog(str(blocked / "sub"), repo="r", projects=tmp_path)
    run = FakeRun()
    log.started(run)                                    # can't write: swallowed
    run.events.append({"t": "text", "text": "x"})
    log.progress(run)
    run.ok, run.error = False, "stopped"
    log.finished(run, None)
    assert capsys.readouterr().err.count("conversation log") == 1               # said once, not per event
    ok = cl.ConversationLog(str(tmp_path / "out"), repo="r", projects=tmp_path)
    ok.started(run)
    ok.finished(run, None)
    assert "**ended with an error:** stopped" in ok.path(run.conv).read_text(encoding="utf-8")


def test_the_workshop_writes_its_conversations_next_to_its_sessions_unless_it_is_the_real_bridge(tmp_path):
    w = engine.Workshop(["claude"], sessions=str(tmp_path / "s.json"))
    assert Path(w.convlog.root) == tmp_path / "conversations"
    real = engine.Workshop(["claude"])
    assert str(real.convlog.root) == cl.ROOT


def test_backfill_renders_and_copies_what_claude_code_holds(tmp_path):
    projects, repo = tmp_path / "p", "C:/x/SkippyTel-workshop"
    proj = cl.claude_project_dir(repo, projects)
    proj.mkdir(parents=True)
    lines = [{"type": "user", "timestamp": "t1", "message": {"content": "hello skippy"}},
             {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi, Captain."},
                                                            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "content": "README.md"}]}}]
    (proj / "s9.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    sessions = tmp_path / "sessions.json"
    sessions.write_text(json.dumps({"conv-abc12345": "s9"}), encoding="utf-8")
    assert cl.backfill(str(tmp_path / "out"), repo, projects, str(sessions)) == 1
    [md] = (tmp_path / "out").glob("*.md")
    text = md.read_text(encoding="utf-8")
    assert "conv-abc12345" in md.name and "hello skippy" in text and "Hi, Captain." in text and '"command": "ls"' in text
    assert "README.md" in text and list((tmp_path / "out").glob("*.claude-s9.jsonl"))
