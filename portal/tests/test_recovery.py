"""Recovery (recovery.py, recovery_web.py, doctor.py): the GitHub client's allow-list, the pure views, the private Skippy's cancel / clear /
pause, and the ONE operation, "Put everything back": its guards (admin only, own origin, rate limit, no confirm box), what it does on the
portal's side, and the checklist. The token must never appear in a page, a flash, a log line or an error."""
import html
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import recovery  # noqa: E402
import workshop as workshop_mod  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, _add_friend, login  # noqa: E402

import app as portal  # noqa: E402

TOKEN = "github_pat_TESTTOKEN0123456789abcdefSECRET"
REPO = recovery.REPO
NOW = datetime.now(timezone.utc).replace(microsecond=0)     # real time: the routes use the real clock for "stuck"
SHA = [f"{i:x}" * 40 for i in range(1, 8)]          # 1111..., 2222..., distinct 40-hex shas
ORIGIN = {"Origin": "http://localhost"}


def run(rid, sha, status="completed", conclusion="success", minutes_ago=10, title="a change"):
    when = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": rid, "head_sha": sha, "status": status, "conclusion": conclusion if status == "completed" else None,
            "created_at": when, "updated_at": when, "html_url": f"https://github.com/{REPO}/actions/runs/{rid}",
            "display_title": title, "event": "push"}


class Transport:
    """Stands in for the network: records every call, answers from `runs`, or with `status` for everything."""

    def __init__(self, runs=None, status=200):
        self.calls = []
        self.runs = runs if runs is not None else [run(9, SHA[0])]
        self.status = status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(body) if body else None, "timeout": timeout})
        if self.status != 200:
            return self.status, b'{"message": "Bad credentials ' + TOKEN.encode() + b'"}'
        if url.endswith("/jobs?per_page=50"):
            return 200, json.dumps({"jobs": [{"name": "portal", "status": "completed", "conclusion": "failure"}]}).encode()
        if method == "GET":
            return 200, json.dumps({"workflow_runs": self.runs}).encode()
        return (204 if url.endswith("/dispatches") else 202), b""


# ── the GitHub client: five endpoints and nothing else ────────────────────────────────────────────
def client_with(transport=None):
    t = transport or Transport()
    return recovery.GitHub(TOKEN, transport=t), t


def test_each_call_goes_to_its_one_endpoint_with_the_token_only_in_the_header():
    gh, t = client_with()
    gh.deploy_runs()
    gh.run_jobs(9)
    gh.dispatch_rollback("")
    gh.dispatch_rollback("latest")
    gh.dispatch_rollback("skippy-portal-00042-abc")
    gh.rerun_failed(9)
    gh.cancel("9")
    base = f"https://api.github.com/repos/{REPO}/actions"
    assert [(c["method"], c["url"]) for c in t.calls] == [
        ("GET", f"{base}/workflows/deploy.yml/runs?branch=main&per_page=20"),
        ("GET", f"{base}/runs/9/jobs?per_page=50"),
        ("POST", f"{base}/workflows/rollback.yml/dispatches"),
        ("POST", f"{base}/workflows/rollback.yml/dispatches"),
        ("POST", f"{base}/workflows/rollback.yml/dispatches"),
        ("POST", f"{base}/runs/9/rerun-failed-jobs"),
        ("POST", f"{base}/runs/9/cancel"),
    ]
    assert [c["body"] for c in t.calls[2:5]] == [{"ref": "main", "inputs": {"revision": v}}
                                                 for v in ("", "latest", "skippy-portal-00042-abc")]
    assert all(c["headers"]["Authorization"] == f"Bearer {TOKEN}" and c["timeout"] == 15 for c in t.calls)
    assert all(TOKEN not in c["url"] and TOKEN not in json.dumps(c["body"]) for c in t.calls)


def test_no_other_url_is_ever_called_even_from_inside_the_client():
    gh, t = client_with()
    for method, path in (("GET", f"/repos/{REPO}/contents/portal/app.py"), ("POST", f"/repos/{REPO}/actions/workflows/core.yml/dispatches"),
                         ("PUT", f"/repos/{REPO}/actions/workflows/deploy.yml/disable"), ("DELETE", f"/repos/{REPO}/actions/runs/9"),
                         ("GET", "/repos/someone/else/actions/workflows/deploy.yml/runs?branch=main&per_page=20"),
                         ("POST", f"/repos/{REPO}/actions/runs/9/cancel/../../secrets"),
                         ("GET", f"/repos/{REPO}/actions/workflows/deploy.yml/runs?branch=dev&per_page=20")):
        with pytest.raises(recovery.GitHubError, match="isn't on its list"):
            gh._call(method, path)
    assert t.calls == []


@pytest.mark.parametrize("bad", ["0", "-1", "9/../../x", "abc", "9 9", "1" * 21, "", None, "09"])
def test_a_run_id_must_be_a_plain_number(bad):
    gh, t = client_with()
    for call in (gh.cancel, gh.rerun_failed, gh.run_jobs):
        with pytest.raises(recovery.GitHubError):
            call(bad if bad is not None else "None")
    assert t.calls == []


@pytest.mark.parametrize("bad", ["previous", "skippy-portal-", "Skippy-Portal-1", "skippy-portal-1; rm -rf /", "--to-latest",
                                 "skippy-portal-00042-abc\nx", "x" * 80])
def test_a_rollback_input_is_blank_latest_or_a_revision_name(bad):
    gh, t = client_with()
    assert recovery.valid_revision(bad) is None
    with pytest.raises(recovery.GitHubError, match="isn't a revision name"):
        gh.dispatch_rollback(bad)
    assert t.calls == []
    assert recovery.valid_revision("  latest ") == "latest" and recovery.valid_revision(None) == ""


@pytest.mark.parametrize("status,words", [(401, "refused the portal's token"), (403, "may not do that"), (404, "couldn't find"),
                                          (409, "can't be changed now"), (422, "refused the request"), (502, "had a problem (HTTP 502)"),
                                          (301, "HTTP 301"), (418, "HTTP 418")])
def test_errors_are_plain_sentences_without_the_token_or_the_body(status, words):
    gh, _ = client_with(Transport(status=status))
    with pytest.raises(recovery.GitHubError) as exc:
        gh.cancel(9)
    assert words in str(exc.value) and TOKEN not in str(exc.value) and "Bad credentials" not in str(exc.value)


def test_a_network_failure_or_a_timeout_is_a_plain_sentence(monkeypatch):
    import urllib.error

    def boom(request, timeout):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(recovery._OPENER, "open", boom)
    with pytest.raises(recovery.GitHubError, match="didn't answer within 15 seconds"):
        recovery.GitHub(TOKEN).deploy_runs()

    def refused(request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError("nope"))

    monkeypatch.setattr(recovery._OPENER, "open", refused)
    with pytest.raises(recovery.GitHubError, match="Couldn't reach GitHub"):
        recovery.GitHub(TOKEN).deploy_runs()


def test_the_real_transport_never_follows_a_redirect():
    assert any(isinstance(h, recovery._NoRedirect) for h in recovery._OPENER.handlers)
    assert recovery._NoRedirect().redirect_request(None, None, 301, "Moved", {}, "https://evil.example/") is None


def test_the_client_never_shows_its_token_and_without_one_calls_nothing():
    gh, _ = client_with()
    assert TOKEN not in repr(gh) and TOKEN not in str(gh) and TOKEN not in f"{gh!r} {[gh]}"
    empty = recovery.GitHub("", transport=Transport())
    assert not empty.configured
    with pytest.raises(recovery.GitHubError, match="aren't configured"):
        empty.deploy_runs()
    assert empty.transport.calls == []


def test_odd_runs_from_github_are_dropped_not_shown():
    gh, _ = client_with(Transport(runs=[run(1, SHA[0]), {**run(2, SHA[1]), "html_url": "javascript:alert(1)"},
                                        {**run(3, "nothex"), }, {**run(4, SHA[2]), "id": "4"}, "junk",
                                        {**run(5, SHA[3]), "created_at": "yesterday"}]))
    assert [r["id"] for r in gh.deploy_runs()] == [1]


# ── the pure views ─────────────────────────────────────────────────────────────────────────────────
def parsed(*raw):
    return [recovery._run(r) for r in raw]


def test_how_far_main_is_ahead_counts_pushes_newer_than_the_live_commit():
    runs = parsed(run(5, SHA[3], "in_progress", minutes_ago=2), run(4, SHA[2], conclusion="failure"),
                  run(3, SHA[2], conclusion="cancelled"), run(2, SHA[1]), run(1, SHA[0]))
    assert recovery.pipeline(runs, SHA[1], NOW)["ahead"] == 2            # SHA[3] and SHA[2] (twice): two pushes
    assert recovery.pipeline(runs, SHA[3], NOW)["ahead"] == 0
    assert recovery.pipeline(runs, SHA[6], NOW)["ahead"] is None         # not in the list: far behind, or a local build
    assert recovery.pipeline(runs, "dev", NOW)["ahead"] is None


def test_a_rollback_holding_traffic_back_is_named():
    runs = parsed(run(3, SHA[2]), run(2, SHA[1]), run(1, SHA[0]))
    assert recovery.pipeline(runs, SHA[1], NOW)["held_back"]               # a newer green deploy isn't serving
    assert not recovery.pipeline(runs, SHA[2], NOW)["held_back"]


def test_stuck_is_only_the_newest_run_and_only_after_twenty_minutes():
    young = parsed(run(2, SHA[1], "in_progress", minutes_ago=19), run(1, SHA[0]))
    old = parsed(run(2, SHA[1], "in_progress", minutes_ago=21), run(1, SHA[0]))
    queued = parsed(run(2, SHA[1], "queued", minutes_ago=45))
    assert recovery.pipeline(young, SHA[0], NOW)["stuck"] is None
    assert recovery.pipeline(old, SHA[0], NOW)["stuck"]["id"] == 2
    assert recovery.pipeline(queued, SHA[0], NOW)["stuck"]["id"] == 2
    assert recovery.pipeline(parsed(run(3, SHA[2]), *old), SHA[0], NOW)["stuck"] is None   # not the newest: not offered


def test_re_run_is_offered_only_for_the_newest_run_when_it_failed():
    assert recovery.pipeline(parsed(run(2, SHA[1], conclusion="failure"), run(1, SHA[0])), SHA[0], NOW)["failed"]["id"] == 2
    # an older failure under a newer green run: re-running it would put old code live
    assert recovery.pipeline(parsed(run(2, SHA[1]), run(1, SHA[0], conclusion="failure")), SHA[1], NOW)["failed"] is None


def test_same_origin_wants_this_portal_and_refuses_when_it_cant_tell():
    roots = ["https://portal.example/", ""]
    assert recovery.same_origin("https://portal.example", None, roots)
    assert not recovery.same_origin("https://evil.example", "https://portal.example/admin", roots)    # Origin wins
    assert recovery.same_origin(None, "https://portal.example/admin", roots)
    assert not recovery.same_origin(None, "https://portal.example.evil.example/admin", roots)
    assert not recovery.same_origin(None, None, roots)
    assert not recovery.same_origin("null", None, roots)


def test_the_log_keeps_twenty_and_writes_one_json_line_each():
    lines = []
    log = recovery.Log(clock=lambda: 5.0, out=lines.append)
    for i in range(25):
        log.add("rollback", ADMIN, f"try {i}", True)
    assert len(log.recent()) == 20 and log.recent()[0]["result"] == "try 24"
    assert json.loads(lines[-1]) == {"event": "recovery", "action": "rollback", "by": ADMIN, "result": "try 24", "ok": True}


# ── the private Skippy's controls (workshop.py) ───────────────────────────────────────────────────
CONV = "conv-12345678"


@pytest.fixture
def shop():
    import home as home_mod
    clock = type("C", (), {"now": 1000.0, "__call__": lambda self: self.now})()
    home = home_mod.Home(FakeVm(), clock=clock)
    home.accept({"capabilities": {"workshop": {"ready": True}}})
    return workshop_mod.Workshop(home, clock=clock), home, clock


def test_stopping_a_queued_message_withdraws_it_so_it_is_never_delivered(shop):
    ws, home, _ = shop
    task = ws.send(ADMIN, CONV, "go")
    assert ws.stop(ADMIN, task)
    assert home.jobs_reply()["jobs"] == []                                  # never handed to skippy-pc (audit L06)
    assert ws.read(ADMIN, task)["state"] == "error" and ws.read(ADMIN, task)["error"] == "stopped"
    assert ws.send(ADMIN, CONV, "next")                                     # and the chat is free again


def test_cancel_current_stops_a_running_task_and_withdraws_its_job(shop):
    ws, home, _ = shop
    task = ws.send(ADMIN, CONV, "go")
    ws.push(task, ADMIN, CONV, 0, [], "running", None)
    assert ws.cancel_current() == [task]
    assert home._jobs[task]["event"].is_set()                               # the queue no longer offers it
    assert ws.push(task, ADMIN, CONV, 0, [], "running", None)["stop"] is True  # and the bridge is told on the next push
    assert ws.current()["stopping"] and ws.cancel_current() == [task]         # still running until the bridge says it stopped
    ws.push(task, ADMIN, CONV, 0, [], "error", "stopped")
    assert ws.cancel_current() == [] and not ws.current()["open"]


def test_clear_ends_a_stuck_record_and_a_late_running_push_cannot_reopen_it(shop):
    ws, _, clock = shop
    task = ws.send(ADMIN, CONV, "go")
    ws.push(task, ADMIN, CONV, 0, [], "running", None)
    clock.now += workshop_mod.STALL_S + 1
    assert ws.current()["state"] == "stalled"
    assert ws.clear_stuck() == [task]
    assert ws.read(ADMIN, task)["state"] == "error"
    assert ws.push(task, ADMIN, CONV, 0, [], "running", None)["stop"] is True
    assert ws.read(ADMIN, task)["state"] == "error" and ws.clear_stuck() == []


def test_pause_refuses_new_messages_cancels_the_run_and_resume_lifts_it(shop):
    ws, home, _ = shop
    task = ws.send(ADMIN, CONV, "go")
    assert ws.pause(ADMIN) == [task] and home.jobs_reply()["jobs"] == []
    with pytest.raises(workshop_mod.Paused, match="paused"):
        ws.send(ADMIN, CONV, "again")
    assert isinstance(workshop_mod.Paused("x"), workshop_mod.NotReady)     # the route answers it as a plain 503
    assert ws.resume() and not ws.resume()
    assert ws.send(ADMIN, CONV, "again")


# ── the routes ─────────────────────────────────────────────────────────────────────────────────────
def make_app(transport=None, token=TOKEN):
    t = transport or Transport(runs=[run(9, SHA[1], conclusion="failure"), run(8, SHA[0])])
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), github=recovery.GitHub(token, transport=t))
    app.extensions["home"].accept({"capabilities": {"workshop": {"ready": True}}})
    return app, app.test_client(), t


@pytest.fixture
def env():
    return make_app()


def admin_page(client) -> str:
    """The Admin page as text: apostrophes unescaped, runs of whitespace (template line breaks) as one space."""
    return " ".join(html.unescape(client.get("/admin").data.decode()).split())


def press(client, headers=ORIGIN):
    return client.post("/admin/recovery/repair", data={}, headers=headers)


def test_only_the_captain_can_press_it_and_only_from_the_portals_page(env, capsys):
    app, client, t = env
    assert press(client).status_code in (302, 401, 403) and not [c for c in t.calls if c["method"] == "POST"]   # signed out
    login(client, FRIEND)
    assert press(client).status_code == 403
    assert client.get("/admin/recovery/state").status_code == 403
    login(client, ADMIN)
    assert press(client, headers={"Origin": "https://evil.example"}).status_code == 403
    assert "didn't come from the portal's own page" in capsys.readouterr().out          # logged
    assert not [c for c in t.calls if c["method"] == "POST"]


def test_it_acts_at_once_redeploys_a_failed_portal_clears_stuck_records_and_says_skippy_pc_is_unreachable(env):
    app, client, t = env
    login(client, ADMIN)
    resp = press(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("#recovery")       # no confirm page: it acted
    posts = [c for c in t.calls if c["method"] == "POST"]
    assert [c["url"].split("/actions/")[1] for c in posts] == ["workflows/deploy.yml/dispatches"] and posts[0]["body"] == {"ref": "main"}
    page = admin_page(client)
    assert "redeploying main (the newest deploy" in page and "starter isn't answering" in page
    assert TOKEN not in page
    assert press(client).status_code == 302 and press(client).status_code == 302
    assert "Already putting everything back" in admin_page(client)                          # twice in five minutes, then it waits


def test_a_healthy_portal_is_not_redeployed_and_a_running_deploy_is_left_alone():
    app, client, t = make_app(Transport(runs=[run(9, SHA[0])]))
    login(client, ADMIN)
    press(client)
    assert not [c for c in t.calls if c["method"] == "POST"]
    app2, client2, t2 = make_app(Transport(runs=[run(10, SHA[2], status="in_progress"), run(9, SHA[0])]))
    login(client2, ADMIN)
    press(client2)
    assert not [c for c in t2.calls if c["method"] == "POST"]


def test_with_the_starter_answering_the_button_asks_it_to_put_skippy_pc_back_with_what_is_failing(env):
    app, client, t = env
    starter, doctor = app.extensions["starter"], app.extensions["doctor"]
    starter.poll({"status": {"docker": True, "containers_up": 9, "containers_total": 10, "bridge": True}})
    doctor.observe_home({"flask-api": {"ok": False, "ms": 5000, "why": "no answer in time"}, "whisper": {"ok": True, "ms": 12, "why": ""}})
    login(client, ADMIN)
    press(client)
    (cmd,) = starter.poll({"status": {"docker": True}})
    assert cmd["op"] == "repair" and cmd["arg"] == "flask-api"
    state = client.get("/admin/recovery/state").get_json()
    assert state["repair"]["done"] is False
    starter.poll({"results": [{"id": cmd["id"], "ok": True, "summary": "Docker up; 10 of 10 containers; flask-api recreated."}]})
    state = client.get("/admin/recovery/state").get_json()
    assert state["repair"] == {**state["repair"], "done": True, "ok": True, "summary": "Docker up; 10 of 10 containers; flask-api recreated."}


def test_the_checklist_shows_every_part_and_why_and_a_friend_never_sees_it(env):
    app, client, t = env
    app.extensions["doctor"].observe_home({"voice-gate": {"ok": False, "ms": 30, "why": "Whisper isn't answering behind it"}})
    login(client, ADMIN)
    rows = {r["name"]: r for r in client.get("/admin/recovery/state").get_json()["rows"]}
    assert rows["portal"]["ok"] is False and "failed" in rows["portal"]["why"]
    assert rows["starter"]["ok"] is False and rows["voice-gate"]["why"] == "Whisper isn't answering behind it"
    assert {"portal", "starter", "home-reporter", "voice-gate", "workshop"} <= set(rows)
    page = admin_page(client)
    assert "Put everything back" in page and 'action="/admin/recovery/repair"' in page and "Whisper isn't answering behind it" in page
    for gone in ("Roll back the portal", "Restart service", "Start SkippyTel", "Cancel the current run", "are you sure"):
        assert gone not in page, gone
    login(client, FRIEND)
    assert "Put everything back" not in client.get("/").data.decode()


def test_recovery_is_never_a_chat_tool():
    import chat_tools
    names = {t["name"] for t in chat_tools.ChatTools(FakeVm(), is_admin=True).schemas()}
    assert not {n for n in names if "recover" in n or "repair" in n or "rollback" in n}


def test_the_one_dispatch_it_may_send_besides_rollback_is_a_deploy_of_main():
    gh, t = client_with()
    gh.dispatch_deploy()
    (call,) = t.calls
    assert call["method"] == "POST" and call["url"].endswith(f"/repos/{REPO}/actions/workflows/deploy.yml/dispatches")
    assert call["body"] == {"ref": "main"}
