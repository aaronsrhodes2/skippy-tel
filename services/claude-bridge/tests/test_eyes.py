"""Eyes for Skippy Core's text-only coder (eyes.py): a picture it opens itself is looked at by Core's vision slot instead, and a
Core run is the only kind that gets this (a Claude run sees pictures by itself)."""
import json
import subprocess
import sys
from pathlib import Path

import eyes
import workshop_engine as engine
from bridge_testkit import calls, finish  # noqa: F401  (the `shop` fixture: conftest.py)

JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
CORE = {"SKIPPY_CORE_EYES": "1", "ANTHROPIC_BASE_URL": "https://core.example", "ANTHROPIC_API_KEY": "sk-core"}


def read(path):
    return {"tool_name": "Read", "tool_input": {"file_path": str(path)}}


def answering(text):
    asked = []

    def post(req):
        asked.append(json.loads(req.data))
        return {"content": [{"type": "text", "text": text}]}
    return post, asked


def test_a_core_run_opening_a_picture_gets_the_vision_models_description_instead(tmp_path):
    shot = tmp_path / "page.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
    post, asked = answering("A portal page. Text: 'Bilby's Audio Service', a play key, 'Play on: Kitchen (Skippy)'.")
    code, message = eyes.check(read(shot), env=CORE, post=post)
    assert code == 2 and "Play on: Kitchen (Skippy)" in message and str(shot) in message and "not done" in message
    assert asked[0]["model"] == "vision" and asked[0]["messages"][0]["content"][0]["source"]["media_type"] == "image/png"


def test_anything_else_is_left_alone(tmp_path):
    shot = tmp_path / "page.png"
    shot.write_bytes(JPG)
    code_py = tmp_path / "app.py"
    code_py.write_text("x = 1")
    post, asked = answering("never")
    assert eyes.check(read(shot), env={}, post=post) == (0, "")                          # a Claude run: it sees pictures itself
    assert eyes.check(read(code_py), env=CORE, post=post) == (0, "")                     # not a picture
    assert eyes.check(read(tmp_path / "gone.png"), env=CORE, post=post) == (0, "")       # not there: Read says so itself
    assert eyes.check({"tool_name": "Bash", "tool_input": {"command": "ls"}}, env=CORE, post=post) == (0, "")
    assert asked == []


def test_a_relative_path_is_found_in_the_project_folder(tmp_path):
    (tmp_path / "shots").mkdir()
    (tmp_path / "shots" / "a.jpg").write_bytes(JPG)
    post, _ = answering("a thing")
    code, message = eyes.check(read("shots/a.jpg"), env={**CORE, "CLAUDE_PROJECT_DIR": str(tmp_path)}, post=post)
    assert code == 2 and "a thing" in message


def test_when_the_vision_model_fails_the_read_is_still_refused_and_says_why(tmp_path):
    shot = tmp_path / "a.jpg"
    shot.write_bytes(JPG)

    def down(req):
        raise ConnectionError("core asleep")
    code, message = eyes.check(read(shot), env=CORE, post=down)
    assert code == 2 and "couldn't look" in message and "core asleep" in message


def test_the_hook_runs_as_claude_code_runs_it(tmp_path):
    """As a real process, with the event on stdin: a Claude run's picture Read is allowed (no network needed for that)."""
    shot = tmp_path / "a.jpg"
    shot.write_bytes(JPG)
    hook = Path(engine.__file__).with_name("eyes.py")
    ok = subprocess.run([sys.executable, str(hook)], input=json.dumps(read(shot)), capture_output=True, text=True, env={"PATH": ""})
    assert ok.returncode == 0 and ok.stderr == ""
    bad = subprocess.run([sys.executable, str(hook)], input="not json", capture_output=True, text=True, env={"PATH": ""})
    assert bad.returncode == 0


def test_a_core_run_switches_its_eyes_on_and_a_claude_run_does_not(shop, monkeypatch):
    monkeypatch.setenv("SKIPPY_CORE_URL", "https://core.example/")
    monkeypatch.setenv("SKIPPY_CORE_API_KEY", "sk-core-test")
    monkeypatch.delenv("SKIPPY_CORE_EYES", raising=False)              # keep child_env() clean for the check
    monkeypatch.setattr(engine.Workshop, "core_ready", lambda self: True)
    finish(shop.start("aaaaaaaa", "conv-12345678", "hi", brain="core"))
    finish(shop.start("bbbbbbbb", "conv-12345678", "hi"))
    core, claude = calls(shop)
    assert shop.core_env().get("SKIPPY_CORE_EYES") == "1"            # core runs get eyes on
    assert "SKIPPY_CORE_EYES" not in engine.child_env()              # claude runs (base env) don't


def _transcript(tmp_path, *rows):
    t = tmp_path / "t.jsonl"
    t.write_text("".join(json.dumps(r) + chr(10) for r in rows), encoding="utf-8")
    return str(t)


def test_with_a_task_core_s_big_model_reviews_the_picture_against_it(tmp_path):
    shot = tmp_path / "build.png"
    shot.write_bytes(JPG)
    transcript = _transcript(tmp_path, {"type": "user", "message": {"content": "build a red barn"}},
                             {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}})
    post, asked = answering("A grey shed. Faults: it is grey, not red; the roof is missing.")
    code, message = eyes.check({**read(shot), "transcript_path": transcript}, env=CORE, post=post)
    assert code == 2 and "the roof is missing" in message and "with your task in mind" in message
    sent = asked[0]
    assert sent["model"] == eyes.REVIEW_MODEL == "chat" and sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert "build a red barn" in sent["messages"][0]["content"][1]["text"]


def test_when_the_review_fails_the_vision_slot_still_describes_it(tmp_path):
    shot = tmp_path / "a.png"
    shot.write_bytes(JPG)
    transcript = _transcript(tmp_path, {"type": "user", "message": {"content": "fix the page"}})
    models = []

    def post(req):
        body = json.loads(req.data)
        models.append(body["model"])
        if body["model"] == "chat":
            raise ConnectionError("chat busy")
        return {"content": [{"type": "text", "text": "A page with a Save key."}]}
    code, message = eyes.check({**read(shot), "transcript_path": transcript}, env=CORE, post=post)
    assert code == 2 and "A page with a Save key." in message and models == ["chat", "vision"]


def test_the_task_is_the_latest_thing_the_person_said_not_a_tool_result(tmp_path):
    transcript = _transcript(tmp_path,
                             {"type": "user", "message": {"content": "first ask"}},
                             {"type": "assistant", "message": {"content": [{"type": "text", "text": "on it"}]}},
                             {"type": "user", "message": {"content": [{"type": "text", "text": "now make it blue"}]}},
                             {"type": "user", "message": {"content": [{"type": "tool_result", "content": "file written"}]}})
    assert eyes.task_of(transcript) == "now make it blue"
    assert eyes.task_of(str(tmp_path / "missing.jsonl")) == ""
