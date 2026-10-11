"""The private Skippy on the Captain's dashboard: the task store's rules, its admin-only routes, the reporter's
push endpoint, and the /me layout with the chat on the left."""
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import home as home_mod  # noqa: E402
import workshop as workshop_mod  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, _add_friend, _pair, login  # noqa: E402

import app as portal  # noqa: E402

CONV = "conv-12345678"
TEXT = {"t": "text", "text": "On it."}
TOOL = {"t": "tool", "name": "Bash", "detail": "git push origin main"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def shop():
    clock = Clock()
    home = home_mod.Home(FakeVm(), clock=clock)
    home.accept({"capabilities": {"workshop": {"ready": True, "model": "claude-sonnet-5-5"}}})
    return workshop_mod.Workshop(home, clock=clock), home, clock


# ── the store ─────────────────────────────────────────────────────────────────
def test_a_message_becomes_a_long_lived_home_job_and_its_id_is_the_task_id(shop):
    ws, home, _ = shop
    task = ws.send(ADMIN, CONV, "  make the tile green  ")
    [job] = home.jobs_reply()["jobs"]
    assert job == {"id": task, "kind": "workshop", "conv": CONV, "message": "make the tile green", "who": ADMIN,
                   "project": "skippytel"}
    assert home._jobs[task]["ttl"] == workshop_mod.JOB_TTL > home_mod.JOB_TTL


def test_it_refuses_bad_input_and_a_workshop_that_isnt_reporting(shop):
    ws, home, clock = shop
    for args in ((ADMIN, "x", "hi"), (ADMIN, CONV, "   "), (ADMIN, CONV, "a" * 8001)):
        with pytest.raises(ValueError):
            ws.send(*args)
    clock.now += home_mod.STALE_AFTER + 1                       # home went silent: nothing to hand the job to
    with pytest.raises(workshop_mod.NotReady):
        ws.send(ADMIN, CONV, "hi")


def test_one_task_at_a_time_until_it_finishes(shop):
    ws, _, _ = shop
    task = ws.send(ADMIN, CONV, "one")
    with pytest.raises(workshop_mod.Busy):
        ws.send(ADMIN, CONV, "two")
    ws.push(task, ADMIN, CONV, 0, [TEXT], "done", None)
    assert ws.send(ADMIN, CONV, "two")


def test_pushed_events_are_readable_in_order_and_only_by_their_owner(shop):
    ws, _, _ = shop
    task = ws.send(ADMIN, CONV, "go")
    assert ws.push(task, ADMIN, CONV, 0, [TEXT, TOOL], "running", None) == {"have": 2, "stop": False}
    got = ws.read(ADMIN, task, 0)
    assert got == {"state": "running", "error": None, "events": [TEXT, TOOL], "next": 2, "quiet_s": 0, "project": "skippytel"}
    assert ws.read(ADMIN, task, 1)["events"] == [TOOL]
    assert ws.read("someone@else.com", task, 0)["state"] == "unknown"       # not theirs: as if it didn't exist
    assert ws.read(ADMIN, "0" * 16, 0)["state"] == "unknown"


def test_a_push_that_overlaps_adds_only_the_new_events_and_a_gap_asks_for_more(shop):
    ws, _, _ = shop
    task = ws.send(ADMIN, CONV, "go")
    ws.push(task, ADMIN, CONV, 0, [TEXT], "running", None)
    assert ws.push(task, ADMIN, CONV, 0, [TEXT, TOOL], "running", None)["have"] == 2       # the first was already here
    assert ws.push(task, ADMIN, CONV, 5, [TEXT], "running", None)["have"] == 2             # a gap: nothing taken, "have" says why
    assert ws.read(ADMIN, task, 0)["next"] == 2


def test_a_redeployed_portal_rebuilds_the_task_from_what_home_sends(shop):
    ws, home, clock = shop
    fresh = workshop_mod.Workshop(home, clock=clock)                        # the new instance: no record of the task
    assert fresh.push("abcd1234ef", ADMIN, CONV, 0, [TEXT, TOOL], "running", None)["have"] == 2
    assert fresh.read(ADMIN, "abcd1234ef", 0)["events"] == [TEXT, TOOL]      # the owner comes from home's push
    assert fresh.read(FRIEND, "abcd1234ef", 0)["state"] == "unknown"


def test_events_from_home_are_cleaned_before_they_can_reach_a_browser(shop):
    ws, _, _ = shop
    task = ws.send(ADMIN, CONV, "go")
    ws.push(task, ADMIN, CONV, 0, [{"t": "text", "text": "x" * 9000}, {"t": "html", "text": "<b>"}, "junk",
                                   {"t": "tool", "name": "N" * 99, "detail": "d" * 999, "secret": "s"}], "running", None)
    events = ws.read(ADMIN, task, 0)["events"]
    assert [e["t"] for e in events] == ["text", "tool"] and len(events[0]["text"]) == workshop_mod.MAX_TEXT
    assert set(events[1]) == {"t", "name", "detail"} and len(events[1]["name"]) == 40 and len(events[1]["detail"]) == 200
    with pytest.raises(ValueError):
        ws.push(task, ADMIN, CONV, 0, [], "exploding", None)


def test_the_states_a_watcher_sees(shop):
    ws, home, clock = shop
    task = ws.send(ADMIN, CONV, "go")
    assert ws.read(ADMIN, task)["state"] == "queued"
    clock.now += workshop_mod.OFFLINE_S + 1
    assert ws.read(ADMIN, task)["state"] == "offline"                      # skippy-pc never picked it up
    ws.push(task, ADMIN, CONV, 0, [TEXT], "running", None)
    assert ws.read(ADMIN, task)["state"] == "running"
    clock.now += workshop_mod.STALL_S + 1
    assert ws.read(ADMIN, task)["state"] == "stalled"                      # the link went quiet mid-run
    ws.push(task, ADMIN, CONV, 1, [], "error", "stopped")
    assert ws.read(ADMIN, task) | {"events": None} == {"state": "error", "error": "stopped", "events": None, "next": 1,
                                                       "project": "skippytel"}


def test_reading_a_running_task_keeps_the_reporters_quick_poll_going(shop):
    ws, home, clock = shop
    task = ws.send(ADMIN, CONV, "go")
    home.fast_until = 0
    ws.read(ADMIN, task)
    assert home.fast_until > clock.now


def test_finishing_stops_the_job_being_offered_again_and_stop_reaches_home(shop):
    ws, home, _ = shop
    task = ws.send(ADMIN, CONV, "go")
    assert not ws.stop("someone@else.com", task) and ws.stop(ADMIN, task)
    assert ws.push(task, ADMIN, CONV, 0, [], "running", None)["stop"] is True
    ws.push(task, ADMIN, CONV, 0, [], "error", "stopped")
    assert home.jobs_reply()["jobs"] == [] and not ws.stop(ADMIN, task)


# ── the routes ────────────────────────────────────────────────────────────────
@pytest.fixture
def env():
    vm = FakeVm()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy())
    client = app.test_client()
    app.extensions["home"].accept({"capabilities": {"workshop": {"ready": True, "model": "claude-sonnet-5-5"}}})
    return client, app, app.extensions["home"]


H = {"X-Workshop": "1"}


def post(client, url, body, headers=H):
    return client.post(url, json=body, headers=headers)


def test_only_the_captain_can_use_the_private_skippy(env):
    client, _, _ = env
    assert client.post("/me/workshop/send", json={"conv": CONV, "message": "hi"}, headers=H).status_code == 302   # signed out
    login(client, FRIEND)
    for method, url in (("post", "/me/workshop/send"), ("get", "/me/workshop/events?task=abcd1234"), ("post", "/me/workshop/stop")):
        assert getattr(client, method)(url, headers=H).status_code == 403
    _add_friend(client, "pal@example.com", ["skippy"])
    login(client, "pal@example.com")                                     # a friend with the public Skippy: still no
    assert post(client, "/me/workshop/send", {"conv": CONV, "message": "hi"}).status_code == 403
    assert client.get("/me").status_code == 403


def test_a_page_on_another_site_cannot_drive_it(env):
    client, _, home = env
    login(client, ADMIN)
    assert client.post("/me/workshop/send", json={"conv": CONV, "message": "hi"}).status_code == 400      # no header
    assert client.post("/me/workshop/send", data="message=hi", content_type="text/plain", headers=H).status_code == 400
    assert home.jobs_reply()["jobs"] == []


def test_sending_queues_a_job_for_skippy_pc_and_never_logs_the_message(env, capsys):
    client, _, home = env
    login(client, ADMIN)
    resp = post(client, "/me/workshop/send", {"conv": CONV, "message": "deploy my secret plan"})
    task = resp.get_json()["task"]
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert [(j["kind"], j["conv"], j["message"], j["who"]) for j in home.jobs_reply()["jobs"]] == \
        [("workshop", CONV, "deploy my secret plan", ADMIN)]
    out = capsys.readouterr().out
    assert task in out and "secret plan" not in out                      # who and how long, never what
    assert post(client, "/me/workshop/send", {"conv": CONV, "message": "again"}).status_code == 409   # busy


def test_send_errors_are_plain(env):
    client, app, home = env
    login(client, ADMIN)
    assert post(client, "/me/workshop/send", {"conv": CONV, "message": ""}).status_code == 400
    assert post(client, "/me/workshop/send", {"conv": "x", "message": "hi"}).status_code == 400
    home.report = {"capabilities": {}}                                   # the bridge isn't reporting a workshop
    resp = post(client, "/me/workshop/send", {"conv": CONV, "message": "hi"})
    assert resp.status_code == 503 and "bridge" in resp.get_json()["error"]


def test_home_pushes_progress_with_its_token_and_the_captain_reads_it(env):
    client, _, _ = env
    login(client, ADMIN)
    task = post(client, "/me/workshop/send", {"conv": CONV, "message": "go"}).get_json()["task"]
    token = _pair(client)
    body = {"who": ADMIN, "conv": CONV, "start": 0, "events": [TEXT, TOOL], "state": "running", "error": None}
    assert client.post(f"/home/workshop/{task}", json=body).status_code == 401                            # no token
    assert client.post(f"/home/workshop/{task}", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.post(f"/home/workshop/{task}", json=body, headers={"Authorization": f"Bearer {token}"})
    assert ok.get_json() == {"have": 2, "stop": False}
    got = client.get(f"/me/workshop/events?task={task}&after=1", headers=H).get_json()
    assert got["state"] == "running" and got["events"] == [TOOL] and got["next"] == 2
    bad = client.post(f"/home/workshop/{task}", json={**body, "state": "weird"}, headers={"Authorization": f"Bearer {token}"})
    assert bad.status_code == 400
    assert client.post("/home/workshop/not-a-task", json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_stop_reaches_home_on_its_next_push(env):
    client, _, _ = env
    login(client, ADMIN)
    task = post(client, "/me/workshop/send", {"conv": CONV, "message": "go"}).get_json()["task"]
    token = _pair(client)
    login(client, ADMIN)
    assert post(client, "/me/workshop/stop", {"task": task}).get_json() == {"ok": True}
    reply = client.post(f"/home/workshop/{task}", headers={"Authorization": f"Bearer {token}"},
                        json={"who": ADMIN, "conv": CONV, "start": 0, "events": [], "state": "running", "error": None})
    assert reply.get_json()["stop"] is True


# ── the dashboard ─────────────────────────────────────────────────────────────
def test_the_private_skippy_is_the_front_page_for_the_captain_and_no_longer_on_the_dashboard(env):
    client, _, _ = env
    login(client, ADMIN)
    front = client.get("/").data.decode()
    assert 'id="private-skippy"' in front and 'id="mode-agent"' in front and 'id="mode-quick"' in front      # the agent, and the key to the quick chat
    assert "claude-sonnet-5-5" in front and "workshop isn't answering" not in front
    assert front.index('id="mode-agent"') < front.index('id="mode-quick"')                                     # the agent is what he lands in
    dash = client.get("/me").data.decode()
    assert 'id="private-skippy"' not in dash and 'data-pane="skippy"' not in dash and 'id="server" class="apane"' in dash


def test_the_dashboard_says_plainly_when_the_workshop_is_offline(env):
    client, _, home = env
    home.report = {"capabilities": {}}
    login(client, ADMIN)
    page = client.get("/").data.decode()
    assert "workshop isn't answering" in page and "make bridge" in page and "/login" in page
    assert 'id="private-skippy"' in page                                         # still there; the server refuses politely


def test_the_public_chat_has_no_private_skippy_in_it(env):
    client, _, _ = env
    _add_friend(client, "pal@example.com", ["skippy"])
    login(client, "pal@example.com")                                       # a friend's front page is the public chat alone
    chat_page = client.get("/").data.decode()
    assert "private-skippy" not in chat_page and "workshop" not in chat_page.lower()


def test_resync_marks_the_task_and_wakes_the_reporters_quick_poll_and_only_for_its_owner(shop):
    ws, home, clock = shop
    task = ws.send(ADMIN, CONV, "go")
    ws.push(task, ADMIN, CONV, 0, [TEXT], "running", None)
    home.fast_until = 0.0
    assert ws.resync(ADMIN, task) == {"state": "running", "error": None}
    assert home.fast_until > clock.now                                  # the reporter polls quickly now
    assert ws._tasks[task]["resync"] == clock.now
    assert ws.resync(FRIEND, task) == {"state": "unknown", "error": None}   # not yours: nothing marked
    assert ws.resync(ADMIN, "0123456789abcdef") == {"state": "unknown", "error": None}
    clock.now += workshop_mod.STALL_S + 1
    assert ws.resync(ADMIN, task)["state"] == "stalled"                 # the state it reports is the live one


def test_the_resync_route_is_admin_only_needs_the_workshop_header_and_answers_with_the_state(env):
    client, _, home = env
    assert client.post("/me/workshop/resync", json={"task": "abcd1234"}, headers=H).status_code == 302     # signed out
    login(client, FRIEND)
    assert post(client, "/me/workshop/resync", {"task": "abcd1234"}).status_code == 403                    # not the Captain
    login(client, ADMIN)
    assert client.post("/me/workshop/resync", json={"task": "abcd1234"}).status_code == 400                # no header
    task = post(client, "/me/workshop/send", {"conv": CONV, "message": "go"}).get_json()["task"]
    home.fast_until = 0.0
    out = post(client, "/me/workshop/resync", {"task": task})
    assert out.status_code == 200 and out.get_json() == {"state": "queued", "error": None}
    assert home.fast_until > 0


# ── recovery: what the reporter's reconciler relies on (see services/home-reporter/tests/test_workshop_chaos.py) ──
TASK = "abcd1234ef"


def test_a_push_may_not_create_a_task_nobody_owns(shop):
    ws, _, _ = shop
    for who in ("", "   "):
        with pytest.raises(ValueError):
            ws.push(TASK, who, CONV, 0, [TEXT], "running", None)
    assert ws.listing() == {"tasks": []}
    ws.push(TASK, ADMIN, CONV, 0, [TEXT], "running", None)
    assert ws.push(TASK, "", CONV, 1, [TOOL], "running", None)["have"] == 2        # a task the portal already holds needs no `who`


def test_finishing_stops_the_job_only_when_the_push_really_made_the_task_final(shop):
    ws, home, _ = shop
    task = ws.send(ADMIN, CONV, "go")
    ws.push(task, ADMIN, CONV, 0, [TEXT], "running", None)
    assert ws.push(task, ADMIN, CONV, 5, [TEXT], "done", None)["have"] == 1       # a gap: the portal asked for earlier events first
    assert not home._jobs[task]["event"].is_set()                                  # so the job is still offered
    assert ws.read(ADMIN, task, 0)["state"] == "running"                           # and the end was not taken
    ws.push(task, ADMIN, CONV, 0, [TEXT, TOOL], "done", None)
    assert home._jobs[task]["event"].is_set() and ws.read(ADMIN, task, 0)["state"] == "done"    # now it really ended: stop offering it
    ws.push(task, ADMIN, CONV, 0, [TEXT, TOOL], "done", None)                       # a repeat of the end is harmless


def test_a_stale_running_push_cannot_reopen_a_finished_task_but_a_new_end_replaces_an_old_one(shop):
    ws, _, _ = shop
    task = ws.send(ADMIN, CONV, "go")
    ws.push(task, ADMIN, CONV, 0, [TEXT], "error", "Lost contact")
    ws.push(task, ADMIN, CONV, 0, [TEXT, TOOL], "running", None)                   # a relay's older view, landing late
    got = ws.read(ADMIN, task, 0)
    assert got["state"] == "error" and got["error"] == "Lost contact" and got["events"] == [TEXT, TOOL]      # events still added
    assert "quiet_s" not in got
    ws.push(task, ADMIN, CONV, 2, [], "done", None)                                  # the bridge's truth corrects the guess
    got = ws.read(ADMIN, task, 0)
    assert got["state"] == "done" and got["error"] is None
    ws.push(task, ADMIN, CONV, 2, [], "running", None)
    assert ws.read(ADMIN, task, 0)["state"] == "done"


def test_every_accepted_push_is_a_heartbeat_even_a_gap_or_one_with_nothing_new(shop):
    ws, _, clock = shop
    task = ws.send(ADMIN, CONV, "go")
    ws.push(task, ADMIN, CONV, 0, [TEXT], "running", None)
    clock.now += workshop_mod.STALL_S - 5
    ws.push(task, ADMIN, CONV, 1, [], "running", None)                              # nothing new
    clock.now += workshop_mod.STALL_S - 5
    assert ws.read(ADMIN, task, 0)["state"] == "running"                            # (without the beat above it would be stalled)
    clock.now += 50
    ws.push(task, ADMIN, CONV, 9, [TEXT], "running", None)                          # a gap: skippy-pc is there, the portal is behind
    assert ws.read(ADMIN, task, 0)["state"] == "running" and ws.read(ADMIN, task, 0)["quiet_s"] == 0


def test_a_gap_push_takes_a_waiting_task_out_of_the_queue_state(shop):
    ws, _, clock = shop
    task = ws.send(ADMIN, CONV, "go")
    clock.now += workshop_mod.OFFLINE_S + 1
    assert ws.read(ADMIN, task, 0)["state"] == "offline"
    ws.push(task, ADMIN, CONV, 4, [TEXT], "running", None)                          # it has the run; this portal missed the start
    assert ws.read(ADMIN, task, 0)["state"] == "running"


def test_read_says_how_long_a_running_task_has_been_quiet_and_never_moves_next_backwards(shop):
    ws, _, clock = shop
    task = ws.send(ADMIN, CONV, "go")
    assert ws.read(ADMIN, task, 0)["quiet_s"] == 0
    clock.now += 7
    assert ws.read(ADMIN, task, 0)["quiet_s"] == 7                                  # queued: since it was sent
    ws.push(task, ADMIN, CONV, 0, [TEXT, TOOL], "running", None)
    clock.now += 45
    got = ws.read(ADMIN, task, 0)
    assert got["state"] == "running" and got["quiet_s"] == 45
    clock.now += workshop_mod.STALL_S
    got = ws.read(ADMIN, task, 0)
    assert got["state"] == "stalled" and got["quiet_s"] == 45 + workshop_mod.STALL_S
    assert "quiet_s" not in ws.read(ADMIN, "0" * 16, 0) and ws.read(ADMIN, "0" * 16, 7)["next"] == 7
    ws.push(task, ADMIN, CONV, 2, [], "done", None)
    assert "quiet_s" not in ws.read(ADMIN, task, 0)
    # a portal that was redeployed holds fewer events for a moment than a page has already shown
    fresh = workshop_mod.Workshop(shop[1], clock=clock)
    fresh.push(task, ADMIN, CONV, 0, [TEXT], "running", None)
    got = fresh.read(ADMIN, task, 2)
    assert got["events"] == [] and got["next"] == 2                                  # the page keeps its place: no event twice
    fresh.push(task, ADMIN, CONV, 1, [TOOL, TEXT], "running", None)
    got = fresh.read(ADMIN, task, 2)
    assert got["events"] == [TEXT] and got["next"] == 3


def test_the_listing_is_what_the_reconciler_needs_newest_first(shop):
    ws, home, clock = shop
    old = ws.send(ADMIN, CONV, "one")
    ws.push(old, ADMIN, CONV, 0, [TEXT, TOOL], "done", None)
    clock.now += 10
    new = ws.send(ADMIN, CONV, "two")
    clock.now += 4
    ws.push(new, ADMIN, CONV, 0, [TEXT], "running", None)
    clock.now += 3
    assert ws.listing() == {"tasks": [
        {"task": new, "state": "running", "have": 1, "final": False, "owner": ADMIN, "quiet_s": 3, "want": False},
        {"task": old, "state": "done", "have": 2, "final": True, "owner": ADMIN, "quiet_s": 17, "want": False}]}
    clock.now += workshop_mod.STALL_S
    assert ws.listing()["tasks"][0]["state"] == "stalled"                            # the LIVE state, so the reconciler can see it
    assert ws.listing()["tasks"][1]["final"] is True
    fresh = workshop_mod.Workshop(home, clock=clock)
    assert fresh.listing() == {"tasks": []}


def test_the_listing_marks_a_task_somebody_asked_about_for_two_minutes_and_a_push_answers_it(shop):
    ws, _, clock = shop
    task = ws.send(ADMIN, CONV, "go")
    ws.resync(ADMIN, task)
    assert ws.listing()["tasks"][0]["want"] is True
    clock.now += workshop_mod.WANT_S - 1
    assert ws.listing()["tasks"][0]["want"] is True
    clock.now += 2
    assert ws.listing()["tasks"][0]["want"] is False                                 # asked too long ago to matter
    ws.resync(ADMIN, task)
    ws.push(task, ADMIN, CONV, 0, [TEXT], "running", None)
    assert ws.listing()["tasks"][0]["want"] is False                                 # the reporter answered


def test_the_listing_is_capped_and_pruned_like_the_store(shop):
    ws, home, clock = shop
    for n in range(workshop_mod.MAX_LISTED + 5):
        ws.push(f"{n:08x}", ADMIN, CONV, 0, [TEXT], "done", None)
        clock.now += 1
    tasks = ws.listing()["tasks"]
    assert len(tasks) == workshop_mod.MAX_LISTED and tasks[0]["task"] == f"{workshop_mod.MAX_LISTED + 4:08x}"      # newest first
    clock.now += workshop_mod.KEEP_S
    assert ws.listing() == {"tasks": []}


def test_the_listing_route_wants_the_home_token_is_never_cached_and_lists_what_the_portal_holds(env):
    client, _, _ = env
    login(client, ADMIN)
    task = post(client, "/me/workshop/send", {"conv": CONV, "message": "go"}).get_json()["task"]
    token = _pair(client)
    assert client.get("/home/workshop").status_code == 401                                   # no token
    assert client.get("/home/workshop", headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.get("/home/workshop", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200 and ok.headers["Cache-Control"] == "no-store"
    [row] = ok.get_json()["tasks"]
    assert row["task"] == task and row["state"] == "queued" and row["have"] == 0 and row["final"] is False
    assert row["owner"] == ADMIN and set(row) == {"task", "state", "have", "final", "owner", "quiet_s", "want"}


def test_the_push_route_refuses_a_task_nobody_owns_and_rebuilds_one_it_never_saw_when_told_who(env):
    client, _, _ = env
    login(client, ADMIN)
    token = _pair(client)
    auth = {"Authorization": f"Bearer {token}"}
    body = {"conv": CONV, "start": 0, "events": [TEXT], "state": "running", "error": None}
    assert client.post(f"/home/workshop/{TASK}", json={**body, "who": ""}, headers=auth).status_code == 400
    assert client.post(f"/home/workshop/{TASK}", json=body, headers=auth).status_code == 400            # no `who` at all
    ok = client.post(f"/home/workshop/{TASK}", json={**body, "who": ADMIN}, headers=auth)
    assert ok.status_code == 200 and ok.get_json() == {"have": 1, "stop": False}
    login(client, ADMIN)
    assert client.get(f"/me/workshop/events?task={TASK}&after=0", headers=H).get_json()["events"] == [TEXT]


# ── why the workshop is not ready (the bridge's own words, passed on by the reporter) ──────────────────────────
EXPIRED = "Claude's login on skippy-pc expired: run `claude auth login` there"


def _not_ready(home, **info):
    home.accept({"capabilities": {"workshop": {"ready": False, "model": "claude-sonnet-5-5", **info}}})


def test_the_reason_a_capability_is_not_ready_is_only_ever_one_short_plain_line_and_only_when_it_is_not_ready(shop):
    ws, home, clock = shop
    assert home.capability_reason("workshop") is None                                # ready: nothing is wrong
    _not_ready(home)
    assert home.capability_reason("workshop") is None                                # not ready but nothing said (an older bridge)
    _not_ready(home, reason="  two\nlines \t here  ")
    assert home.capability_reason("workshop") == "two lines here"
    _not_ready(home, reason="x" * 500)
    assert home.capability_reason("workshop") == "x" * 200
    _not_ready(home, reason=7)
    assert home.capability_reason("workshop") is None
    home.accept({"capabilities": {"workshop": {"ready": True, "reason": "stale"}}})
    assert home.capability_reason("workshop") is None                                # ready wins over a leftover reason
    _not_ready(home, reason=EXPIRED)
    assert home.capability_reason("workshop") == EXPIRED and home.capability_reason("make_music") is None
    clock.now += home_mod.STALE_AFTER + 1
    assert home.capability_reason("workshop") is None                                # silent home: no report is coming to say why


def test_send_refuses_with_the_reason_when_the_bridge_gave_one_and_the_old_words_when_not(shop):
    ws, home, _ = shop
    _not_ready(home, login="expired", reason=EXPIRED)
    with pytest.raises(workshop_mod.NotReady) as why:
        ws.send(ADMIN, CONV, "hi")
    assert EXPIRED in str(why.value) and "isn't ready" in str(why.value)
    _not_ready(home)
    with pytest.raises(workshop_mod.NotReady) as why:
        ws.send(ADMIN, CONV, "hi")
    assert "isn't answering" in str(why.value) and "bridge" in str(why.value)


def test_the_card_says_why_with_the_command_copyable_and_the_text_escaped(env):
    client, _, home = env
    _not_ready(home, login="expired", reason=EXPIRED)
    login(client, ADMIN)
    page = client.get("/").data.decode()
    assert "workshop isn't ready: Claude&#39;s login on skippy-pc expired: run " in page
    assert '<code class="code">claude auth login</code> there' in page              # the backticked command is one click to copy
    assert "make bridge" not in page.split('id="private-skippy"')[1].split("ps-log")[0]     # the generic checklist gives way to the cause
    _not_ready(home, reason="<script>alert(1)</script> and `<b>x</b>`")
    page = client.get("/").data.decode()
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert '<code class="code">&lt;b&gt;x&lt;/b&gt;</code>' in page
    _not_ready(home)                                                                 # nothing said: the old checklist
    page = client.get("/").data.decode()
    assert "workshop isn't answering" in page and "make bridge" in page and "isn't ready:" not in page


def test_the_send_route_answers_503_with_the_reason(env):
    client, _, home = env
    _not_ready(home, reason=EXPIRED)
    login(client, ADMIN)
    resp = post(client, "/me/workshop/send", {"conv": CONV, "message": "go"})
    assert resp.status_code == 503 and EXPIRED in resp.get_json()["error"]
