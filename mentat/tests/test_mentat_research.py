"""Mentat keeps everything it outputs (mentat/research.py): the Captain's research data, 2026-10-23.

The writer itself needs nothing; the endpoint tests need a sigma-ground checkout, like the other Mentat suites:
    SIGMA_GROUND_DIR=/path/to/sigma-ground python -m pytest mentat/tests -q
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

import research

BASEBALL = ("If I throw a baseball at 45 degrees at 100 miles per hour, how far will it travel before it "
            "touches the ground on a flat surface?")


def lines(directory: Path) -> list[dict]:
    out = []
    for f in sorted(directory.glob("*.jsonl")):
        out += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def test_a_record_goes_to_this_months_file_whole_and_is_only_ever_appended(tmp_path):
    log = research.Log(directory=str(tmp_path), clock=lambda: dt.datetime(2026, 10, 23, 12, tzinfo=dt.timezone.utc))
    log.record({"endpoint": "/ask", "request": {"question": "q1"}, "response": {"ok": True, "result": {"value": 1}}})
    log.drain()
    log.record({"endpoint": "/ask", "request": {"question": "q2"}})
    log.drain()
    got = lines(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["2026-10.jsonl"]
    assert [g["request"]["question"] for g in got] == ["q1", "q2"]
    assert got[0]["response"] == {"ok": True, "result": {"value": 1}} and got[0]["instance"] == "home"
    assert got[0]["v"] == 1 and got[0]["id"] != got[1]["id"]


def test_the_cloud_copy_uploads_batches_as_new_objects_and_never_loses_a_failed_one(capsys):
    sent = []
    log = research.Log(bucket="b", uploader=lambda bucket, name, body: sent.append((bucket, name, body)))
    for n in range(3):
        log.record({"endpoint": "/tools/x", "request": {"n": n}})
    log.drain()
    (bucket, name, body), = sent
    assert bucket == "b" and name.startswith("mentat-research/") and name.endswith(".jsonl")
    assert [json.loads(x)["request"]["n"] for x in body.decode().splitlines()] == [0, 1, 2]

    def broken(*_):
        raise OSError("no bucket yet")
    log = research.Log(bucket="b", uploader=broken)
    log.record({"endpoint": "/ask", "request": {"question": "kept anyway"}})
    log.drain()
    printed = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert printed[0]["event"] == "mentat_research_unsaved" and printed[0]["record"]["request"]["question"] == "kept anyway"
    assert log.failed == 1


def test_logging_never_raises_into_an_answer(tmp_path):
    log = research.Log(directory=str(tmp_path / "a-file"))
    (tmp_path / "a-file").write_text("not a directory")
    log.record({"endpoint": "/ask", "request": {"question": "x"}, "response": object()})   # unwritable place, odd value
    log.drain()
    assert log.failed == 1
    research.Log().record({"anything": 1})            # nowhere configured: a no-op


@pytest.fixture(scope="module")
def mentat():
    pytest.importorskip("mcp")
    pytest.importorskip("sigma_ground")
    pytest.importorskip("sigma_ground.materia.inputs")      # the suggest-don't-fill sigma-ground
    path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("mentat_app_research", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def kept(mentat, tmp_path, monkeypatch):
    log = research.Log(directory=str(tmp_path))
    monkeypatch.setattr(mentat.research, "LOG", log)
    mentat._hits.clear()

    def read():
        log.drain()
        return lines(tmp_path)
    return mentat.app.test_client(), read


def test_the_baseball_question_is_kept_in_full_with_its_trace_id(kept):
    client, read = kept
    r = client.post("/ask", json={"question": BASEBALL}, headers={"X-Skippy-Trace": "chat-1234", "X-Skippy-Caller": "test"})
    result = r.get_json()["result"]
    assert result["value"] == pytest.approx(203.785, rel=1e-3) and result["units"] == "m"
    rec, = read()
    assert rec["trace"] == "chat-1234" and rec["endpoint"] == "/ask" and rec["caller"]["via"] == "test"
    assert rec["request"] == {"question": BASEBALL, "given": None}
    assert rec["response"] == r.get_json()                      # the whole answer, as sent
    step, = rec["understood"]["steps"]
    assert step["verb"] == "projectile_motion"
    assert step["params"]["speed_m_s"] == pytest.approx(44.704) and step["units"]["speed_m_s"] == "m/s"
    assert rec["status"] == 200 and rec["ok"] and rec["ms"] >= 0


def test_a_missing_input_gets_a_suggestion_and_a_yes_computes_with_it(kept):
    client, read = kept
    first = client.post("/ask", json={"question": "range of a projectile launched at 45 degrees"}).get_json()["result"]
    assert first["value"] is None and first["clarification"]["needs"] == ["speed_m_s"]
    assert first["clarification"]["suggested"]["speed_m_s"]["value"] == 30.0
    follow = first["clarification"]["follow_up"]
    second = client.post("/ask", json=follow).get_json()["result"]
    assert second["value"] == pytest.approx(91.77, rel=1e-3)
    recs = read()
    assert [r["request"]["given"] for r in recs] == [None, {"speed_m_s": 30.0}]
    assert recs[0]["response"]["result"]["clarification"] == first["clarification"]


def test_given_must_be_plain_values_and_tool_calls_and_refusals_are_kept_too(kept):
    client, read = kept
    assert client.post("/ask", json={"question": "q", "given": {"a": [1]}}).status_code == 400
    assert client.post("/tools/convert_units", json={"value": 1, "from_units": "mile", "to_units": "km"}).status_code == 200
    assert client.post("/tools/no_such_tool", json={}).status_code == 404
    recs = read()
    assert [r["endpoint"] for r in recs] == ["/ask", "/tools/convert_units", "/tools/no_such_tool"]
    assert recs[1]["response"]["result"]["value"] == pytest.approx(1.609344)
    assert recs[2]["status"] == 404 and not recs[2]["ok"]


def test_mcp_tool_calls_are_kept(kept):
    client, read = kept
    client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "convert_units", "arguments": {"value": 2, "from_units": "m", "to_units": "cm"}}})
    rec, = read()
    assert rec["endpoint"] == "/mcp" and rec["tool"] == "convert_units"
    assert rec["request"] == {"value": 2, "from_units": "m", "to_units": "cm"}
    assert json.loads(rec["response"]["mcp"]["content"][0]["text"])["value"] == pytest.approx(200)
