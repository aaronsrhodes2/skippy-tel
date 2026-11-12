"""The bridge's front door (front.py): a hand-over starts a new bridge generation for new work while the old one finishes its run.
Also the bridge's side of it: a hand-over generation leaves the older one's live runs alone and takes back finished ones from disk,
and the SkippyTel fix lane every conversation may work in (lane_guard.py)."""
import json
import time
from pathlib import Path

import front as fr
import lane_guard as lg
import workshop_engine as engine
from test_bridge_persistence import write_run

AUTH = {"Authorization": "Bearer t", "Content-Type": "application/json"}


class Proc:
    def __init__(self, pid):
        self.pid, self.dead = pid, False

    def poll(self):
        return 1 if self.dead else None


class World:
    """Fake bridge generations, one per port: what each one's /health and /workshop/runs say."""

    def __init__(self):
        self.runs: dict[int, list] = {}
        self.calls: list = []
        self.bodies: list = []
        self.procs: dict[int, Proc] = {}
        self.killed: list[int] = []
        self.code = "c1"

    def spawn(self, port, n, handover):
        self.runs[port] = []
        p = self.procs[port] = Proc(1000 + port)
        p.handover = handover
        return p

    def request(self, method, url, body=None, headers=None, timeout=10):
        port = int(url.split(":")[2].split("/")[0])
        path = "/" + url.split("/", 3)[3]
        self.calls.append((method, port, path))
        self.bodies.append((path, body))
        if port not in self.runs or self.procs[port].dead:
            return 0, b""
        if path.startswith("/health"):
            return 200, json.dumps({"status": "ok", "workshop": {"busy": any(not r["done"] for r in self.runs[port])}}).encode()
        if path.startswith("/workshop/runs"):
            return (200, json.dumps({"runs": self.runs[port]}).encode()) if (headers or {}).get("Authorization") else (401, b"{}")
        if path == "/workshop" and method == "POST":
            task = json.loads(body)["task"]
            self.runs[port].append({"task": task, "done": False, "age_s": 0, "brain": json.loads(body).get("brain") or "core"})
            return 200, json.dumps({"ok": True, "task": task, "done": False}).encode()
        return 200, b"{}"

    def kill(self, pid):
        self.killed.append(pid)
        for p in self.procs.values():
            if p.pid == pid:
                p.dead = True


def make(tmp_path):
    w = World()
    f = fr.Front(spawn=w.spawn, request=w.request, state=tmp_path / "front.json", code=lambda: w.code, kill=w.kill)
    f.start_gen(handover=False)
    return w, f


def start(f, task, brain=None):
    return f.route("POST", "/workshop", "", json.dumps({"task": task, "conv": "conv-12345678", "message": "m", **({"brain": brain} if brain else {})}).encode(), AUTH)


def test_new_work_goes_to_the_newest_and_a_run_stays_with_the_bridge_that_holds_it(tmp_path):
    w, f = make(tmp_path)
    old = f.current()
    assert start(f, "task-old1")[0] == 200
    ok, summary = f.handover("test")
    assert ok and "finishes its run" in summary and w.procs[f.current().port].handover is True
    new = f.current()
    assert new.port != old.port and old.draining
    f.route("GET", "/workshop/run/task-old1", "after=0&wait=0", b"", AUTH)
    assert w.calls[-1] == ("GET", old.port, "/workshop/run/task-old1?after=0&wait=0")


def test_one_queue_a_new_run_waits_for_the_older_generations_run(tmp_path):
    w, f = make(tmp_path)
    start(f, "task-old1")
    f.handover("test")
    status, raw = start(f, "task-new1")
    assert status == 409 and json.loads(raw)["error"] == "busy"
    w.runs[f.gens[0].port][0]["done"] = True
    assert start(f, "task-new1")[0] == 200 and any(r["task"] == "task-new1" for r in w.runs[f.current().port])


def test_one_queue_per_brain_an_older_generations_core_run_does_not_hold_up_claude(tmp_path):
    """The Captain, 2026-11-05: 'even on auto orange it stuck me in line and never followed up', behind an eleven-hour Core run in a draining generation."""
    w, f = make(tmp_path)
    start(f, "task-old1", brain="core")
    f.handover("test")
    assert start(f, "task-new1", brain="core")[0] == 409                      # Core waits for Core ...
    assert start(f, "task-new2", brain="claude")[0] == 200                    # ... Claude does not
    assert any(r["task"] == "task-new2" for r in w.runs[f.current().port])
    assert start(f, "task-new3")[0] == 409                                    # no brain named is Core (purple), as everywhere


def test_a_drained_generation_is_stopped_and_its_runs_adopted_by_the_new_one(tmp_path):
    w, f = make(tmp_path)
    start(f, "task-old1")
    f.handover("test")
    old = f.gens[0]
    f.tick()
    assert old in f.gens                              # still running: never stopped mid-run
    w.runs[old.port][0]["done"] = True
    f.tick()
    assert old not in f.gens and old.pid in w.killed
    assert ("POST", f.current().port, "/workshop/adopt") in w.calls


def test_the_adopt_after_one_old_generation_goes_spares_the_run_another_old_one_still_has(tmp_path):
    w, f = make(tmp_path)
    start(f, "task-old1")
    f.handover("one")                                 # generation 1 keeps task-old1
    f.handover("two")                                 # generation 2 (idle) drains at once
    f.tick()
    assert len(f.gens) == 2 and f.gens[0].n == 1      # the one with the run stays
    (body,) = [b for path, b in w.bodies if path == "/workshop/adopt"]
    assert json.loads(body) == {"keep": ["task-old1"]}  # ... and the adopt is told to leave its run alone (2026-10-31)


def test_adopt_leaves_a_run_another_generation_holds(shop):
    path = write_run(shop, "abcdefab", done=False)
    before = path.read_text(encoding="utf-8")
    assert shop.load(close_unfinished=True, keep={"abcdefab"}) == 0
    assert path.read_text(encoding="utf-8") == before


def test_a_run_wrongly_closed_here_takes_the_real_ending_its_own_generation_wrote(shop):
    write_run(shop, "abcdefab", done=False)
    shop.load(close_unfinished=True)                                   # the old bug: closed here as "bridge restarted"
    assert shop.get("abcdefab").error == engine.RESTART_ERROR
    write_run(shop, "abcdefab", done=True, finished_ago=1, ok=True)    # the generation that really ran it finishes it
    shop.load(close_unfinished=True)
    run = shop.get("abcdefab")
    assert run.done and run.ok and run.error is None


def test_runs_and_health_cover_every_generation(tmp_path):
    w, f = make(tmp_path)
    start(f, "task-old1")
    f.handover("test")
    runs = json.loads(f.route("GET", "/workshop/runs", "", b"", AUTH)[1])["runs"]
    assert [r["task"] for r in runs] == ["task-old1"]
    health = json.loads(f.route("GET", "/health", "", b"", {})[1])
    assert health["workshop"]["busy"] is True and len(health["front"]["generations"]) == 2


def test_new_code_on_disk_hands_over_by_itself_after_it_settles(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "AUTO", True)
    w, f = make(tmp_path)
    first = f.current()
    w.code = "c2"
    f.tick()
    assert f.current() is first                      # seen once: a pull may still be writing
    f.tick()
    assert f.current() is not first and f.current().code == "c2"


def test_a_dead_newest_generation_is_replaced(tmp_path):
    w, f = make(tmp_path)
    w.procs[f.current().port].dead = True
    f.tick()
    assert f.current() is not None and not w.procs[f.current().port].dead


def test_a_replacement_never_reuses_a_generation_number_even_when_none_was_left(tmp_path):
    w, f = make(tmp_path)
    first = f.current().n
    w.procs[f.current().port].dead = True         # the only generation dies (the chaos drill, 2026-11-01): the next one is NOT 1 again
    f.tick()
    assert f.current().n == first + 1
    again = fr.Front(spawn=w.spawn, request=w.request, state=tmp_path / "front.json", code=lambda: w.code, kill=w.kill)
    again.restore()
    assert again.last_n == first + 1                # a restarted front door keeps counting from there


def test_handover_needs_the_token(tmp_path):
    w, f = make(tmp_path)
    assert f.route("POST", "/front/handover", "", b"{}", {})[0] == 401
    assert f.route("POST", "/front/handover", "", b"{}", AUTH)[0] == 200


def test_a_restarted_front_door_takes_its_generations_back(tmp_path, monkeypatch):
    w, f = make(tmp_path)
    monkeypatch.setattr(fr, "pid_alive", lambda pid: True)
    g = fr.Front(spawn=w.spawn, request=w.request, state=tmp_path / "front.json", code=lambda: w.code, kill=w.kill)
    g.restore()
    assert [x.port for x in g.gens] == [f.current().port]


# ── the bridge's side of a hand-over ───────────────────────────────────────────────────────────────────────────────────────
def test_a_handover_bridge_leaves_the_older_ones_live_run_alone(shop):
    path = write_run(shop, "abcdefab", done=False)
    before = path.read_text(encoding="utf-8")
    assert shop.load(close_unfinished=False) == 0
    assert shop.get("abcdefab") is None and path.read_text(encoding="utf-8") == before


def test_a_run_finished_by_the_older_generation_is_taken_back_from_disk(shop):
    write_run(shop, "abcdefab", done=True, finished_ago=5, ok=True)
    run = shop.get("abcdefab")
    assert run is not None and run.done and run.ok


# ── the SkippyTel fix lane ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_every_conversation_may_fix_skippytel_in_its_clone():
    lane = lg.Lane("D:/Aaron/development/arkanoid", "D:/Aaron/development/SkippyTel", temps=[],
                   fix="D:/Aaron/development/SkippyTel-workshop")
    assert lane.check("Edit", {"file_path": "D:/Aaron/development/SkippyTel-workshop/portal/app.py"}) is None
    assert lane.check("Bash", {"command": "cd D:/Aaron/development/SkippyTel-workshop && git commit -am x && git push"}) is None
    assert lane.check("Bash", {"command": "git -C D:/Aaron/development/SkippyTel-workshop push"}) is None
    assert "outside" in lane.check("Edit", {"file_path": "D:/Aaron/development/SkippyTel/portal/app.py"})     # never his checkout
    assert "outside" in lane.check("Edit", {"file_path": "D:/Aaron/development/music-collection/a.py"})


def test_the_lane_env_names_the_fix_lane_except_in_skippytel_itself(shop):
    st = shop.project()
    other = engine.projects_mod.Project("f-arkanoid", "arkanoid", "", "main", "D:/Aaron/development/arkanoid",
                                        "D:/Aaron/development/arkanoid", "", "")
    assert shop.lane_env(other) == {"SKIPPY_LANE_DEPLOY": st.primary, "SKIPPY_LANE_FIX": st.clone}
    assert shop.lane_env(st) == {"SKIPPY_LANE_DEPLOY": st.primary, "SKIPPY_LANE_FIX": ""}
