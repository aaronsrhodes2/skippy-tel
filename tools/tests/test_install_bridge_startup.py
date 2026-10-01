"""tools/install_bridge_startup.sh registers the Windows task that starts the bridge supervisor at every sign-in.

It changes the Captain's OS, so the rule it is held to here: nothing happens without --apply. NO TEST BELOW PASSES --apply:
the real task is never registered from a test run. Where a test needs to prove "schtasks was not called", SCHTASKS points
at a stub that records its calls, so even a bug in the script cannot reach the real Task Scheduler.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bashrun import BASH, posix, run_bash

SCRIPT = Path(__file__).resolve().parents[1] / "install_bridge_startup.sh"
ROOT = SCRIPT.parents[1]
XML_FILE = ROOT / "state" / "SkippyBridge.task.xml"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash (Git for Windows' on Windows)")

STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_CALLS"
"""


@pytest.fixture
def stub(tmp_path):
    """A stand-in for schtasks.exe that records its arguments, one call per line."""
    path, calls = tmp_path / "schtasks-stub", tmp_path / "calls.txt"
    path.write_text(STUB, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return {"SCHTASKS": posix(path), "STUB_CALLS": posix(calls), "calls": calls}


def installer(*args, env=None):
    return run_bash([posix(SCRIPT), *args], env=env)


def snippet(body: str, *args: str, env=None):
    """Source the script and run `body`; `args` arrive as $1, $2 ... (so nothing needs quoting into the shell text)."""
    return run_bash(["-c", f'source "{posix(SCRIPT)}"\n{body}', "_", *args], env=env)


def xml_from(text: str) -> ET.Element:
    return ET.fromstring(text.encode("utf-16"))          # the way schtasks reads it: declared and encoded as UTF-16


def task_xml(user: str = r"PC\captain", bash_exe: str = r"C:\Program Files\Git\bin\bash.exe") -> ET.Element:
    done = snippet('task_xml "$1" "$2" "$3" "$USER_ARG"', bash_exe, "D:/x/tools/bridge_supervisor.sh", "D:\\x",
                   env={"USER_ARG": user})                          # in the environment: see the note on shquote below
    assert done.returncode == 0, done.stderr
    return xml_from(done.stdout)


# ── the dry run ──────────────────────────────────────────────────────────────

def test_the_default_is_a_dry_run_that_prints_the_exact_command_and_changes_nothing(stub):
    before = XML_FILE.stat().st_mtime if XML_FILE.exists() else None
    done = installer(env={k: v for k, v in stub.items() if k != "calls"})
    assert done.returncode == 0, done.stderr
    assert "DRY RUN: nothing was changed" in done.stdout
    assert "MSYS_NO_PATHCONV=1" in done.stdout and "/Create /TN SkippyBridge /XML" in done.stdout
    assert done.stdout.count("/F") >= 1
    assert not stub["calls"].exists(), "the dry run called schtasks"
    assert (XML_FILE.stat().st_mtime if XML_FILE.exists() else None) == before, "the dry run wrote the task file"


def test_the_printed_command_is_schtasks_with_an_xml_and_force_so_a_second_run_replaces_the_task(stub):
    # (the stub again, never the default schtasks.exe: should the dry run ever regress into a real run, a test must not register a task)
    done = installer(env={k: v for k, v in stub.items() if k != "calls"})
    line = next(ln for ln in done.stdout.splitlines() if "/Create" in ln)
    assert line.strip().startswith(f"$ MSYS_NO_PATHCONV=1 {stub['SCHTASKS']} /Create /TN SkippyBridge /XML ")
    assert line.rstrip().endswith("/F")
    assert "SkippyBridge.task.xml" in line
    assert "${SCHTASKS:-schtasks.exe}" in SCRIPT.read_text(encoding="utf-8")        # and out of the box it is the real schtasks.exe


def test_remove_is_also_a_dry_run_by_default_and_names_both_steps(stub):
    done = installer("--remove", env={k: v for k, v in stub.items() if k != "calls"})
    assert done.returncode == 0, done.stderr
    assert "/End /TN SkippyBridge" in done.stdout and "/Delete /TN SkippyBridge /F" in done.stdout
    assert "DRY RUN" in done.stdout
    assert not stub["calls"].exists(), "the dry-run removal called schtasks"


def test_status_is_read_only_and_asks_windows_about_the_task(stub):
    done = installer("--status", env={k: v for k, v in stub.items() if k != "calls"})
    assert done.returncode == 0, done.stderr
    assert stub["calls"].read_text().splitlines() == ["/Query /TN SkippyBridge /V /FO LIST"]     # a query, and nothing else


def test_an_unknown_option_is_refused(stub):
    done = installer("--apply-now", env={k: v for k, v in stub.items() if k != "calls"})
    assert done.returncode == 2 and "unknown option" in done.stderr
    assert not stub["calls"].exists()


# ── the task definition ──────────────────────────────────────────────────────

def test_the_task_starts_at_logon_as_the_user_with_no_time_limit_and_restarts_on_failure():
    root = task_xml()
    ns = lambda p: root.find(p, NS)                       # noqa: E731
    assert ns("t:Triggers/t:LogonTrigger") is not None                                   # at logon (not at boot: it needs his session)
    assert ns("t:Triggers/t:LogonTrigger/t:UserId").text == r"PC\captain"
    assert ns("t:Principals/t:Principal/t:UserId").text == r"PC\captain"
    assert ns("t:Principals/t:Principal/t:RunLevel").text == "LeastPrivilege"            # not elevated, and not SYSTEM: his claude login
    assert ns("t:Principals/t:Principal/t:LogonType").text == "InteractiveToken"
    s = "t:Settings/t:"
    assert ns(s + "ExecutionTimeLimit").text == "PT0S"                                    # `schtasks /TR` would kill it after 72 hours
    assert ns(s + "RestartOnFailure/t:Interval").text == "PT1M" and ns(s + "RestartOnFailure/t:Count").text == "999"
    assert ns(s + "MultipleInstancesPolicy").text == "IgnoreNew"
    assert ns(s + "DisallowStartIfOnBatteries").text == "false" and ns(s + "StopIfGoingOnBatteries").text == "false"
    assert ns(s + "Enabled").text == "true"


def test_the_task_runs_the_supervisor_through_git_bash_in_a_login_shell():
    root = task_xml(bash_exe=r"C:\Program Files\Git\bin\bash.exe")
    exe = root.find("t:Actions/t:Exec", NS)
    assert exe.find("t:Command", NS).text == r"C:\Program Files\Git\bin\bash.exe"
    assert exe.find("t:Arguments", NS).text == '-l "D:/x/tools/bridge_supervisor.sh"'      # -l: the launcher's PATH (curl, python)
    assert exe.find("t:WorkingDirectory", NS).text == r"D:\x"


def test_awkward_characters_in_a_user_name_survive_the_xml():
    assert task_xml(user="PC\\Q&A <ops> \"x\" 'y'").find("t:Principals/t:Principal/t:UserId", NS).text == "PC\\Q&A <ops> \"x\" 'y'"


def test_a_missing_user_name_leaves_the_user_out_rather_than_writing_an_empty_one():
    root = task_xml(user="")
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", NS) is None
    assert root.find("t:Principals/t:Principal/t:UserId", NS) is None


def test_the_xml_is_written_as_utf_16_with_a_byte_order_mark(tmp_path):
    out = tmp_path / "task.xml"
    done = snippet('task_xml "$1" "D:/x/s.sh" "D:\\x" "PC\\u" | write_utf16 "$2"', "C:\\bash.exe", posix(out))
    assert done.returncode == 0, done.stderr
    raw = out.read_bytes()
    assert raw[:2] in (b"\xff\xfe", b"\xfe\xff")                                          # a BOM: what schtasks sniffs
    assert ET.fromstring(raw).find("t:Actions/t:Exec/t:Command", NS) is not None


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["plain", "with space", "it's", "C:\\Users\\x\\f.xml", "", "$HOME `x` \"q\""])
def test_shquote_gives_a_word_bash_reads_back_unchanged(word):
    # The word travels in the environment: Git for Windows' bash launcher strips single quotes out of ARGUMENTS.
    done = snippet('eval "printf %s $(shquote "$WORD")"', env={"WORD": word})
    assert done.returncode == 0, done.stderr
    assert done.stdout == word


def test_shquote_leaves_plain_words_bare_so_the_printed_command_stays_readable():
    done = run_bash(["-c", f'source "{posix(SCRIPT)}"\nshquote /Create; echo; shquote SkippyBridge; echo; shquote "a b"'])
    assert done.stdout.splitlines() == ["/Create", "SkippyBridge", "'a b'"]
