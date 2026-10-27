"""core/shipper.py: a container that died or keeps restarting becomes a crash record with its last lines, error-looking lines are picked
out a few per container, and nothing is lost when the portal is away (a spool written before sending, removed only once accepted)."""
import json

from core_testlib import load

ship = load("shipper.py", "core_shipper")


class Docker:
    def __init__(self, tails=None, since=None):
        self.tails, self.since, self.calls = tails or {}, since or {}, []

    def __call__(self, cmd, timeout=20):
        self.calls.append(cmd)
        name = cmd[-1]
        if "--tail" in cmd:
            return self.tails.get(name, "")
        return self.since.get(name, "")


def shipper(tmp_path, docker, accepted=None, clock=lambda: 1000.0):
    sent = accepted if accepted is not None else []
    state = {"up": True}

    def post(rows):
        if state["up"]:
            sent.extend(rows)
        return state["up"]
    s = ship.Shipper("skippy-core", tmp_path, docker, post, clock=clock)
    return s, sent, state


def test_the_first_round_only_learns_and_a_death_afterwards_is_a_crash_with_its_last_lines(tmp_path):
    docker = Docker(tails={"llm": "loading\nCUDA out of memory\nkilled"})
    s, sent, _ = shipper(tmp_path, docker)
    assert s.collect([{"name": "llm", "state": "running", "status": "Up 3 hours"}]) == []            # nothing to compare with yet
    rows = s.collect([{"name": "llm", "state": "exited", "status": "Exited (137) 2 seconds ago"}])
    crash = rows[0]
    assert crash["kind"] == "crash" and crash["level"] == "error" and crash["source"] == "docker/llm"
    assert "exit 137" in crash["msg"] and crash["fields"]["exit_code"] == 137 and "killed" in crash["msg"]
    assert [r["msg"] for r in rows[1:]] == ["loading", "CUDA out of memory"]                           # its last lines ride along


def test_a_restart_counter_that_went_up_is_a_crash_even_though_it_is_running_again(tmp_path):
    s, _, _ = shipper(tmp_path, Docker(tails={"mentat": "Traceback (most recent call last):"}))
    s.collect([{"name": "mentat", "state": "running", "restarts": 2}])
    rows = s.collect([{"name": "mentat", "state": "running", "restarts": 3}])
    assert rows and rows[0]["kind"] == "crash" and rows[0]["fields"]["restarts"] == 3
    assert s.collect([{"name": "mentat", "state": "running", "restarts": 3}]) == []                    # no new news, no new record


def test_error_looking_lines_of_a_running_container_are_picked_a_few_at_a_time(tmp_path):
    noisy = "\n".join(["fine"] + [f"ERROR thing {i}" for i in range(12)] + ["all good"])
    s, _, _ = shipper(tmp_path, Docker(since={"voice": noisy}))
    rows = s.collect([{"name": "voice", "state": "running"}])
    assert len(rows) == ship.MAX_PER_CONTAINER and all(r["kind"] == "error" for r in rows)
    assert rows[-1]["msg"] == "ERROR thing 11"                                                         # the newest ones


def test_nothing_is_lost_while_the_portal_is_away_and_it_all_goes_when_it_returns(tmp_path):
    s, sent, state = shipper(tmp_path, Docker())
    state["up"] = False
    assert s.flush([{"source": "docker/a", "msg": "first words of a crash", "level": "error"}]) == 0
    assert (tmp_path / "spool.jsonl").exists()                                                          # on disk before it was ever sent
    s2, sent2, state2 = shipper(tmp_path, Docker(), accepted=sent)                                      # the shipper itself restarted
    assert s2.flush([{"source": "docker/b", "msg": "second", "level": "error"}]) == 2
    assert [r["msg"] for r in sent] == ["first words of a crash", "second"] and not (tmp_path / "spool.jsonl").exists()


def test_a_long_outage_keeps_the_newest_and_never_grows_without_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(ship, "MAX_SPOOL_BYTES", 2000)
    s, sent, state = shipper(tmp_path, Docker())
    state["up"] = False
    for i in range(100):
        s.flush([{"source": "docker/a", "msg": f"line {i} " + "x" * 60, "level": "info"}])
    assert (tmp_path / "spool.jsonl").stat().st_size <= 2200
    state["up"] = True
    s.flush([])
    assert json.loads(json.dumps(sent))[-1]["msg"].startswith("line 99")
