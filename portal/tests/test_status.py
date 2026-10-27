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
    assert {s["name"]: s["lamp"] for s in core["serves"]} == {"chat": "ok", "coder": "degraded"}
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
