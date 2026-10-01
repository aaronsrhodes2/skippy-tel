"""R01: the private Skippy's prompt reaches Claude, whole, on Windows.

The incident: the multi-line workshop prompt went to claude as ONE argument (--append-system-prompt <text>). On Windows
`claude` is npm's claude.cmd, and cmd.exe cuts a command line at the first newline, so only the prompt's first line
arrived and every argument after it (--resume, --add-dir) was dropped. For every run since the chat shipped the
guardrails were not in force (the agent has --dangerously-skip-permissions), and a follow-up message never resumed its
session. CI is Linux-only, so nothing could see it; the two Windows-only tests at the bottom are the ones that can.

The fix, and what is pinned here: the prompt is rendered into a file ({repo}/{primary} filled in) on every task and
passed with --append-system-prompt-file; the message goes by stdin; no argument may ever contain a newline."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import workshop_engine as engine
from bridge_testkit import calls, finish


# ── the command line ────────────────────────────────────────────────────────
@pytest.mark.parametrize("session", [None, "sess-9"])
def test_no_argument_contains_a_newline_and_the_prompt_is_a_file(shop, session):
    shop.write_prompt()
    cmd = shop.command(session)
    assert not [a for a in cmd if "\n" in a or "\r" in a]
    assert shop.prompt().count("\n") > 10                                   # it IS multi-line: the case that broke
    assert not [a for a in cmd if "Guardrails" in a or "Skippy" in a]       # none of its text is on the command line
    assert "--append-system-prompt" not in cmd                              # the inline form is gone for good
    assert cmd[cmd.index("--append-system-prompt-file") + 1] == str(shop.prompt_path())
    assert cmd[cmd.index("--add-dir") + 1] == shop.primary
    assert len(subprocess.list2cmdline(cmd)) < 7000                         # cmd.exe refuses a line over about 8191
    assert (cmd[-2:] == ["--resume", session]) == (session is not None)


def test_the_resumed_snapshot_is_switched_off_so_the_current_rules_always_apply(shop):
    """By default Claude Code records the system prompt on a conversation's first request and replays that record on
    every resume (measured on 2.1.285: a resumed session ignored a changed prompt file). A chat begun under the old
    one-line prompt would then keep it, and an edit to the Captain's rules would not reach a chat under way."""
    cmd = shop.command("sess-9")
    assert cmd[cmd.index("--system-prompt-snapshot") + 1] == "off"
    assert cmd.index("--system-prompt-snapshot") < cmd.index("--resume")


def test_the_prompt_file_sits_next_to_the_sessions_file(shop, tmp_path):
    assert shop.prompt_path() == tmp_path / engine.PROMPT_NAME and shop.prompt_path().parent == Path(shop.sessions_path).parent
    assert engine.PROMPT_NAME.startswith(".skippy-workshop-")              # the family of its neighbours: sessions, runs


def test_the_rendered_prompt_is_exactly_the_template_with_the_two_paths_filled_in(shop):
    path = shop.write_prompt()
    text = path.read_text(encoding="utf-8")
    assert text == engine.PROMPT_FILE.read_text(encoding="utf-8").replace("{repo}", shop.repo).replace("{primary}", shop.primary)
    assert shop.repo in text and shop.primary in text and "{repo}" not in text and "{primary}" not in text
    assert path.read_bytes() == text.encode("utf-8") and b"\r" not in path.read_bytes()   # no CRLF rewriting of the rules


# ── what actually reaches the process ───────────────────────────────────────
def test_a_run_launches_claude_with_every_flag_intact_and_the_whole_prompt_in_the_file(shop):
    finish(shop.start("aaaaaaaa", "conv-12345678", "hello"))
    first = calls(shop)[0]
    assert first["argv"] == shop.command(None)[2:]                          # [2:] is the interpreter and the fake's own path
    assert first["prompt_file"] == shop.prompt()                            # the file existed, complete, when claude started
    assert first["prompt_file"].count("\n") > 10 and "## Guardrails" in first["prompt_file"]
    assert first["stdin"] == "hello"
    finish(shop.start("bbbbbbbb", "conv-12345678", "again"))
    second = calls(shop)[1]
    assert second["argv"][-2:] == ["--resume", "sess-1"] and "--add-dir" in second["argv"]    # what the old launch dropped
    assert second["prompt_file"] == shop.prompt()


def test_an_edited_prompt_takes_effect_on_the_next_message(shop, tmp_path):
    template = tmp_path / "prompt.md"
    template.write_text("one\ntwo {repo}\nthree {primary}\n", encoding="utf-8")
    shop.prompt_file = template
    finish(shop.start("aaaaaaaa", "conv-12345678", "x"))
    assert calls(shop)[0]["prompt_file"] == f"one\ntwo {shop.repo}\nthree {shop.primary}\n"
    template.write_text("changed\nrules\n", encoding="utf-8")
    finish(shop.start("bbbbbbbb", "conv-12345678", "y"))
    assert calls(shop)[1]["prompt_file"] == "changed\nrules\n"


def test_an_old_copy_of_the_prompt_is_replaced_not_trusted(shop):
    shop.prompt_path().write_text("the old first line only\n", encoding="utf-8")
    finish(shop.start("aaaaaaaa", "conv-12345678", "x"))
    assert calls(shop)[0]["prompt_file"] == shop.prompt()


def test_an_unchanged_prompt_file_is_not_touched(shop):
    path = shop.write_prompt()
    stamp = (path.stat().st_mtime_ns, path.stat().st_size)
    assert shop.write_prompt() == path and (path.stat().st_mtime_ns, path.stat().st_size) == stamp


# ── never a run without its rules ───────────────────────────────────────────
def test_when_the_prompt_file_cannot_be_written_nothing_is_launched(shop, monkeypatch):
    def refuse(self, data):
        raise PermissionError("[WinError 5] Access is denied")
    monkeypatch.setattr(Path, "write_bytes", refuse)
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "x"))
    assert run.ok is False and "instructions file" in run.error and "Access is denied" in run.error
    assert not shop.record.exists()                                         # claude never started without its guardrails


def test_an_argument_with_a_newline_is_refused_and_nothing_is_launched(shop):
    shop.primary = "D:\\Aaron\\dev\nelopment"                               # the kind of thing that must never reach cmd.exe
    with pytest.raises(ValueError, match="newline"):
        shop.command(None)
    with pytest.raises(ValueError, match="newline"):
        shop.command("sess-1\r\n--evil")
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "x"))
    assert run.ok is False and run.error.startswith("can't start Claude Code") and "newline" in run.error
    assert not shop.record.exists()


# ── Windows only: the real thing (CI is Linux and skips both) ───────────────
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the truncation is cmd.exe's: claude.cmd is a Windows shim")


def real_claude_cmd():
    found = shutil.which("claude.cmd") or os.path.expanduser("~/AppData/Roaming/npm/claude.cmd")
    return found if os.path.isfile(found) else None


@windows_only
def test_the_real_claude_cmd_receives_every_argument_with_a_multiline_prompt_file(tmp_path):
    """The real npm shim, through the same Popen path a run uses (_spawn), with the real command() and a real multi-line
    prompt file. --version is the LAST argument: before the fix a newline cut the line short and it never arrived
    (claude answered 'Input must be provided...' instead of its version), so seeing the version proves nothing before
    it, --resume and --add-dir included, was dropped."""
    claude = real_claude_cmd()
    if claude is None:
        pytest.skip("claude.cmd isn't installed on this PC")
    (tmp_path / "clone").mkdir()
    w = engine.Workshop([claude], repo=str(tmp_path / "clone"), primary=str(tmp_path / "primary"),
                        sessions=str(tmp_path / "sessions.json"), remote="unused")
    w.write_prompt()
    assert w.prompt_path().read_text(encoding="utf-8").count("\n") > 10
    proc = w._spawn(w.command("00000000-0000-4000-8000-000000000000") + ["--version"])
    out, _ = proc.communicate(timeout=120)
    assert proc.returncode == 0 and re.search(r"^\d+\.\d+\.\d+ \(Claude Code\)$", out.strip(), re.M), out


@windows_only
def test_a_cmd_shim_gets_every_argument_and_the_whole_prompt(shop, tmp_path):
    """No claude install needed: a .cmd shim in front of the fake, so a whole run goes through cmd.exe exactly as it
    does in production. The old launch recorded a one-line prompt and no --resume/--add-dir here."""
    shim = tmp_path / "claude.cmd"
    shim.write_text(f'@"{sys.executable}" "{shop.fake}" %*\r\n', encoding="utf-8")
    shop.claude_cmd = [str(shim)]
    finish(shop.start("aaaaaaaa", "conv-12345678", "first"))
    finish(shop.start("bbbbbbbb", "conv-12345678", "second"))
    first, second = calls(shop)
    assert first["argv"] == shop.command(None)[1:] and first["prompt_file"] == shop.prompt()
    assert first["prompt_file"].count("\n") > 10
    assert second["argv"] == shop.command("sess-1")[1:] and second["argv"][-2:] == ["--resume", "sess-1"]
    assert second["stdin"] == "second"
