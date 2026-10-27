"""The HTTP face of vision-popups, with the models stubbed out (no GPU): errors map to the codes the patient queue reads,
and the registry stays consistent."""
import base64
import io
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


class _OOM(RuntimeError):
    pass


fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(reset_peak_memory_stats=lambda: None, max_memory_reserved=lambda: 3 * 2**20,
                                                              OutOfMemoryError=_OOM))
import app as server  # noqa: E402
import models as m  # noqa: E402
import tools  # noqa: E402


def picture() -> str:
    out = io.BytesIO()
    Image.new("RGB", (16, 12), (40, 80, 120)).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(m, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(m, "WEIGHTS", tmp_path / "weights")
    return server.app.test_client()


def stub(monkeypatch, fn):
    monkeypatch.setitem(tools.TOOLS["remove_background"], "run", fn)


def test_a_run_hands_back_pictures_text_and_data(client, monkeypatch):
    seen = {}

    def fake(rgb, alpha, params):
        seen.update(size=rgb.size, params=params)
        return {"text": "done", "outputs": [server.ik.encode(rgb, "cutout")], "data": {"n": 1}}
    stub(monkeypatch, fake)
    r = client.post("/run/remove_background", json={"image": picture(), "background": "black"})
    assert r.status_code == 200, r.json
    j = r.json
    assert j["text"] == "done" and j["data"] == {"n": 1} and j["model"] == "birefnet" and j["licence"] == "MIT"
    assert j["outputs"][0]["name"] == "cutout" and base64.b64decode(j["outputs"][0]["b64"]).startswith(b"\x89PNG")
    assert seen["size"] == (16, 12) and seen["params"]["background"] == "black"
    assert j["peak_mb"] == 3


def test_errors_map_to_what_the_queue_reads(client, monkeypatch):
    def busy(*_):
        raise m.CardBusy("full")
    stub(monkeypatch, busy)
    r = client.post("/run/remove_background", json={"image": picture()})
    assert r.status_code == 503 and r.json["busy"] is True

    def oom(*_):
        raise _OOM()
    stub(monkeypatch, oom)
    r = client.post("/run/remove_background", json={"image": picture()})
    assert r.status_code == 503 and r.json["busy"] is True

    def wrong(*_):
        raise server.ik.BadInput("say what to look for")
    stub(monkeypatch, wrong)
    r = client.post("/run/remove_background", json={"image": picture()})
    assert r.status_code == 400 and "look for" in r.json["error"]

    def broken(*_):
        raise ValueError("kaboom")
    stub(monkeypatch, broken)
    r = client.post("/run/remove_background", json={"image": picture()})
    assert r.status_code == 500 and "kaboom" in r.json["error"]


def test_bad_requests(client):
    assert client.post("/run/nope", json={"image": picture()}).status_code == 404
    assert client.post("/run/upscale", data="x").status_code == 400
    assert client.post("/run/upscale", json={"image": ""}).status_code == 400
    assert client.post("/run/upscale", json={"image": "AAAA"}).status_code == 400


def test_health_is_ready_only_with_weights(client, tmp_path):
    j = client.get("/health").json
    assert j["ready"] is False and j["loaded"] is False and j["models_loaded"] == []
    (tmp_path / "archive" / "ZhengPeng7__BiRefNet").mkdir(parents=True)
    j = client.get("/health").json
    assert j["ready"] is True and j["tools"]["remove_background"]["ready"] and not j["tools"]["ocr"]["ready"]


def test_every_tool_names_registered_models_and_licences(client):
    for name, t in tools.TOOLS.items():
        assert t["default"] in t["models"], name
        for model in t["models"]:
            assert model in m.MODELS, model
            assert m.MODELS[model].licence and m.MODELS[model].need_mb > 0
    j = client.get("/tools").json
    assert set(j["tools"]) == set(tools.TOOLS) and "birefnet" in j["models"]


def test_a_model_not_offered_for_a_tool_is_refused(client):
    r = client.post("/run/remove_background", json={"image": picture(), "model": "sam2.1_large"})
    assert r.status_code == 400 and "birefnet" in r.json["error"]


def test_card_busy_rather_than_overcommitting(monkeypatch):
    monkeypatch.setattr(m, "free_mb", lambda: 1000)
    monkeypatch.setattr(m, "_loaded", {})
    with pytest.raises(m.CardBusy):
        m.use("birefnet")


def test_seeding_copies_the_archive_once(monkeypatch, tmp_path):
    src = tmp_path / "archive" / "ZhengPeng7__BiRefNet"
    (src / ".cache").mkdir(parents=True)
    (src / "model.safetensors").write_bytes(b"w" * 10)
    (src / ".cache" / "note").write_text("x")
    monkeypatch.setattr(m, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(m, "WEIGHTS", tmp_path / "weights")
    dest = m.path("birefnet")
    assert (dest / "model.safetensors").read_bytes() == b"w" * 10 and not (dest / ".cache").exists()
    (src / "model.safetensors").unlink()
    assert m.path("birefnet") == dest                      # seeded: never re-read from the slow disk


def test_the_popup_call_hands_back_one_result_under_its_type(client, monkeypatch):
    def fake(rgb, alpha, params):
        return {"text": "cut", "outputs": [server.ik.encode(rgb, "cutout"), server.ik.encode(rgb.convert("L"), "mask")],
                "data": {"coverage": 0.5}}
    stub(monkeypatch, fake)
    given = {"type": "image/png", "name": "p.png", "b64": picture()}
    r = client.post("/popup/remove_background", json={"args": {}, "input": given})
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data.startswith(b"\x89PNG")
    r = client.post("/popup/remove_background", json={"args": {"output": "mask"}, "input": given})
    assert r.status_code == 200 and r.mimetype == "image/png"
    r = client.post("/popup/remove_background", json={"args": {"output": "json"}, "input": given})
    assert r.mimetype == "application/json" and r.json["data"] == {"coverage": 0.5} and r.json["text"] == "cut"
    r = client.post("/popup/remove_background", json={"args": {"output": "nope"}, "input": given})
    assert r.status_code == 400 and "cutout, mask" in r.json["error"]
    r = client.post("/popup/remove_background", json={"args": {}})
    assert r.status_code == 400 and "picture" in r.json["error"]
    assert client.post("/popup/remove_background", json={"args": "x"}).status_code == 400


def test_ocr_comes_back_as_plain_text(client, monkeypatch):
    monkeypatch.setitem(tools.TOOLS["ocr"], "run", lambda rgb, alpha, params: {"text": "Größe 12 × 7", "outputs": [], "data": {}})
    r = client.post("/popup/ocr", json={"args": {}, "input": {"type": "image/png", "b64": picture()}})
    assert r.status_code == 200 and r.mimetype == "text/plain" and r.data.decode("utf-8") == "Größe 12 × 7"


def test_every_registered_popup_calls_a_real_tool_and_names_its_outputs():
    import json
    entries = json.loads((HERE / "popups.json").read_text(encoding="utf-8"))["popups"]
    assert {e["endpoint"].split("/")[-1] for e in entries} == set(tools.TOOLS)
    for e in entries:
        tool = e["endpoint"].split("/")[-1]
        assert e["endpoint"] == f"/popup/{tool}" and e["family"] == "vision" and e["box"] == "pc"
        assert e["url"].endswith(":8004") and e["url_env"] == "VISION_POPUPS_URL"
        model = e["inputs"]["properties"].get("model", {}).get("enum")
        if model:
            assert set(model) == set(tools.TOOLS[tool]["models"]), e["name"]
        assert e["media_required"] and e["vram_gb"] * 1024 >= m.MODELS[tools.TOOLS[tool]["default"]].need_mb * 0.9, e["name"]
