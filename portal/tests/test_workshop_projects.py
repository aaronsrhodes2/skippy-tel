"""Projects and resumable conversations in the private Skippy, on the portal's side: only a project skippy-pc lists can
be worked on, every run carries its project, and the Conversations list and a conversation's turns come back from home
checked, cut, and only for their owner."""
import json
import re

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import home as home_mod  # noqa: E402
import workshop as workshop_mod  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402
from test_workshop import CONV, TEXT, TOOL, Clock  # noqa: E402

import app as portal  # noqa: E402

PROJECTS = [{"id": "skippytel", "name": "SkippyTel", "repo": "aaronsrhodes2/skippy-tel", "branch": "main"},
            {"id": "sigma-ground", "name": "sigma-ground", "repo": "aaronsrhodes2/sigma-ground", "branch": "master"}]
H = {"X-Workshop": "1"}


def caps(projects=PROJECTS):
    return {"capabilities": {"workshop": {"ready": True, "model": "claude-sonnet-5-5", "projects": projects}}}


@pytest.fixture
def shop():
    clock = Clock()
    home = home_mod.Home(FakeVm(), clock=clock)
    home.accept(caps())
    return workshop_mod.Workshop(home, clock=clock), home, clock


@pytest.fixture
def env():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    app.extensions["home"].accept(caps())
    return app.test_client(), app, app.extensions["home"]


# ── the project list ───────────────────────────────────────────────────────
def test_the_projects_skippy_pc_lists_are_checked_again_here():
    home = home_mod.Home(FakeVm())
    home.accept(caps(PROJECTS + [{"id": "Bad Id"}, {"id": "skippytel", "name": "dupe"}, "junk",
                                 {"id": "x", "name": "<b>X</b>\n", "repo": "javascript:alert(1)", "branch": "a b"}]))
    got = home.workshop_projects()
    assert got[:2] == PROJECTS and len(got) == 3
    assert got[2] == {"id": "x", "name": "<b>X</b>", "repo": "", "branch": "main"}      # the template escapes the name


def test_a_silent_home_or_an_old_reporter_offers_skippytel_only(shop):
    ws, home, clock = shop
    clock.now += home_mod.STALE_AFTER + 1
    assert home.workshop_projects() == [] and [p["id"] for p in ws.projects()] == ["skippytel"]
    home.accept({"capabilities": {"workshop": {"ready": True}}})
    assert [p["id"] for p in ws.projects()] == ["skippytel"]


# ── sending for a project ──────────────────────────────────────────────────
def test_a_message_for_a_listed_project_carries_it_to_skippy_pc(shop):
    ws, home, _ = shop
    task = ws.send(ADMIN, CONV, "fix the build", "sigma-ground")
    [job] = home.jobs_reply()["jobs"]
    assert job["project"] == "sigma-ground" and job["id"] == task
    assert ws.read(ADMIN, task)["project"] == "sigma-ground"


@pytest.mark.parametrize("bad", ["zphone", "../SkippyTel", "SkippyTel", "aaronsrhodes2/skippy-tel"])
def test_a_project_skippy_pc_doesnt_list_is_refused_and_nothing_is_queued(shop, bad):
    ws, home, _ = shop
    with pytest.raises(ValueError, match="doesn't offer"):
        ws.send(ADMIN, CONV, "hi", bad)
    assert home.jobs_reply()["jobs"] == []


def test_no_project_means_skippytel(shop):
    ws, home, _ = shop
    ws.send(ADMIN, CONV, "hi")
    assert home.jobs_reply()["jobs"][0]["project"] == "skippytel"


def test_a_rebuilt_record_learns_its_project_from_the_push(shop):
    ws, _, _ = shop
    ws.push("abcd1234", ADMIN, CONV, 0, [TEXT], "running", None, "sigma-ground")
    assert ws.read(ADMIN, "abcd1234")["project"] == "sigma-ground"
    ws.push("abcd5678", ADMIN, CONV, 0, [TEXT], "running", None, "../../evil")
    assert ws.read(ADMIN, "abcd5678")["project"] == "skippytel"


def test_the_send_route_takes_the_project_and_refuses_an_unknown_one(env):
    client, _, home = env
    login(client, ADMIN)
    resp = client.post("/me/workshop/send", json={"conv": CONV, "message": "hi", "project": "nope"}, headers=H)
    assert resp.status_code == 400 and "doesn't offer" in resp.get_json()["error"]
    resp = client.post("/me/workshop/send", json={"conv": CONV, "message": "hi", "project": "sigma-ground"}, headers=H)
    assert resp.status_code == 200 and home.jobs_reply()["jobs"][0]["project"] == "sigma-ground"


def test_the_card_offers_the_projects_and_escapes_their_names(env):
    client, _, home = env
    home.accept(caps(PROJECTS + [{"id": "evil", "name": "</script><script>alert(1)</script>"}]))
    login(client, ADMIN)
    page = client.get("/chat").data.decode()
    assert '<select id="ps-project" aria-label="Project" hidden>' in page          # offered to the script; the model picks the folder, so it is not shown
    assert '<option value="sigma-ground">sigma-ground</option>' in page
    assert "<script>alert(1)" not in page                                  # escaped in the option AND in the script's JSON
    assert re.search(r"const PROJECTS = \[.*\\u003c/script\\u003e", page)
    assert 'id="ps-convs-btn">Conversations</button>' in page
    # The row's own class: the dashboard already styles `.bar` (8 px high, overflow hidden), which squashed the row
    # to a sliver on a phone (found in the Browser pane at 375 px).
    assert '<div class="ps-bar">' in page and ".me-chat .bar" not in page and 'class="bar"' not in page.split("private-skippy")[1].split("</script>")[0]


# ── conversations ──────────────────────────────────────────────────────────
def finish(home, job, result):
    assert home.finish(job, {"result": result})


SUMMARY = {"conv": CONV, "project": "sigma-ground", "who": ADMIN, "title": "make it green", "started": 1.0e9,
           "last": 1.0e9 + 60, "turns": 2, "state": "done"}


def test_the_list_is_a_job_for_home_and_comes_back_checked_and_only_the_owners(shop):
    ws, home, _ = shop
    job = ws.ask_history(ADMIN)
    assert home.jobs_reply()["jobs"] == [{"id": job, "kind": "workshop_conversations"}]
    assert ws.history_result(ADMIN, job) == {"state": "running", "error": None}
    finish(home, job, {"conversations": [
        SUMMARY,
        {**SUMMARY, "conv": "conv-otherone", "who": FRIEND},                            # someone else's: never shown
        {**SUMMARY, "conv": "../../etc"}, "junk",                                       # not conversations
        {**SUMMARY, "conv": "conv-weird000", "project": "Bad Id", "title": "<img src=x>\n" + "x" * 200, "turns": -3,
         "state": "exploded", "last": "yesterday"}]})
    got = ws.history_result(ADMIN, job)
    assert got["state"] == "done"
    assert got["conversations"][0] == {k: v for k, v in SUMMARY.items() if k != "who"}
    weird = got["conversations"][1]
    assert weird["project"] == "skippytel" and len(weird["title"]) == 90 and weird["turns"] == 0
    assert weird["state"] == "done" and weird["last"] == 1.0e9                         # bad `last`: falls back to started
    assert len(got["conversations"]) == 2
    assert ws.history_result(FRIEND, job) == {"state": "unknown"}                       # not the asker's job


def test_one_conversation_comes_back_with_its_turns_cleaned(shop):
    ws, home, _ = shop
    job = ws.ask_history(ADMIN, CONV)
    assert home.jobs_reply()["jobs"] == [{"id": job, "kind": "workshop_conversation", "conv": CONV}]
    finish(home, job, {**SUMMARY, "trimmed": 3, "turns": [
        {"task": "abcd1234", "at": 1.0e9, "message": "make it\ngreen", "state": "done", "error": None,
         "events": [TEXT, TOOL, {"t": "script", "src": "x"}, {"t": "text", "text": "y" * 9000}]},
        {"task": "not-a-task!", "message": 5, "state": "??", "events": "junk", "error": 7}]})
    got = ws.history_result(ADMIN, job)["conversation"]
    assert got["conv"] == CONV and got["project"] == "sigma-ground" and got["trimmed"] == 3 and "who" not in got
    first, second = got["turns"]
    assert first["message"] == "make it\ngreen" and first["task"] == "abcd1234"
    assert first["events"][:2] == [TEXT, TOOL] and len(first["events"]) == 3 and len(first["events"][2]["text"]) == 4000
    assert second == {"task": None, "message": "", "events": [], "at": None, "state": "done", "error": None}


def test_someone_elses_conversation_is_not_shown(shop):
    ws, home, _ = shop
    job = ws.ask_history(ADMIN, CONV)
    finish(home, job, {**SUMMARY, "who": FRIEND, "turns": []})
    assert ws.history_result(ADMIN, job)["state"] == "error"


def test_a_failed_job_says_why_and_a_bad_id_is_refused(shop):
    ws, home, _ = shop
    job = ws.ask_history(ADMIN)
    home.finish(job, {"error": "The bridge on skippy-pc isn't answering."})
    assert ws.history_result(ADMIN, job) == {"state": "error", "error": "The bridge on skippy-pc isn't answering."}
    with pytest.raises(ValueError):
        ws.ask_history(ADMIN, "../x")
    assert ws.history_result(ADMIN, "zzz") == {"state": "unknown"}


def test_the_history_routes_are_admin_only_need_the_header_and_skippy_pc(env):
    client, app, home = env
    assert client.post("/me/workshop/history", json={}, headers=H).status_code == 302          # signed out
    login(client, FRIEND)
    assert client.post("/me/workshop/history", json={}, headers=H).status_code == 403
    assert client.get("/me/workshop/history/result?job=abcd1234", headers=H).status_code == 403
    login(client, ADMIN)
    assert client.post("/me/workshop/history", json={}).status_code == 400                      # no X-Workshop header
    assert client.post("/me/workshop/history", json={"conv": "../x"}, headers=H).status_code == 400
    resp = client.post("/me/workshop/history", json={}, headers=H)
    job = resp.get_json()["job"]
    assert resp.headers["Cache-Control"] == "no-store"
    finish(home, job, {"conversations": [SUMMARY]})
    got = client.get(f"/me/workshop/history/result?job={job}", headers=H).get_json()
    assert got["state"] == "done" and got["conversations"][0]["title"] == "make it green"
    home.report = None                                                                          # home went silent
    assert client.post("/me/workshop/history", json={}, headers=H).status_code == 503
