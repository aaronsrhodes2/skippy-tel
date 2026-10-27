"""The Status map (portal/status.py, status_web.py): what each machine reports becomes one tree of lamps and connections; nothing a
machine sends can break the page; the core report door wants the home token; the admin-only page, feed and keys."""
import pytest

pytest.importorskip("flask")

import status as status_mod  # noqa: E402


def tree(st, **kw):
    args = dict(home_online=True, home_age=5, home_report={}, starter_view={"status": {}}, portal={"short": "abc1234", "revision": "r1"},
                games_vm={"status": "TERMINATED"}, core_verdict=None)
    args.update(kw)
    return st.tree(**args)


def by(t, mid):
    return next(m for m in t["machines"] if m["id"] == mid)


def test_a_fresh_core_report_is_ok_and_its_models_and_containers_get_lamps():
    now = [1000.0]
    st = status_mod.Status(clock=lambda: now[0])
    st.accept_core({"containers": [{"name": "skippycore-mentat-1", "state": "running", "health": "", "restarts": 0},
                                   {"name": "skippycore-llm-1", "state": "restarting", "restarts": 7}],
                    "models": [{"name": "chat", "state": "ready"}, {"name": "coder", "state": "starting"}],
                    "deployer": {"state": "ok", "commit": "abc"}, "doctor": {"ok": True}, "vitals": {"mem_pct": 40}})
    core = by(tree(st), "skippy-core")
    assert core["state"] == "degraded"                                    # a restarting container
    lamps = {c["name"]: c["lamp"] for c in core["containers"]}
    assert lamps == {"skippycore-mentat-1": "ok", "skippycore-llm-1": "down"}
    assert {s["name"]: s["lamp"] for s in core["serves"]} == {"chat": "ok", "coder": "degraded", "router": "off", "vision": "off"}   # a slot not loaded is still listed
    assert any("skippycore-llm-1" in p["what"] for p in tree(st)["problems"])


def test_a_report_that_stopped_is_stale_never_green():
    now = [1000.0]
    st = status_mod.Status(clock=lambda: now[0])
    st.accept_core({"containers": [], "models": []})
    now[0] += status_mod.STALE_S + 1
    t = tree(st)
    assert by(t, "skippy-core")["state"] == "stale"
    edge = next(e for e in t["edges"] if e["to"] == "skippy-core" and e["from"] == "skippy-portal")
    assert edge["lamp"] == "stale"


def test_no_report_ever_is_off_and_pc_follows_the_home_report():
    st = status_mod.Status()
    t = tree(st, home_online=False, home_age=None)
    assert by(t, "skippy-core")["state"] == "off" and by(t, "skippy-pc")["state"] == "off"
    assert by(tree(st), "skippy-pc")["state"] == "ok"


def test_junk_from_a_machine_is_bounded_and_cleaned():
    got = status_mod.clean_containers([{"name": "x" * 500, "state": 5, "cpu": "NaN", "restarts": -4}, "junk", {"nope": 1}] + [{"name": f"c{i}"} for i in range(500)])
    assert len(got) <= status_mod.MAX_ITEMS and len(got[0]["name"]) <= 60 and got[0]["cpu"] is None and got[0]["restarts"] == 0
    assert status_mod.clean_models("nope") == [] and status_mod.clean_containers(None) == []
    st = status_mod.Status()
    st.accept_core({"deployer": "x", "vitals": [1], "aliases": "no"})              # wrong shapes: accepted as empty, never an error
    assert st.core["containers"] == [] and st.core["vitals"] == {}


def test_planned_services_of_the_pc_are_off_not_down():
    t = tree(status_mod.Status(), home_report={"services": [{"name": "UPS Monitor", "available": False, "planned": True},
                                                            {"name": "Vault", "available": False}]})
    lamps = {s["name"]: s["lamp"] for s in by(t, "skippy-pc")["serves"]}
    assert lamps["UPS Monitor"] == "off" and lamps["Vault"] == "down"


# ── the routes ─────────────────────────────────────────────────────────────────
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

import app as portal  # noqa: E402


@pytest.fixture
def world():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    return app, app.test_client()


def test_the_core_report_door_wants_the_home_token(world):
    app, client = world
    assert client.post("/home/core-report", json={}).status_code == 401
    t = app.extensions["home"].pair()
    h = {"Authorization": f"Bearer {t}"}
    assert client.post("/home/core-report", data="nope", headers=h).status_code == 400
    assert client.post("/home/core-report", data="x" * 300_000, headers={**h, "Content-Type": "application/json"}).status_code == 413
    got = client.post("/home/core-report", json={"containers": [{"name": "a", "state": "running"}], "models": [{"name": "chat", "state": "ready"}]}, headers=h)
    assert got.status_code == 200
    login(client, ADMIN)
    state = client.get("/status/state").get_json()
    core = next(m for m in state["machines"] if m["id"] == "skippy-core")
    assert core["state"] == "ok" and core["containers"][0]["name"] == "a" and state["edges"]


def test_the_page_and_its_feed_are_the_captains_only(world):
    app, client = world
    assert client.get("/status/state").status_code in (302, 401, 403)
    login(client, FRIEND)
    assert client.get("/status").status_code in (302, 401, 403)
    assert client.get("/status/state").status_code in (302, 401, 403)
    client.get("/auth/logout")
    login(client, ADMIN)
    page = client.get("/status")
    assert page.status_code == 200 and b'id="s-back"' in page.data and b'id="s-close"' in page.data
    assert page.data.index(b'id="s-back"') < page.data.index(b'id="s-close"')          # Back and Close side by side
    assert client.get("/status/state").get_json()["machines"]


def test_heal_and_repair_need_the_portals_own_page_and_the_starter(world):
    app, client = world
    login(client, ADMIN)
    own = {"Origin": "http://localhost"}
    assert client.post("/status/heal", json={"name": "voice-gate"}).status_code == 403            # not from the page
    assert client.post("/status/heal", json={"name": "voice-gate"}, headers=own).status_code == 503      # no starter yet: says so
    starter = app.extensions["starter"]
    starter.poll({"status": {"docker": True}})                                                      # the starter is there
    assert client.post("/status/heal", json={"name": "rm -rf"}, headers=own).status_code == 400
    assert client.post("/status/heal", json={"name": "voice-gate"}, headers=own).status_code == 200
    assert starter.view()["waiting"][0]["op"] == "heal"


def test_the_user_access_key_sits_by_logout_for_the_captain_only(world):
    app, client = world
    login(client, FRIEND)
    assert b'aria-label="User access"' not in client.get("/apps").data
    client.get("/auth/logout")
    login(client, ADMIN)
    assert b'aria-label="User access"' in client.get("/apps").data


# ── probes: a test question to each model ─────────────────────────────────────
def test_a_probe_records_how_each_model_answered_one_at_a_time_and_never_while_the_box_is_busy():
    asked = []

    def test(model):
        asked.append(model)
        return (model != "coder", "" if model != "coder" else "no answer in time", 1200)
    p = status_mod.Probes(test, clock=lambda: 5000.0)
    ok, why = p.start(["chat", "coder", "evil; rm"], busy=True)
    assert ok is False and "private Skippy" in why and asked == []                      # the box is busy: nothing is loaded under the run
    ok, why = p.start(["nope"], busy=False)
    assert ok is False and "Nothing to ask" in why
    ok, why = p.start(["chat", "coder", "evil; rm"], busy=False, spawn=lambda names: p.run(names))
    assert ok is True and asked == ["chat", "coder"]                                    # only known slots are asked; the junk name is dropped
    got = p.view()
    assert got["running"] is None and got["results"]["chat"] == {"ok": True, "why": "", "ms": 1200, "at": 5000}
    assert got["results"]["coder"]["ok"] is False and got["results"]["coder"]["why"] == "no answer in time"


def test_a_probe_that_blows_up_is_a_failed_probe_and_two_never_run_at_once():
    def boom(model):
        raise RuntimeError("x")
    p = status_mod.Probes(boom)
    p.start(["chat"], busy=False, spawn=lambda names: p.run(names))
    assert p.view()["results"]["chat"]["ok"] is False and "RuntimeError" in p.view()["results"]["chat"]["why"]
    q = status_mod.Probes(lambda m: (True, "", 1))
    q.running = "chat"
    assert q.start(["coder"], busy=False)[0] is False                                    # one at a time


def test_the_probe_answer_shows_on_the_models_row_and_the_route_guards_it(world):
    app, client = world
    h = {"Authorization": f"Bearer {app.extensions['home'].pair()}"}
    client.post("/home/core-report", json={"models": [{"name": "chat", "state": "ready"}], "busy": False}, headers=h)
    app.extensions["probes"].test = lambda model: (True, "", 900)
    login(client, ADMIN)
    own = {"Origin": "http://localhost"}
    assert client.post("/status/probe", json={"names": ["chat"]}).status_code == 403              # not from the page
    spawned = []
    app.extensions["probes"]._thread = lambda names: spawned.append(names)
    assert client.post("/status/probe", json={"names": "all"}, headers=own).status_code == 200 and spawned == [["chat", "coder", "router", "vision"]]
    app.extensions["probes"].run(["chat"])
    core = next(m for m in client.get("/status/state").get_json()["machines"] if m["id"] == "skippy-core")
    chat = next(s for s in core["serves"] if s["name"] == "chat")
    assert chat["probe"]["ok"] is True and chat["probe"]["ms"] == 900
    app.extensions["status"].core["busy"] = True
    app.extensions["probes"].running = None
    assert client.post("/status/probe", json={"names": ["chat"]}, headers=own).status_code == 409


def test_ready_busy_and_hung_are_three_different_things_not_just_an_http_200():
    st = status_mod.Status(clock=lambda: 1000.0)
    st.accept_core({"models": [
        {"name": "chat", "state": "ready", "jobs": 0, "decoded": 0, "stuck_s": 0},
        {"name": "coder", "state": "ready", "jobs": 2, "decoded": 310, "stuck_s": 20},
        {"name": "router", "state": "ready", "jobs": 1, "decoded": 5, "stuck_s": 200},
        {"name": "vision", "state": "starting"}], "containers": [], "doctor": {"ok": True}})
    core = by(tree(st), "skippy-core")
    row = {s["name"]: s for s in core["serves"]}
    assert (row["chat"]["lamp"], row["chat"]["detail"]) == ("ok", "ready: accepting work, no jobs")
    assert row["coder"]["lamp"] == "ok" and row["coder"]["detail"].startswith("busy: 2 jobs running, 310 words")
    assert row["router"]["lamp"] == "hung" and row["router"]["detail"].startswith("HUNG: 1 job running, no new words for 3 min 20 s")
    assert row["vision"]["lamp"] == "degraded"
    assert core["state"] == "degraded"                                                    # a hung model makes the machine degraded
    assert any("router is hung" in p["what"] for p in tree(st)["problems"])


def test_skippy_pcs_services_show_what_they_said_about_themselves_not_just_that_they_answered():
    doctor = {"voice-gate": {"ok": True, "state": "ready"}, "music-gate": {"ok": True, "state": "busy", "jobs": 2},
              "video-gate": {"ok": False, "state": "hung", "jobs": 1, "stuck_s": 300, "why": "hung: 1 job running, no progress for 300 s"},
              "mentat-home": {"ok": False, "state": "not-ready", "why": "waiting on llm (skippy-core doesn't offer the chat model)"},
              "whisper": {"ok": True}, "flask-api": {"ok": False, "why": "HTTP 500"}}
    t = tree(status_mod.Status(), home_report={"doctor": doctor})
    row = {s["name"]: s for s in by(t, "skippy-pc")["serves"] if s["name"] in doctor}
    assert (row["voice-gate"]["lamp"], row["voice-gate"]["detail"]) == ("ok", "ready: accepting work, no jobs")
    assert row["music-gate"]["lamp"] == "ok" and row["music-gate"]["detail"] == "busy: 2 jobs running"
    assert row["video-gate"]["lamp"] == "hung" and row["video-gate"]["detail"] == "HUNG: 1 job running, no progress for 5 min 0 s"
    assert row["mentat-home"]["lamp"] == "down" and "chat model" in row["mentat-home"]["detail"]
    assert row["whisper"]["lamp"] == "off" and "cannot yet say whether it is ready" in row["whisper"]["detail"]      # answered, unverified: grey, not green
    assert row["flask-api"]["lamp"] == "down" and row["flask-api"]["detail"] == "HTTP 500"
    assert by(t, "skippy-pc")["state"] == "degraded" and any("video-gate is hung" in p["what"] for p in t["problems"])
