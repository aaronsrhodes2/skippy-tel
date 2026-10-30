"""The reporter's half of the pop-ups (docs/popups.md): it reads the registry from the mounted services/ folder (again whenever a file
changes), advertises each pop-up whose /health says ready, and runs the generic `popup` job: POST {"args", "input"?} to the service,
check the type it answers with is one of the entry's outputs, hand the bytes to the portal. A skippy-pc pop-up that uses the card
unloads Ollama first and holds the GPU lock, like a picture."""
import json

import pytest

import reporter
from test_music import Fake, _json

ENTRY = {"name": "remove_bg", "family": "vision", "box": "pc", "tier": "quick", "url_env": "REMOVE_BG_URL", "endpoint": "/cut",
         "inputs": {"type": "object", "properties": {}}, "outputs": ["image/png"], "est_seconds": 20, "vram_gb": 3,
         "audience": "everyone", "description": "Cuts the subject out of a picture."}
PNG = b"\x89PNG\r\n\x1a\n" + bytes(40)


@pytest.fixture
def home(monkeypatch, tmp_path):
    (tmp_path / "popups").mkdir()
    (tmp_path / "popups" / "registry.json").write_text(json.dumps({"popups": []}))
    (tmp_path / "vision-popups").mkdir()
    (tmp_path / "vision-popups" / "popups.json").write_text(json.dumps({"popups": [ENTRY, {**ENTRY, "name": "Bad Name"}]}))
    seen = {"svc": [], "portal": [], "ollama": []}

    def svc(method, path, headers, body):
        seen["svc"].append((method, path, json.loads(body or b"{}")))
        if path == "/health":
            return _json({"ready": True})
        return 200, {"Content-Type": svc.ctype}, svc.reply
    svc.ctype, svc.reply = "image/png", PNG

    def ollama(method, path, headers, body):
        seen["ollama"].append(path)
        return _json({"models": [{"name": "qwen3:8b"}]} if path == "/api/ps" else {})

    def portal(method, path, headers, body):
        seen["portal"].append((method, path, headers.get("Content-Type"), body))
        return _json({"ok": True})

    fakes = [Fake(svc), Fake(ollama), Fake(portal)]
    monkeypatch.setattr(reporter, "POPUPS_DIR", str(tmp_path))
    monkeypatch.setenv("REMOVE_BG_URL", fakes[0].url)
    monkeypatch.setattr(reporter, "OLLAMA", fakes[1].url)
    monkeypatch.setattr(reporter, "PORTAL", fakes[2].url)
    yield seen, svc, tmp_path
    for f in fakes:
        f.close()


def test_the_registry_is_read_from_the_mount_and_a_new_entry_needs_no_restart(home):
    _, _, root = home
    assert list(reporter.popups()) == ["remove_bg"]                            # the bad name is skipped
    (root / "audio-popups").mkdir()
    (root / "audio-popups" / "popups.json").write_text(json.dumps({"popups": [{**ENTRY, "name": "stems", "box": "core"}]}))
    assert sorted(reporter.popups()) == ["remove_bg", "stems"]


def test_each_popup_is_advertised_when_its_health_says_ready(home):
    caps = reporter.capabilities()
    assert caps["remove_bg"]["ready"] is True and caps["remove_bg"]["box"] == "pc"
    assert "remove_bg" in reporter.gather()["capabilities"]


def test_a_popup_job_runs_after_ollama_unloads_and_hands_its_result_to_the_portal(home):
    seen, _, _ = home
    job = {"id": "job1", "kind": "popup", "name": "remove_bg", "args": json.dumps({"text": "the dog"}),
           "input_b64": "iVBORw0KGgo=", "input_type": "image/png", "input_name": "dog.png"}
    out = reporter.run_job(job, "tok")
    assert out == {"result": {"title": "the dog", "type": "image/png", "bytes": len(PNG)}}
    assert "/api/ps" in seen["ollama"]                                          # vram_gb > 0 on skippy-pc: the card is freed
    (_, path, body), = [c for c in seen["svc"] if c[1] == "/cut"]
    assert body == {"args": {"text": "the dog"}, "input": {"type": "image/png", "name": "dog.png", "b64": "iVBORw0KGgo="}}
    (method, ppath, ctype, data), = seen["portal"]
    assert method == "PUT" and ppath.startswith("/home/media/job1") and ctype == "image/png" and data == PNG


def test_a_light_or_core_popup_neither_unloads_ollama_nor_waits_for_the_lock(home, monkeypatch):
    seen, _, root = home
    (root / "vision-popups" / "popups.json").write_text(json.dumps({"popups": [{**ENTRY, "vram_gb": 0}]}))
    assert reporter.GPU.acquire(blocking=False)
    try:
        assert "result" in reporter.run_job({"id": "j2", "kind": "popup", "name": "remove_bg", "args": "{}"}, "tok")
    finally:
        reporter.GPU.release()
    assert seen["ollama"] == []


@pytest.mark.parametrize("ctype, why", [("text/html", "isn't one of its outputs"), ("application/json", "isn't one of its outputs")])
def test_a_result_of_the_wrong_type_is_refused(home, ctype, why):
    _, svc, _ = home
    svc.ctype = ctype
    assert why in reporter.run_job({"id": "j3", "kind": "popup", "name": "remove_bg", "args": "{}"}, "tok")["error"]


def test_an_unknown_popup_or_bad_args_fail_plainly(home):
    assert "no pop-up called" in reporter.run_job({"id": "j4", "kind": "popup", "name": "nosuch", "args": "{}"}, "tok")["error"]
    assert "didn't arrive whole" in reporter.run_job({"id": "j5", "kind": "popup", "name": "remove_bg", "args": "[1]"}, "tok")["error"]


# ── big files (docs/popups.md "Big files"): the job names a file the popup-gate kept; only its owner's, only up to the entry's cap ──
def keep(root, ref="d" * 32, owner="0123456789abcdef", data=PNG, ctype="image/png", ext="png"):
    root.mkdir(exist_ok=True)
    (root / f"{ref}.{ext}").write_bytes(data)
    (root / f"{ref}.json").write_text(json.dumps({"id": ref, "owner": owner, "type": ctype, "name": "dog.png", "bytes": len(data)}))
    return ref


def test_a_kept_file_rides_to_the_service_for_its_owner_only(home, monkeypatch, tmp_path):
    seen, _, root = home
    inputs = tmp_path / "inputs"
    monkeypatch.setattr(reporter, "POPUP_INPUTS", str(inputs))
    ref = keep(inputs)
    job = {"id": "big1", "kind": "popup", "name": "remove_bg", "args": "{}", "input_ref": ref, "input_owner": "0123456789abcdef"}
    assert "result" in reporter.run_job(job, "tok")
    (_, _, body), = [c for c in seen["svc"] if c[1] == "/cut"]
    assert body["input"]["type"] == "image/png" and body["input"]["name"] == "dog.png"
    assert reporter.run_job({**job, "input_owner": "ffffffffffffffff"}, "tok") == {"error": "that file isn't yours"}
    assert "gone" in reporter.run_job({**job, "input_ref": "e" * 32}, "tok")["error"]
    assert "malformed" in reporter.run_job({**job, "input_ref": "../../etc"}, "tok")["error"]


def test_a_kept_file_bigger_than_the_entry_takes_is_refused(home, monkeypatch, tmp_path):
    _, _, root = home
    inputs = tmp_path / "inputs"
    monkeypatch.setattr(reporter, "POPUP_INPUTS", str(inputs))
    ref = keep(inputs, data=PNG + bytes(2 * 1024 * 1024))                    # ENTRY has no max_input_mb: 1 MB
    job = {"id": "big2", "kind": "popup", "name": "remove_bg", "args": "{}", "input_ref": ref, "input_owner": "0123456789abcdef"}
    assert "bigger than remove_bg takes (1 MB)" in reporter.run_job(job, "tok")["error"]


def test_the_gate_is_a_capability_with_its_public_address(home, monkeypatch):
    monkeypatch.setattr(reporter, "POPUP_GATE", "")
    assert reporter.capabilities()["popup_gate"] == {"ready": False, "url": reporter.POPUP_GATE_PUBLIC}
