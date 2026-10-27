"""Runs survive a restart of the bridge itself. Before this, the bridge held every run only in memory: a crash, a
reboot or the supervisor's restart erased the chat's answer, and the chat then waited for a run that no longer existed.

Every test here points the Workshop at a tmp state directory (make_shop does), never the real one. A "restart" is a
second Workshop over the same directory, loaded the way bridge.main() loads it."""
import json
import os
import time
from pathlib import Path

import bridge
import workshop_engine as engine
from bridge_testkit import finish, make_door, make_shop, saved, wait_for

BODY = {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "a message that must not be written to disk"}
FIELDS = {"task", "conv", "who", "project", "brain", "client_id", "created", "finished_at", "done", "ok", "error", "events"}


def write_run(shop, task, *, done, finished_ago=None, age=None, who="c@example.com", events=None, error=None, ok=None):
    """A run's file as an earlier bridge process would have left it (`age` seconds old by the file's own clock)."""
    root = Path(shop.state_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    path = root / f"{task}.json"
    path.write_text(json.dumps({"task": task, "conv": "conv-12345678", "who": who, "created": now - 500,
                                "finished_at": None if finished_ago is None else now - finished_ago, "done": done,
                                "ok": ok, "error": error, "events": events or []}), encoding="utf-8")
    if age is not None:
        os.utime(path, (now - age, now - age))
    return path


# ── saving ──────────────────────────────────────────────────────────────────
def test_a_finished_run_is_saved_with_exactly_the_documented_fields(shop):
    run = finish(shop.start(BODY["task"], BODY["conv"], BODY["message"], who="captain@example.com"))
    data = saved(shop, "aaaaaaaa", lambda d: d["done"])
    assert set(data) == FIELDS
    assert (data["task"], data["conv"], data["who"]) == ("aaaaaaaa", "conv-12345678", "captain@example.com")
    assert data["done"] is True and data["ok"] is True and data["error"] is None
    assert data["events"] == run.events and len(data["events"]) == 4
    assert data["created"] <= data["finished_at"] and isinstance(data["created"], float)
    assert BODY["message"] not in json.dumps(data)                               # nothing needs the message again
    assert not [p for p in Path(shop.state_dir).iterdir() if p.name.endswith(".tmp")]     # the write was atomic: no litter


def test_a_run_is_on_disk_from_the_moment_it_starts(shop, monkeypatch):
    """Without this a bridge that dies seconds in would have nothing to find, and the chat would never learn it ended."""
    monkeypatch.setenv("FAKE_MODE", "slow")
    run = shop.start(BODY["task"], BODY["conv"], BODY["message"], who="c@example.com")
    try:
        data = saved(shop, "aaaaaaaa")
        assert data["done"] is False and data["ok"] is None and data["finished_at"] is None and data["who"] == "c@example.com"
    finally:
        shop.cancel("aaaaaaaa")
        finish(run)


def test_while_running_a_run_is_written_at_most_every_few_seconds(shop, monkeypatch):
    monkeypatch.setattr(engine, "SAVE_EVERY_S", 3600)
    run = engine.Run("aaaaaaaa", "conv-12345678", "m", "c@example.com")
    shop.adopt(run)
    assert saved(shop, "aaaaaaaa")["events"] == []
    for i in range(3):
        run.emit({"t": "text", "text": f"event {i}"})
    assert saved(shop, "aaaaaaaa")["events"] == []                               # throttled: not a disk write per event
    run.finish(True)
    data = saved(shop, "aaaaaaaa", lambda d: d["done"])                          # but the end is always written, in full
    assert [e["text"] for e in data["events"]] == ["event 0", "event 1", "event 2"] and data["ok"] is True


def test_with_no_throttle_every_event_is_written(shop, monkeypatch):
    monkeypatch.setattr(engine, "SAVE_EVERY_S", 0)
    run = engine.Run("aaaaaaaa", "conv-12345678", "m", "c@example.com")
    shop.adopt(run)
    for i in range(3):
        run.emit({"t": "text", "text": f"event {i}"})
        assert len(saved(shop, "aaaaaaaa")["events"]) == i + 1


# ── a failing disk never breaks or stops a run ──────────────────────────────
def test_a_state_directory_that_cannot_be_created_does_not_touch_the_run(shop, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(engine, "SAVE_RETRY_S", (0.01,) * 6)                     # the real pauses add up to seconds
    blocker = tmp_path / "a-file-where-a-directory-should-be"
    blocker.write_text("x")
    broken = engine.Workshop(shop.claude_cmd, repo=shop.repo, primary=shop.primary, sessions=str(tmp_path / "s2.json"),
                             remote=str(shop.origin), state_dir=str(blocker / "runs"))
    run = finish(broken.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is True and run.error is None and [e["t"] for e in run.events] == ["text", "tool", "tool", "text"]
    assert broken.listing()[0]["done"] is True                                   # and it is served from memory as usual
    err = capsys.readouterr().err
    assert err.count("couldn't save run aaaaaaaa") == 1                          # said once, not once per write


def test_a_failing_replace_does_not_touch_the_run_either(shop, monkeypatch, capsys):
    monkeypatch.setattr(engine, "SAVE_RETRY_S", (0.01,) * 6)

    def refuse(*args, **kwargs):
        raise PermissionError("[WinError 5] Access is denied (the antivirus has the file)")
    monkeypatch.setattr(engine.os, "replace", refuse)
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is True and len(run.events) == 4
    assert "couldn't save run aaaaaaaa" in capsys.readouterr().err
    assert not (Path(shop.state_dir) / "aaaaaaaa.json").exists()


def test_the_creation_write_tries_again_when_the_disk_is_busy_for_a_moment(shop, monkeypatch):
    """Windows holds a new file briefly (antivirus, the search indexer): os.replace says "Access is denied"."""
    monkeypatch.setattr(engine, "SAVE_RETRY_S", (0.01,) * 6)
    real, failures = os.replace, []

    def flaky(src, dst):
        if len(failures) < 2:
            failures.append(src)
            raise PermissionError("busy")
        return real(src, dst)
    monkeypatch.setattr(engine.os, "replace", flaky)
    monkeypatch.setenv("FAKE_MODE", "slow")
    run = shop.start("aaaaaaaa", "conv-12345678", "hi")
    try:
        assert len(failures) == 2 and saved(shop, "aaaaaaaa")["done"] is False       # on disk after two refusals
    finally:
        shop.cancel("aaaaaaaa")
        finish(run)


def test_the_final_write_waits_out_a_busy_disk_because_nothing_rewrites_it(shop, monkeypatch):
    """Found on a loaded PC: a reader holding the file made os.replace fail on every try of the end-of-run write, the
    run's file kept saying "still running", and after a restart the answer would have been lost. The end of a run is
    the one write nobody redoes, so it tries many more times than the creation write does."""
    monkeypatch.setattr(engine, "SAVE_RETRY_S", (0.01,) * 6)
    real, failures = os.replace, []

    def refuse_the_final_write_six_times(src, dst):
        if str(dst).endswith("aaaaaaaa.json") and json.loads(Path(src).read_text(encoding="utf-8"))["done"] and len(failures) < 6:
            failures.append(src)
            raise PermissionError("[WinError 5] Access is denied")
        return real(src, dst)
    monkeypatch.setattr(engine.os, "replace", refuse_the_final_write_six_times)
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert len(failures) == 6 and run.ok is True
    data = saved(shop, "aaaaaaaa")
    assert data["done"] is True and data["ok"] is True and data["events"] == run.events


# ── loading ─────────────────────────────────────────────────────────────────
def test_a_finished_run_comes_back_after_a_restart_and_can_be_followed_again(shop, tmp_path, monkeypatch):
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi", who="captain@example.com"))
    saved(shop, "aaaaaaaa", lambda d: d["done"])
    restarted = make_shop(tmp_path, monkeypatch)
    assert restarted.get("aaaaaaaa") is None                                     # nothing until main() loads
    assert restarted.load() == 1
    back = restarted.get("aaaaaaaa")
    assert back.since(0) == run.since(0) and back.since(2)["events"] == run.events[2:]
    assert (back.conv, back.who, back.created, back.finished_at) == (run.conv, "captain@example.com", run.created, run.finished_at)
    assert back.done is True and back.ok is True and back.message == ""
    assert restarted.start("aaaaaaaa", "conv-12345678", "", attach=True) is back             # re-attach, no new claude run
    assert not restarted.busy() and len(restarted.listing()) == 1


def test_a_run_that_was_running_when_the_bridge_died_is_closed_with_the_restart_error(shop):
    write_run(shop, "cccccccc", done=False, events=[{"t": "text", "text": "half way"}], age=30)
    assert shop.load() == 1
    run = shop.get("cccccccc")
    assert run.done is True and run.ok is False and run.error == "The bridge restarted while this was running."
    assert run.events == [{"t": "text", "text": "half way"}] and run.who == "c@example.com"
    assert 0 <= time.time() - run.finished_at < 30                               # finished NOW, so it stays deliverable for an hour
    data = saved(shop, "cccccccc", lambda d: d["done"])                          # and re-saved closed: a second restart agrees
    assert data["ok"] is False and data["error"] == run.error
    assert not shop.busy()                                                       # the chat can send its next message
    again = engine.Workshop(shop.claude_cmd, repo=shop.repo, primary=shop.primary, sessions=shop.sessions_path,
                            remote=shop.remote)
    assert again.load() == 1 and again.get("cccccccc").error == run.error


def test_a_real_run_cut_off_by_a_restart_is_closed_the_same_way(shop, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")
    run = shop.start("aaaaaaaa", "conv-12345678", "hi", who="captain@example.com")
    try:
        saved(shop, "aaaaaaaa")
        restarted = make_shop(tmp_path, monkeypatch)                             # the old process is "gone": a new one over the disk
        assert restarted.load() == 1
        closed = restarted.get("aaaaaaaa")
        assert closed.done and closed.ok is False and closed.error == engine.RESTART_ERROR and closed.who == "captain@example.com"
        assert restarted.listing()[0]["done"] is True and not restarted.busy()
    finally:
        shop.cancel("aaaaaaaa")
        finish(run)


def test_old_and_broken_files_are_dropped_and_never_stop_the_start(shop):
    root = Path(shop.state_dir)
    write_run(shop, "dddddddd", done=True, ok=True, finished_ago=engine.KEEP_FINISHED_S + 60)        # finished long ago
    write_run(shop, "eeeeeeee", done=False, age=engine.KEEP_FINISHED_S + 60)                         # died long ago
    write_run(shop, "ffffffff", done=True, ok=True, finished_ago=100)                                # recent: kept
    (root / "abababab.json.tmp").write_text("half a write")
    (root / "99999999.json").write_text("{not json")                                                 # corrupt but recent
    (root / "88888888.json").write_text("[1, 2]")
    (root / "a1a1a1a1.json").write_text(json.dumps({"task": "b2b2b2b2", "conv": "conv-12345678", "events": []}))   # wrong task
    (root / "c3c3c3c3.json").write_text(json.dumps({"task": "c3c3c3c3", "conv": "x", "events": []}))                # bad conv
    (root / "d4d4d4d4.json").write_text(json.dumps({"task": "d4d4d4d4", "conv": "conv-12345678", "events": "no"}))   # bad events
    (root / "notes.txt").write_text("not a run")
    old_corrupt = root / "77777777.json"
    old_corrupt.write_text("garbage")
    os.utime(old_corrupt, (time.time() - engine.KEEP_FINISHED_S - 60,) * 2)
    assert shop.load() == 1
    assert [r["task"] for r in shop.listing()] == ["ffffffff"]
    left = sorted(p.name for p in root.iterdir())
    assert "dddddddd.json" not in left and "eeeeeeee.json" not in left and "abababab.json.tmp" not in left
    assert "77777777.json" not in left                                           # long-unreadable files stop piling up
    assert "99999999.json" in left and "notes.txt" in left                       # recent unreadable ones are left alone


def test_loading_with_no_state_directory_is_a_quiet_zero(shop):
    assert not Path(shop.state_dir).exists()
    assert shop.load() == 0


def test_a_loaded_run_does_not_replace_one_already_held(shop):
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    saved(shop, "aaaaaaaa", lambda d: d["done"])
    assert shop.load() == 0 and shop.get("aaaaaaaa") is run


def test_pruning_a_run_removes_its_file(shop):
    first = finish(shop.start("aaaaaaaa", "conv-12345678", "one"))
    saved(shop, "aaaaaaaa", lambda d: d["done"])
    first.finished_at -= engine.KEEP_FINISHED_S + 1
    finish(shop.start("bbbbbbbb", "conv-12345678", "two"))                       # starting a task prunes
    assert shop.get("aaaaaaaa") is None
    wait_for(lambda: not (Path(shop.state_dir) / "aaaaaaaa.json").exists(), what="the pruned run's file to go")
    assert (Path(shop.state_dir) / "bbbbbbbb.json").exists()


# ── the real bridge ─────────────────────────────────────────────────────────
def test_after_a_restart_the_door_serves_the_old_run_and_lists_it(shop, tmp_path, monkeypatch):
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi", who="captain@example.com"))
    saved(shop, "aaaaaaaa", lambda d: d["done"])
    restarted = make_shop(tmp_path, monkeypatch)
    restarted.load()
    call, server = make_door(restarted, monkeypatch)
    try:
        status, got = call("GET", "/workshop/run/aaaaaaaa?after=0")
        assert status == 200 and got["done"] is True and got["ok"] is True and got["events"] == run.events
        status, listed = call("GET", "/workshop/runs")
        assert status == 200 and [(r["task"], r["who"], r["events"], r["done"]) for r in listed["runs"]] == [
            ("aaaaaaaa", "captain@example.com", 4, True)]
    finally:
        server.shutdown()
        server.server_close()


class _Server:
    """Stands in for ThreadingHTTPServer so main() can run to the point of serving and stop."""
    served = False

    def __init__(self, *args, **kwargs):
        pass

    def serve_forever(self):
        type(self).served = True
        raise KeyboardInterrupt


def test_main_restores_the_saved_runs_before_it_serves(shop, monkeypatch):
    write_run(shop, "ffffffff", done=True, ok=True, finished_ago=10)
    monkeypatch.setattr(bridge, "WORKSHOP", shop)
    monkeypatch.setattr(bridge, "ThreadingHTTPServer", _Server)
    _Server.served = False
    bridge.main()
    assert _Server.served and shop.get("ffffffff") is not None


def test_a_state_directory_that_blows_up_on_load_does_not_stop_the_bridge_starting(shop, monkeypatch):
    def boom():
        raise RuntimeError("the disk is on fire")
    monkeypatch.setattr(shop, "load", boom)
    monkeypatch.setattr(bridge, "WORKSHOP", shop)
    monkeypatch.setattr(bridge, "ThreadingHTTPServer", _Server)
    _Server.served = False
    bridge.main()
    assert _Server.served                                                        # being reachable beats remembering


def test_importing_the_bridge_never_reads_or_writes_the_real_state_directory():
    """The module-level Workshop must not load on construction: a test (or anything) importing bridge would otherwise
    re-save the Captain's real runs."""
    real = Path(bridge.WORKSHOP.state_dir)
    assert real.name == engine.STATE_DIR_NAME and real.parent == Path(engine.SESSIONS).parent
    assert bridge.WORKSHOP.listing() == []                                       # not loaded: nothing in memory
