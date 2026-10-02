"""The bridge's Skippy Memory doors: a pass imports, the list and one conversation read back, bad keys are refused, and a
missing Drive is a reason, not a crash."""
from pathlib import Path

import memory_service

GROK = Path(__file__).resolve().parents[3] / "memory" / "tests" / "fixtures" / "grok_export.json"


def test_a_pass_imports_the_inbox_and_the_reads_serve_it(tmp_path):
    root = tmp_path / "My Drive" / "Skippy Memory"
    (root / "inbox").mkdir(parents=True)
    (root / "inbox" / "grok.json").write_bytes(GROK.read_bytes())
    svc = memory_service.MemoryService(root=root, claude_home=tmp_path / "none")
    assert svc.run_once()["exports"]["grok.json"] == "2 new, 0 updated"
    status, body = svc.listing()
    assert status == 200 and body["total"] == 2 and body["last_error"] == ""
    key = body["conversations"][0]["key"]
    status, conv = svc.read(key)
    assert status == 200 and conv["key"] == key and conv["text"]
    assert svc.read("grok:missing")[0] == 404
    assert svc.read("../../etc/passwd")[0] == 400


def test_without_drive_it_says_why_and_lists_nothing(tmp_path):
    svc = memory_service.MemoryService(root=tmp_path / "nowhere" / "Skippy Memory", claude_home=tmp_path)
    assert svc.run_once() is None and "Google Drive" in svc.last_error
    assert svc.listing()[0] == 503
