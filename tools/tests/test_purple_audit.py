"""tools/purple_audit.py on recorded runs: conversation indexes as the bridge writes them, with and without a `chain` event."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import purple_audit as pa  # noqa: E402

DAY = "2026-11-01"
T0 = time.mktime(time.strptime(DAY, "%Y-%m-%d")) + 3600


def turn(message, events, at, state="done"):
    return {"task": "a" * 16, "at": at, "message": message, "events": events, "state": state, "error": None}


def write(folder: Path, conv: str, turns):
    (folder / f"{DAY}_{conv}.turns.json").write_text(json.dumps({"conv": conv, "project": "skippytel", "who": "c", "title": conv,
                                                                  "started": T0, "turns": turns, "last": T0}), encoding="utf-8")


def recorded(tmp_path):
    write(tmp_path, "conv-a", [
        # a new run: its chain event, with the bridge's own flags
        turn("fix the tile colour and push it", [
            {"t": "tool", "name": "Edit", "detail": "portal/static/x.css"},
            {"t": "text", "text": "Fixed and pushed."},
            {"t": "chain", "calls": [{"n": "Edit", "d": "portal/static/x.css", "k": "edit", "ms": 30, "ok": True}],
             "flags": [{"rule": "push", "text": "says it pushed or committed; no git push or commit call"},
                       {"rule": "untested", "text": "edited files; no test run"}]}], T0),
        # an old run: rebuilt from its tool lines
        turn("deploy it", [{"t": "tool", "name": "Bash", "detail": "git push origin main"},
                           {"t": "tool", "name": "Bash", "detail": "curl -s https://makerportal.live/version"},
                           {"t": "text", "text": "Pushed; the portal serves it, live."}], T0 + 60),
        turn("what's my car's charge?", [{"t": "tool", "name": "mcp__skippy-house__home_state", "detail": ""},
                                          {"t": "text", "text": "80%."}], T0 + 120),
        turn("still going", [{"t": "text", "text": "Pushed."}], T0 + 180, state="running"),       # not judged until it ends
    ])
    write(tmp_path, "conv-b", [turn("why is CI red?", [{"t": "tool", "name": "Bash", "detail": "git push origin main"},
                                                        {"t": "text", "text": "Looked at it."}], T0 - 86400)])


def test_it_reads_every_finished_reply_judges_each_and_says_the_day_in_one_line(tmp_path):
    recorded(tmp_path)
    rows = pa.replies(tmp_path)
    assert [r["message"] for r in rows] == ["why is CI red?", "fix the tile colour and push it", "deploy it", "what's my car's charge?"]
    assert [r["timed"] for r in rows] == [False, True, False, False]
    assert [[f["rule"] for f in r["flags"]] for r in rows] == [["unsaid-push"], ["push", "untested"], [], []]
    assert [r["kind"] for r in rows] == ["look", "ship", "ship", "house"]
    result = pa.audit(rows)
    assert result["replies"] == 4 and result["flagged"] == 2 and result["by_rule"] == {"unsaid-push": 1, "push": 1, "untested": 1}
    assert result["kinds"]["ship"]["common_shapes"][0][1] == 1 and result["kinds"]["house"]["tools"] == [("mcp__skippy-house__home_state", 1)]
    day = pa.day_rows(DAY, tmp_path)
    assert len(day) == 3                                                      # yesterday's conv-b is not today's
    assert pa.ledger_line(day) == "purple: 1 reply claimed a push they didn't make, 1 reply edited files with no test run (of 3)"
    assert pa.ledger_line([]) == "purple: no replies"
    assert pa.ledger_line(pa.day_rows(DAY, tmp_path)[2:]) == "purple: 1 replies, every claim backed by a call"


def test_the_shape_squeezes_runs_and_the_report_prints(tmp_path, capsys):
    assert pa.shape([{"k": "read"}] * 3 + [{"k": "edit"}, {"k": "git"}, {"k": "git"}]) == "R3 E G2"
    assert pa.shape([]) == "-"
    assert pa.shape([{"k": k} for k in ["read", "edit"] * 20]).endswith("…+24")
    recorded(tmp_path)
    assert pa.main(["--folder", str(tmp_path), "--runs", "10"]) == 0
    out = capsys.readouterr().out
    assert "4 replies, 2 with a mismatch" in out and "claimed a push they didn't make" in out and out.strip().endswith("(of 4)")
    assert pa.main(["--folder", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["flagged"] == 2


def test_the_nightly_ledger_carries_the_line(tmp_path, monkeypatch):
    import nightly
    recorded(tmp_path)
    monkeypatch.setattr(pa, "CONVERSATIONS", tmp_path)
    monkeypatch.setattr(pa, "day_rows", lambda date, folder=tmp_path: pa.replies(tmp_path, 0)[1:])
    assert nightly.purple_day(DAY).startswith("purple: 1 reply claimed a push")
