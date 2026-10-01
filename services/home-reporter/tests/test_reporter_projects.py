"""Projects and conversations on the reporter's side: the project a chat message is for reaches the bridge (and back to
the portal on every push, so a redeployed portal knows it), the bridge's project list rides in the workshop capability,
and the Conversations list and a conversation's turns are fetched from the bridge as jobs."""
import json

import pytest

import reporter
from test_music import Fake, _json
from test_workshop_sync import CONV, OWNER, T1, TOKEN, BridgeStub, PortalStub, SpyJobs, ev  # noqa: F401

PROJECTS = [{"id": "skippytel", "name": "SkippyTel", "repo": "aaronsrhodes2/skippy-tel", "branch": "main"},
            {"id": "sigma-ground", "name": "sigma-ground", "repo": "aaronsrhodes2/sigma-ground", "branch": "master"}]


@pytest.fixture
def fakes(monkeypatch):
    made = []

    def make(handler, portal=None):
        if isinstance(handler, BridgeStub):           # it serves itself already
            made.append(handler.fake)
            fake = handler.fake
        else:
            fake = Fake(handler)
            made.append(fake)
        monkeypatch.setattr(reporter, "BRIDGE", fake.url)
        if portal is not None:
            made.append(portal.fake)
            monkeypatch.setattr(reporter, "PORTAL", portal.fake.url)
        monkeypatch.setattr(reporter, "WORKSHOP_WAIT", 0)
        monkeypatch.setattr(reporter, "START_TRIES", 1)
        monkeypatch.setattr(reporter, "WORKSHOP_MAX_S", 3)
        monkeypatch.setattr(reporter.time, "sleep", lambda s: None)
        return fake
    yield make
    for f in made:
        f.close()


# ── the project list ───────────────────────────────────────────────────────
def test_the_capability_carries_the_bridges_projects_checked(fakes):
    listed = PROJECTS + [{"id": "../evil", "name": "x"}, "junk",
                         {"id": "tomb", "name": "Tomb\nof Horrors", "repo": "not a repo", "clone": "D:/secret/path"}]
    fakes(lambda m, p, h, b: _json({"status": "ok", "workshop": {"ready": True, "model": "claude-opus-5-5",
                                                                  "projects": listed}}))
    got = reporter.workshop_capability()
    assert got["projects"][:2] == PROJECTS
    assert got["projects"][2] == {"id": "tomb", "name": "Tomb of Horrors", "repo": "", "branch": "main"}   # no path, ever
    assert len(got["projects"]) == 3 and "D:/secret" not in json.dumps(got)


def test_an_old_bridge_without_projects_adds_nothing(fakes):
    fakes(lambda m, p, h, b: _json({"status": "ok", "workshop": {"ready": True, "model": "claude-opus-5-5"}}))
    assert "projects" not in reporter.workshop_capability()


def test_the_project_list_is_capped():
    many = [{"id": f"p{n}", "name": f"P{n}"} for n in range(100)]
    assert len(reporter.workshop_projects(many)) == reporter.MAX_PROJECTS


# ── the project of a run ───────────────────────────────────────────────────
def test_a_message_for_another_project_reaches_the_bridge_with_it_and_every_push_says_it(fakes):
    bridge = BridgeStub(autofinish=True)
    portal = PortalStub()
    fakes(bridge, portal)
    out = reporter.run_job({"id": T1, "kind": "workshop", "conv": CONV, "message": "fix it", "who": OWNER,
                            "project": "sigma-ground"}, TOKEN)
    assert "result" in out
    assert bridge.started[0]["project"] == "sigma-ground"
    assert portal.pushes and all(p["project"] == "sigma-ground" for p in portal.pushes)


@pytest.mark.parametrize("bad", ["../x", "Sigma Ground", "a" * 60, "-x", ["skippytel"]])
def test_a_project_id_that_isnt_one_is_sent_as_the_default(fakes, bad):
    bridge = BridgeStub(autofinish=True)
    fakes(bridge, PortalStub())
    reporter.run_job({"id": T1, "kind": "workshop", "conv": CONV, "message": "x", "who": OWNER, "project": bad}, TOKEN)
    assert bridge.started[0]["project"] == ""


def test_the_reconciler_carries_each_runs_project(fakes):
    bridge = BridgeStub({T1: BridgeStub.run(events=[ev(0)])})
    bridge.fake.close()                                                # served by the wrapper below instead
    real = bridge.handle

    def with_project(method, path, headers, body):                    # the bridge's listing names the project
        status, hdrs, payload = real(method, path, headers, body)
        if path.startswith("/workshop/runs"):
            data = json.loads(payload)
            for r in data["runs"]:
                r["project"] = "sigma-ground"
            payload = json.dumps(data).encode()
        return status, hdrs, payload
    portal = PortalStub()
    fakes(with_project, portal)
    jobs = SpyJobs()
    assert reporter.WorkshopSync(TOKEN, jobs).sync_once()["adopted"] == [T1]
    assert portal.pushes[0]["project"] == "sigma-ground"
    assert jobs.accepted[0]["jobs"][0]["project"] == "sigma-ground"


def test_a_ship_event_is_carried_to_the_portal_as_it_is(fakes):
    ship = {"t": "ship", "project": "skippytel", "repo": "aaronsrhodes2/skippy-tel", "branch": "main", "sha": "a" * 40,
            "subject": "Make it green", "workflow": "deploy.yml"}
    bridge = BridgeStub({T1: BridgeStub.run(events=[ev(0), ship], done=True, finished_age_s=1)})
    portal = PortalStub()
    fakes(bridge, portal)
    reporter.WorkshopSync(TOKEN, SpyJobs()).sync_once()
    assert portal.held[T1] == [ev(0), ship]


# ── conversations ──────────────────────────────────────────────────────────
def test_the_conversations_list_and_one_conversation_come_from_the_bridge_with_the_token(fakes):
    seen = []

    def bridge(method, path, headers, body):
        seen.append((method, path, headers.get("Authorization")))
        if path == "/workshop/conversations":
            return _json({"conversations": [{"conv": CONV, "title": "hi"}]})
        if path == f"/workshop/conversation/{CONV}":
            return _json({"conv": CONV, "turns": [{"message": "hi"}]})
        return _json({"error": "unknown_conversation"}, 404)
    fakes(bridge)
    assert reporter.run_job({"id": "j1", "kind": "workshop_conversations"}, TOKEN) == {
        "result": {"conversations": [{"conv": CONV, "title": "hi"}]}}
    assert reporter.run_job({"id": "j2", "kind": "workshop_conversation", "conv": CONV}, TOKEN) == {
        "result": {"conv": CONV, "turns": [{"message": "hi"}]}}
    assert reporter.run_job({"id": "j3", "kind": "workshop_conversation", "conv": "conv-00000000"}, TOKEN) == {
        "error": "That conversation isn't on skippy-pc."}
    assert all(auth == f"Bearer {TOKEN}" and method == "GET" for method, _, auth in seen)


@pytest.mark.parametrize("conv", ["../../etc", "short", "", None, "a/b-12345678"])
def test_a_bad_conversation_id_never_reaches_the_bridge(fakes, conv):
    seen = []
    fakes(lambda m, p, h, b: seen.append(p) or _json({}))
    assert reporter.run_job({"id": "j", "kind": "workshop_conversation", "conv": conv}, TOKEN) == {"error": "bad conversation id"}
    assert seen == []


def test_a_bridge_that_is_down_or_unset_is_said_plainly(monkeypatch):
    monkeypatch.setattr(reporter, "BRIDGE", "")
    assert "isn't set up" in reporter.run_job({"id": "j", "kind": "workshop_conversations"}, TOKEN)["error"]
    monkeypatch.setattr(reporter, "BRIDGE", "http://127.0.0.1:9")               # nothing listens there
    assert reporter.run_job({"id": "j", "kind": "workshop_conversations"}, TOKEN) == {
        "error": "The bridge on skippy-pc isn't answering."}
