"""Proof it's live, the portal's half: the `ship` event's strict shape, the one new (read-only) GitHub call, the ship card's
every state against a fake GitHub transport, and the route that only answers for commits the asker's own runs pushed."""
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import recovery  # noqa: E402
import ship as ship_mod  # noqa: E402
import workshop as workshop_mod  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402
from test_workshop import CONV, TEXT  # noqa: E402

import app as portal  # noqa: E402

REPO = recovery.REPO
TOKEN = "github_pat_TESTTOKEN0123456789abcdefSECRET"
SHA, LATER, OLDER = "a" * 40, "b" * 40, "c" * 40
NOW = datetime.now(timezone.utc).replace(microsecond=0)
SHIP = {"t": "ship", "project": "skippytel", "repo": REPO, "branch": "main", "sha": SHA, "subject": "Make the tile green",
        "workflow": "deploy.yml"}
H = {"X-Workshop": "1"}


def run(rid, sha, status="completed", conclusion="success", minutes_ago=5):
    when = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": rid, "head_sha": sha, "status": status, "conclusion": conclusion if status == "completed" else None,
            "created_at": when, "updated_at": when, "html_url": f"https://github.com/{REPO}/actions/runs/{rid}",
            "display_title": "x", "event": "push"}


def job(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion if status == "completed" else None}


class GitHubFake:
    """The network as recovery.GitHub sees it: runs per commit, jobs per run, the main-branch list. Records each call."""

    def __init__(self, by_sha=None, jobs=None, main=None, status=200):
        self.by_sha, self.jobs, self.main, self.status, self.calls = by_sha or {}, jobs or {}, main or [], status, []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        if self.status != 200:
            return self.status, b""
        if "head_sha=" in url:
            sha = url.split("head_sha=")[1].split("&")[0]
            return 200, json.dumps({"workflow_runs": self.by_sha.get(sha, [])}).encode()
        if url.endswith("/jobs?per_page=50"):
            rid = int(url.split("/runs/")[1].split("/")[0])
            return 200, json.dumps({"jobs": self.jobs.get(rid, [])}).encode()
        return 200, json.dumps({"workflow_runs": self.main}).encode()


def watch(fake=None, token=TOKEN):
    gh = recovery.GitHub(token, transport=fake or GitHubFake())
    clock = [0.0]
    return ship_mod.ShipWatch(gh, recovery.RunsCache(gh, clock=lambda: clock[0]), clock=lambda: clock[0]), clock


ALL = [job("test / tests (all but flask-api)"), job("test / flask-api"), job("portal-image"), job("portal")]


# ── the event ──────────────────────────────────────────────────────────────
def test_a_ship_event_in_its_exact_shape_passes():
    assert workshop_mod._clean(SHIP) == SHIP
    assert workshop_mod._clean({**SHIP, "subject": "two\nlines " + "x" * 300})["subject"] == "two lines " + "x" * 190


@pytest.mark.parametrize("bad", [
    {"sha": "A" * 40}, {"sha": "a" * 39}, {"sha": "a" * 40 + "\n"}, {"repo": "javascript:alert(1)"}, {"repo": "a/b/c"},
    {"project": "../x"}, {"workflow": "evil.yml"}, {"branch": "main main"}, {"subject": None}, {"extra": "field"},
])
def test_a_ship_event_out_of_shape_is_dropped(bad):
    assert workshop_mod._clean({**SHIP, **bad}) is None


def test_a_pushed_ship_is_remembered_for_its_owner_only():
    ws = workshop_mod.Workshop(portal.home_mod.Home(FakeVm()))
    ws.push("abcd1234", ADMIN, CONV, 0, [TEXT, SHIP], "done", None)
    assert ws.read(ADMIN, "abcd1234")["events"] == [TEXT, SHIP]
    assert ws.ship(ADMIN, SHA) == SHIP and ws.ship(FRIEND, SHA) is None and ws.ship(ADMIN, LATER) is None


# ── the one new GitHub call ────────────────────────────────────────────────
def test_runs_for_a_commit_is_one_read_only_call_with_a_40_hex_sha():
    fake = GitHubFake({SHA: [run(7, SHA), run(6, OLDER)]})
    gh = recovery.GitHub(TOKEN, transport=fake)
    assert [r["id"] for r in gh.runs_for_sha(SHA)] == [7]                  # a run for another commit is dropped
    assert fake.calls == [("GET", f"https://api.github.com/repos/{REPO}/actions/workflows/deploy.yml/runs?head_sha={SHA}&per_page=5")]
    for bad in ("a" * 39, "A" * 40, SHA + "&branch=x", "../" + SHA[:37], ""):
        with pytest.raises(recovery.GitHubError):
            gh.runs_for_sha(bad)
    with pytest.raises(recovery.GitHubError, match="isn't on its list"):
        gh._call("GET", f"/repos/{REPO}/actions/workflows/deploy.yml/runs?head_sha={SHA}&per_page=50")
    assert len(fake.calls) == 1


# ── the card's states ──────────────────────────────────────────────────────
def test_live_when_the_portal_serves_that_commit():
    w, _ = watch()
    card = w.status(SHIP, SHA)
    assert card["final"] and card["tone"] == "ok" and card["headline"] == f"Live: the portal serves {SHA[:7]}."
    assert card["links"] == {"commit": f"https://github.com/{REPO}/commit/{SHA}"}


def test_waiting_for_github_then_deploying_stage_by_stage():
    fake = GitHubFake()
    w, clock = watch(fake)
    card = w.status(SHIP, OLDER)
    assert not card["final"] and "Waiting for GitHub" in card["headline"]
    assert [s["state"] for s in card["stages"]] == ["waiting"] * 3
    fake.by_sha[SHA] = [run(7, SHA, status="in_progress")]
    fake.jobs[7] = [job("test / tests (all but flask-api)", "in_progress"), job("portal-image")]
    clock[0] += ship_mod.CACHE_S                                            # the cache is over
    card = w.status(SHIP, OLDER)
    assert not card["final"] and card["tone"] == "wait" and card["headline"] == f"Deploying {SHA[:7]}: tests…"
    assert card["stages"] == [{"name": "tests", "state": "running"}, {"name": "build", "state": "done"},
                              {"name": "deploy", "state": "waiting"}]
    assert card["links"]["run"] == f"https://github.com/{REPO}/actions/runs/7"


def test_a_green_run_is_not_live_until_the_portal_serves_it():
    w, _ = watch(GitHubFake({SHA: [run(7, SHA)]}, {7: ALL}))
    card = w.status(SHIP, OLDER)
    assert not card["final"] and "Waiting for the new version" in card["headline"]
    assert all(s["state"] == "done" for s in card["stages"])
    assert w.status(SHIP, SHA)["final"]


def test_a_failed_deploy_is_final_red_with_its_run_and_the_rollback_link():
    w, _ = watch(GitHubFake({SHA: [run(7, SHA, conclusion="failure")]},
                            {7: [job("test / tests (all but flask-api)"), job("portal-image"), job("portal", conclusion="failure")]}))
    card = w.status(SHIP, OLDER)
    assert card["final"] and card["tone"] == "bad" and card["headline"] == f"The deploy of {SHA[:7]} failed at deploy."
    assert card["links"]["run"].endswith("/actions/runs/7") and card["links"]["rollback"] == "/admin#recovery"


def test_live_through_a_later_deploy_that_contains_it():
    main = [run(9, LATER, minutes_ago=1), run(7, SHA, conclusion="cancelled", minutes_ago=5)]
    w, _ = watch(GitHubFake({SHA: [run(7, SHA, conclusion="cancelled")]}, {7: []}, main))
    card = w.status(SHIP, LATER)
    assert card["final"] and card["tone"] == "ok" and "later deploy" in card["headline"]


def test_a_cancelled_deploy_with_a_later_push_waits_and_one_without_is_final():
    main = [run(9, LATER, status="in_progress", minutes_ago=1), run(7, SHA, conclusion="cancelled", minutes_ago=5)]
    w, _ = watch(GitHubFake({SHA: [run(7, SHA, conclusion="cancelled")]}, {7: []}, main))
    card = w.status(SHIP, OLDER)
    assert not card["final"] and "goes live with that one" in card["headline"]
    w, _ = watch(GitHubFake({SHA: [run(7, SHA, conclusion="cancelled")]}, {7: []}, [run(7, SHA, conclusion="cancelled")]))
    card = w.status(SHIP, OLDER)
    assert card["final"] and card["tone"] == "bad"


def test_github_trouble_is_said_and_the_card_keeps_waiting():
    w, _ = watch(GitHubFake(status=401))
    card = w.status(SHIP, OLDER)
    assert not card["final"] and "refused the portal's token" in card["note"] and TOKEN not in json.dumps(card)
    w, _ = watch(token="")
    card = w.status(SHIP, OLDER)
    assert not card["final"] and "GITHUB_ACTIONS_TOKEN" in card["note"]
    assert w.status(SHIP, SHA)["final"]                                     # /version still proves it without a token


def test_github_is_asked_at_most_once_per_commit_per_cache_window():
    fake = GitHubFake({SHA: [run(7, SHA, status="in_progress")]}, {7: []})
    w, clock = watch(fake)
    for _ in range(5):
        w.status(SHIP, OLDER)
    # the commit's runs and that run's jobs once, and main's run list once (shared with the Admin page: 30 s)
    assert len(fake.calls) == 3
    clock[0] += ship_mod.CACHE_S
    w.status(SHIP, OLDER)
    assert len(fake.calls) == 5


def test_another_project_is_pushed_with_its_link_and_never_asks_github():
    fake = GitHubFake()
    w, _ = watch(fake)
    other = {**SHIP, "project": "sigma-ground", "repo": "aaronsrhodes2/sigma-ground", "branch": "master", "workflow": ""}
    card = w.status(other, OLDER)
    assert card["final"] and card["tone"] == "ok" and card["note"] == "Pushed (no deploy pipeline here)."
    assert card["links"] == {"commit": f"https://github.com/aaronsrhodes2/sigma-ground/commit/{SHA}"}
    card = w.status({**other, "workflow": "deploy.yml"}, OLDER)
    assert card["links"]["workflow"] == "https://github.com/aaronsrhodes2/sigma-ground/actions/workflows/deploy.yml"
    assert "can't read that repo's runs" in card["note"]
    card = w.status({**SHIP, "repo": "someone/skippy-tel"}, OLDER)            # "skippytel" in another repo: not ours to watch
    assert card["final"] and "run" not in card["links"]
    assert fake.calls == []


# ── the route ──────────────────────────────────────────────────────────────
@pytest.fixture
def env():
    fake = GitHubFake({SHA: [run(7, SHA, status="in_progress")]}, {7: []})
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(),
                            github=recovery.GitHub(TOKEN, transport=fake))
    app.extensions["home"].accept({"capabilities": {"workshop": {"ready": True}}})
    return app.test_client(), app, fake


def test_the_route_answers_only_for_the_askers_pushed_commits(env):
    client, app, fake = env
    app.extensions["workshop"].push("abcd1234", ADMIN, CONV, 0, [SHIP], "done", None)
    login(client, ADMIN)
    assert client.get(f"/me/workshop/ship?sha={SHA}").status_code == 400            # no X-Workshop header
    resp = client.get(f"/me/workshop/ship?sha={SHA}", headers=H)
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert resp.get_json()["headline"] == f"Deploying {SHA[:7]}…"
    assert client.get(f"/me/workshop/ship?sha={LATER}", headers=H).status_code == 404   # never pushed: GitHub isn't asked
    assert client.get("/me/workshop/ship?sha=../../x", headers=H).status_code == 404
    assert all(f"head_sha={LATER}" not in url for _, url in fake.calls)
    login(client, FRIEND)
    assert client.get(f"/me/workshop/ship?sha={SHA}", headers=H).status_code == 403
