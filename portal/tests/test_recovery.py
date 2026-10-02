"""The Admin page's recovery buttons (recovery.py, recovery_web.py): the GitHub client's allow-list, the pure views, every
route's guards (admin only, own origin, confirm, rate limit), the private Skippy's cancel / clear / pause, and the cards
with and without the token. The token must never appear in a page, a flash, a log line or an error."""
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
    for method, path in (("GET", f"/repos/{REPO}/contents/portal/app.py"), ("POST", f"/repos/{REPO}/actions/workflows/deploy.yml/dispatches"),
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


def press(client, action, confirm="yes", headers=ORIGIN, **fields):
    return client.post(f"/admin/recovery/{action}", data={"confirm": confirm, **fields}, headers=headers)


def test_only_the_captain_can_press_them(env):
    _, client, t = env
    assert press(client, "rollback").status_code == 302                        # signed out: to sign-in
    login(client, FRIEND)
    for action in recovery.ACTIONS:
        assert press(client, action).status_code == 403, action
    _add_friend(client, "pal@example.com", ["skippy", "dragonwilds", "experiments"])
    login(client, "pal@example.com")
    assert press(client, "workshop-pause").status_code == 403
    assert t.calls == []


def test_a_post_from_anywhere_else_is_refused(env, capsys):
    _, client, t = env
    login(client, ADMIN)
    assert press(client, "rollback", headers={}).status_code == 403                                 # no Origin, no Referer
    assert press(client, "rollback", headers={"Origin": "https://evil.example"}).status_code == 403
    assert press(client, "rollback", headers={"Referer": "https://evil.example/admin"}).status_code == 403
    assert t.calls == []
    assert '"result": "refused: the request didn\'t come from the portal\'s own page"' in capsys.readouterr().out
    assert press(client, "rollback", headers={"Referer": "http://localhost/admin"}).status_code == 302   # Referer fallback


def test_nothing_happens_without_a_confirm_the_server_asks_instead(env):
    _, client, t = env
    login(client, ADMIN)
    page = press(client, "rollback", confirm="", revision="latest")
    html = page.data.decode()
    assert page.status_code == 200 and "Roll the portal back?" in html and 'name="confirm" value="yes"' in html
    assert 'name="revision" value="latest"' in html                       # the answer carries the same input forward
    assert t.calls == []


def test_an_unknown_action_is_a_404(env):
    _, client, t = env
    login(client, ADMIN)
    assert press(client, "deploy").status_code == 404 and press(client, "disable-workflows").status_code == 404
    assert t.calls == []


def test_rollback_dispatches_with_a_checked_input_and_is_rate_limited(env, capsys):
    _, client, t = env
    login(client, ADMIN)
    assert press(client, "rollback", revision="skippy-portal-1; rm -rf /").status_code == 302
    assert t.calls == []                                                   # refused before GitHub was asked
    press(client, "rollback", revision="")
    assert [c["body"] for c in t.calls] == [{"ref": "main", "inputs": {"revision": ""}}]
    press(client, "rollback", revision="")                                 # again within 2 minutes: refused
    assert len(t.calls) == 1
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if '"event": "recovery"' in line]
    assert [(e["action"], e["ok"]) for e in lines] == [("rollback", False), ("rollback", True), ("rollback", False)]
    assert all(e["by"] == ADMIN for e in lines) and "already done just now" in lines[-1]["result"]
    page = admin_page(client)
    assert "Rollback started" in page and "Recent recovery actions" in page


def test_undo_sends_latest(env):
    _, client, t = env
    login(client, ADMIN)
    press(client, "undo-rollback")
    assert t.calls[-1]["body"] == {"ref": "main", "inputs": {"revision": "latest"}}


def test_re_run_targets_the_newest_run_and_only_when_it_failed():
    _, client, t = make_app(Transport(runs=[run(9, SHA[1], conclusion="failure"), run(8, SHA[0])]))
    login(client, ADMIN)
    press(client, "rerun-failed")
    assert t.calls[-1]["url"].endswith("/actions/runs/9/rerun-failed-jobs")
    _, client, t = make_app(Transport(runs=[run(9, SHA[1]), run(8, SHA[0], conclusion="failure")]))
    login(client, ADMIN)
    press(client, "rerun-failed")
    assert all(c["method"] == "GET" for c in t.calls)                     # looked, didn't touch the older failure
    assert "nothing to re-run" in admin_page(client)


def test_cancel_stuck_cancels_only_a_run_older_than_twenty_minutes_and_dispatches_no_rollback(monkeypatch):
    real_now = datetime.now(timezone.utc)
    stuck = {**run(9, SHA[1], "in_progress"), "created_at": (real_now - timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _, client, t = make_app(Transport(runs=[stuck]))
    login(client, ADMIN)
    press(client, "cancel-stuck")
    posts = [c for c in t.calls if c["method"] == "POST"]
    assert [c["url"].rsplit("/actions/", 1)[1] for c in posts] == ["runs/9/cancel"]   # its own rollback step does the rest (A2)
    fresh = {**stuck, "created_at": (real_now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _, client, t = make_app(Transport(runs=[fresh]))
    login(client, ADMIN)
    press(client, "cancel-stuck")
    assert not [c for c in t.calls if c["method"] == "POST"]


def test_a_github_failure_is_a_plain_flash_and_the_token_never_shows_anywhere(capsys):
    app, client, t = make_app(Transport(status=401))
    login(client, ADMIN)
    press(client, "rollback")
    page = admin_page(client)
    assert "refused the portal's token" in page
    out = capsys.readouterr().out
    assert TOKEN not in page and TOKEN not in out and "Bad credentials" not in page
    assert "GITHUB_ACTIONS_TOKEN" not in app.config                        # templates are handed app.config


def test_the_token_from_settings_goes_into_the_client_and_out_of_the_config():
    app = portal.create_app({**SETTINGS, "GITHUB_ACTIONS_TOKEN": TOKEN}, vm=FakeVm(), skippy=FakeSkippy())
    assert "GITHUB_ACTIONS_TOKEN" not in app.config and app.extensions["recovery"]["github"].configured
    assert not portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy()).extensions["recovery"]["github"].configured


def test_workshop_buttons_pause_cancel_and_clear_through_the_routes(env):
    app, client, _ = env
    login(client, ADMIN)
    h = {"X-Workshop": "1"}
    task = client.post("/me/workshop/send", json={"conv": CONV, "message": "go"}, headers=h).get_json()["task"]
    home = app.extensions["home"]
    press(client, "workshop-cancel")
    assert home.jobs_reply()["jobs"] == []
    assert client.get(f"/me/workshop/events?task={task}", headers=h).get_json()["state"] == "error"
    press(client, "workshop-pause")
    refused = client.post("/me/workshop/send", json={"conv": CONV, "message": "again"}, headers=h)
    assert refused.status_code == 503 and "paused" in refused.get_json()["error"]
    assert "Paused by" in admin_page(client)
    press(client, "workshop-resume")
    task2 = client.post("/me/workshop/send", json={"conv": CONV, "message": "again"}, headers=h).get_json()["task"]
    press(client, "workshop-clear")
    assert client.get(f"/me/workshop/events?task={task2}", headers=h).get_json()["error"] == "Cleared from the Admin page."


def test_the_workshop_buttons_work_without_the_token(capsys):
    _, client, t = make_app(token="")
    login(client, ADMIN)
    assert press(client, "workshop-pause").status_code == 302 and "paused" in admin_page(client)
    assert press(client, "rollback").status_code == 302
    assert t.calls == [] and "aren't configured" in admin_page(client)


# ── the cards ──────────────────────────────────────────────────────────────────────────────────────
def test_without_the_token_the_pipeline_card_says_so_and_has_no_buttons():
    _, client, t = make_app(token="")
    login(client, ADMIN)
    page = admin_page(client)
    assert 'id="live-version"' in page and 'id="deploy-pipeline"' in page and 'id="workshop-recovery"' in page
    assert "Not configured" in page
    for action in ("rollback", "undo-rollback", "rerun-failed", "cancel-stuck"):
        assert f"/admin/recovery/{action}" not in page, action
    assert "/admin/recovery/workshop-pause" in page                       # Phase 1 needs no token
    assert t.calls == []


def test_with_the_token_the_cards_show_runs_and_how_far_main_is_ahead(monkeypatch):
    monkeypatch.setenv("GIT_SHA", SHA[0])
    _, client, t = make_app(Transport(runs=[run(10, SHA[2], "in_progress", minutes_ago=1), run(9, SHA[1], conclusion="failure"),
                                            run(8, SHA[0])]))
    login(client, ADMIN)
    page = admin_page(client)
    assert f"main is 2 pushes ahead of what's live (newest: {SHA[2][:7]}, running)." in page
    assert f"https://github.com/{REPO}/actions/runs/9" in page and SHA[1][:7] in page
    for action in ("rollback", "undo-rollback"):
        assert f"/admin/recovery/{action}" in page
    assert "/admin/recovery/rerun-failed" not in page                     # the newest run is running, not failed
    assert "/admin/recovery/cancel-stuck" not in page                     # and only a minute old
    assert "undone by" in page or "next push to main" in page             # audit A3, said plainly
    assert "GitHub mobile" in page
    assert TOKEN not in page
    client.get("/admin")
    assert sum(c["method"] == "GET" for c in t.calls) == 1               # the run list is cached between page loads


def test_the_stuck_and_failed_buttons_appear_only_when_they_apply():
    _, client, _ = make_app(Transport(runs=[run(9, SHA[1], "in_progress", minutes_ago=25), run(8, SHA[0])]))
    login(client, ADMIN)
    page = admin_page(client)
    assert "/admin/recovery/cancel-stuck" in page and "/admin/recovery/rerun-failed" not in page
    assert re.search(rf"Deploy {SHA[1][:7]} started 2\d min ago and is still running: stuck\.", page)   # NOW is import time
    _, client, _ = make_app(Transport(runs=[run(9, SHA[1], conclusion="failure"), run(8, SHA[0])]))
    login(client, ADMIN)
    page = admin_page(client)
    assert "/admin/recovery/rerun-failed" in page and "/admin/recovery/cancel-stuck" not in page


def test_the_buttons_act_at_once_with_no_pop_up(env):
    """The Captain's rule: no confirm boxes. Each form fills in confirm=yes itself; a POST without it (not from this page)
    still gets the server's own confirm page (test_nothing_happens_without_a_confirm_the_server_asks_instead)."""
    _, client, _ = env
    login(client, ADMIN)
    raw = client.get("/admin").data.decode()
    forms = raw.count('action="/admin/recovery/')
    assert forms >= 4 and raw.count("onsubmit=\"this.elements.confirm.value = 'yes';\"") == forms
    assert "confirm(" not in raw


def test_a_friend_never_sees_the_cards(env):
    _, client, t = env
    _add_friend(client, "pal@example.com", ["skippy"])
    login(client, "pal@example.com")
    assert client.get("/admin").status_code == 403
    assert "/admin/recovery" not in client.get("/").data.decode()
    assert t.calls == []


def test_recovery_is_never_a_chat_tool():
    import chat_tools
    import inspect
    assert "recovery" not in inspect.getsource(chat_tools).lower()
