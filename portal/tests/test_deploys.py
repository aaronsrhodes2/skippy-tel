"""The house's own deploys (deploys.py): skippy-core reports its runs on POST /home/deploys, and the ship cards and Recovery read
them through recovery.GitHub's own method names; Recovery's requests go back to skippy-core in the reply."""
import pytest

import recovery
import ship as ship_mod
from deploys import HouseDeploys, clean_run

SHA_A, SHA_B = "a" * 40, "b" * 40


def run(i, sha, status="completed", conclusion="success", jobs=None, at="2026-10-25T20:00:00Z"):
    return {"id": i, "head_sha": sha, "status": status, "conclusion": conclusion, "created_at": at, "updated_at": at,
            "title": f"commit {i}", "jobs": jobs or [{"name": "test", "status": "completed", "conclusion": "success"},
                                                     {"name": "portal-image", "status": "completed", "conclusion": "success"},
                                                     {"name": "portal", "status": "completed", "conclusion": conclusion}]}


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_reports_answer_the_cards_and_odd_runs_are_dropped():
    clock = Clock()
    d = HouseDeploys(clock)
    assert not d.configured
    with pytest.raises(recovery.GitHubError, match="hasn't reported"):
        d.deploy_runs()
    d.report({"runs": [run(1, SHA_A, at="2026-10-25T19:00:00Z"), run(2, SHA_B, at="2026-10-25T20:00:00Z"),
                       {"id": 3, "head_sha": "not-a-sha"}, "junk"]})
    assert d.configured and [r["id"] for r in d.deploy_runs()] == [2, 1]          # newest first, junk gone
    assert [j["name"] for j in d.run_jobs(2)] == ["test", "portal-image", "portal"]
    assert [r["id"] for r in d.runs_for_sha(SHA_A)] == [1]
    clock.t += 3600
    assert not d.configured                                                       # skippy-core silent: said, not pretended


def test_recovery_asks_skippy_core_and_a_done_request_goes_away():
    d = HouseDeploys(Clock())
    d.dispatch_deploy()
    d.dispatch_deploy()                                                           # asked twice = once
    d.dispatch_rollback("skippy-portal-00042-abc")
    with pytest.raises(recovery.GitHubError):
        d.dispatch_rollback("rm -rf /")
    pending = d.report({"runs": []})["requests"]
    assert [(q["op"], q["arg"]) for q in pending] == [("deploy", ""), ("rollback", "skippy-portal-00042-abc")]
    assert d.report({"runs": [], "done": [pending[0]["id"]]})["requests"] == [pending[1]]


def test_the_ship_card_reads_a_house_deploy():
    d = HouseDeploys(Clock())
    d.report({"runs": [run(7, SHA_A, status="in_progress", conclusion="",
                           jobs=[{"name": "test", "status": "completed", "conclusion": "success"},
                                 {"name": "portal-image", "status": "in_progress", "conclusion": ""}])]})
    watch = ship_mod.ShipWatch(d, recovery.RunsCache(d))
    card = watch.status({"sha": SHA_A, "subject": "x", "project": "skippytel", "repo": recovery.REPO, "branch": "main",
                         "workflow": "deploy.yml"}, live=SHA_B)
    assert [s["state"] for s in card["stages"]] == ["done", "running", "waiting"] and not card["final"]


def test_a_run_from_skippy_core_is_checked_for_shape():
    assert clean_run({"id": 1, "head_sha": SHA_A, "created_at": "yesterday"}) is None
    assert clean_run({"id": 0, "head_sha": SHA_A, "created_at": "2026-10-25T20:00:00Z"}) is None
    got = clean_run(run(5, SHA_A))
    assert got["html_url"] == "" and got["short"] == SHA_A[:7]


def test_the_door_needs_the_home_token_and_a_house_portal_reads_it(monkeypatch):
    pytest.importorskip("flask")
    pytest.importorskip("authlib")
    import app as portal
    from test_app import SETTINGS, FakeSkippy, FakeVm, _pair
    monkeypatch.setenv("DEPLOYS_FROM", "house")
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    client = app.test_client()
    assert client.post("/home/deploys", json={"runs": []}).status_code == 401
    H = {"Authorization": f"Bearer {_pair(client)}"}
    assert client.post("/home/deploys", json={"runs": [run(9, SHA_A)]}, headers=H).get_json() == {"requests": []}
    github = app.extensions["recovery"]["github"]
    assert isinstance(github, HouseDeploys) and [r["id"] for r in github.deploy_runs()] == [9]
