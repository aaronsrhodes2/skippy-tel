"""SKIPPY_POPUP lines (docs/popups.md): `SKIPPY_POPUP: <name> <json args>` becomes a {"t": "popup", id, name, args} event the portal
queues as a patient job; `_file` hands the job a picture or sound from the clone or the Captain's attachments, nothing else."""
import hashlib
import json
from pathlib import Path

import workshop_engine as engine
import conversation_log
from test_bridge_images import run_saying, texts

WAV = b"RIFF" + bytes(4) + b"WAVEfmt " + bytes(32)


def popups(run):
    return [e for e in run.events if e.get("t") == "popup"]


def test_split_popups_takes_whole_lines_with_json_args():
    said, found = engine.split_popups('ok\nSKIPPY_POPUP: remove_bg {"text": "the dog"}\n`SKIPPY_POPUP: echo`\nSKIPPY_POPUP: stems {bad\nend')
    assert found == [("remove_bg", {"text": "the dog"}), ("echo", {}), ("stems", None)] and "remove_bg" not in said
    assert engine.split_popups('SKIPPY_POPUP: echo ["a list"]')[1] == [("echo", None)]       # not an object: the run says so
    assert engine.split_popups("SKIPPY_POPUP: echo {\"x\": \"" + "y" * 5000 + "\"}")[1] == [("echo", None)]


def test_a_popup_line_becomes_an_event_with_its_own_id_and_is_not_shown(shop, monkeypatch):
    run = run_saying(shop, monkeypatch, 'Queued.\nSKIPPY_POPUP: echo {"text": "ping"}\n')
    (ev,) = popups(run)
    assert ev["name"] == "echo" and ev["args"] == {"text": "ping"} and len(ev["id"]) == 16 and "input" not in ev
    assert texts(run) == ["Queued."]


def test_at_most_three_and_bad_args_are_said(shop, monkeypatch):
    run = run_saying(shop, monkeypatch, "\n".join(f'SKIPPY_POPUP: echo {{"text": "{i}"}}' for i in range(5)))
    assert [e["args"]["text"] for e in popups(run)] == ["0", "1", "2"]
    run = run_saying(shop, monkeypatch, "SKIPPY_POPUP: echo [1]", task="bbbbbbbb")
    assert popups(run) == [] and any("one JSON object" in t for t in texts(run))


def test_a_file_in_the_clone_is_handed_over_by_reference_and_one_outside_is_not(shop, monkeypatch, tmp_path):
    (Path(shop.repo) / "riff.wav").write_bytes(WAV + bytes(3 * 1024 * 1024))          # bigger than a chat message: no matter
    run = run_saying(shop, monkeypatch, 'SKIPPY_POPUP: echo {"text": "x", "_file": "riff.wav"}')
    (ev,) = popups(run)
    assert ev["args"] == {"text": "x"} and ev["input"]["type"] == "audio/wav" and "b64" not in ev["input"]
    ref = ev["input"]["ref"]
    kept = Path(shop.popup_inputs)
    assert (kept / f"{ref}.wav").read_bytes()[:len(WAV)] == WAV
    meta = json.loads((kept / f"{ref}.json").read_text())
    assert meta["owner"] == hashlib.sha256(str(run.who).strip().lower().encode()).hexdigest()[:16]     # the portal's owner_id
    outside = tmp_path / "secret.wav"
    outside.write_bytes(WAV)
    run = run_saying(shop, monkeypatch, f'SKIPPY_POPUP: echo {{"text": "x", "_file": "{outside.as_posix()}"}}', task="cccccccc")
    assert popups(run) == [] and any("inside my clone" in t for t in texts(run))
    (Path(shop.repo) / "notes.txt").write_text("hello")
    run = run_saying(shop, monkeypatch, 'SKIPPY_POPUP: echo {"text": "x", "_file": "notes.txt"}', task="dddddddd")
    assert popups(run) == [] and any("isn't a picture" in t for t in texts(run))


def test_the_conversation_log_keeps_the_popup_without_its_file():
    kept = conversation_log.clean_events([{"t": "popup", "id": "00112233445566ff", "name": "echo", "args": {"text": "x"},
                                           "input": {"type": "audio/wav", "b64": "AAAA"}}])
    assert kept == [{"t": "popup", "id": "00112233445566ff", "name": "echo", "args": {"text": "x"}}]


def test_the_prompt_teaches_the_popup_line(shop):
    rule = next(line for line in shop.prompt().splitlines() if "SKIPPY_POPUP:" in line)
    assert "services/popups/registry.json" in rule and "_file" in rule and "max 3" in rule
