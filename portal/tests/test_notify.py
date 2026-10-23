"""Tell me when it's done: a Discord DM to the Captain, and only him, once per private-Skippy run that this portal saw
going and saw end; never again for a run a restarted portal re-learns; switchable; short, no code, no secrets; failures
swallowed and logged once. Every Discord call goes to a fake transport: no real message is ever sent."""
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import home as home_mod  # noqa: E402
import notify as notify_mod  # noqa: E402
import workshop as workshop_mod  # noqa: E402
from test_app import ADMIN, FRIEND, FakeSkippy, FakeVm, SETTINGS, login  # noqa: E402
from test_workshop import CONV, TEXT, Clock  # noqa: E402

import app as portal  # noqa: E402

BOT = "bot-token-SECRET-do-not-leak"
GUILD, OWNER_ID, CAPTAIN_ID, DM = "111111111111111111", "222222222222222222", "333333333333333333", "444444444444444444"
H = {"X-Workshop": "1"}


class Discord:
    """Discord's API as the notifier uses it: the guild, opening a DM, posting into it. Records every call."""

    def __init__(self, status=200, owner=OWNER_ID):
        self.calls, self.status, self.owner = [], status, owner

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "auth": headers.get("Authorization"),
                           "body": json.loads(body) if body else None})
        if self.status != 200:
            return self.status, b""
        if url.endswith(f"/guilds/{GUILD}"):
            return 200, json.dumps({"id": GUILD, "owner_id": self.owner}).encode()
        if url.endswith("/users/@me/channels"):
            return 200, json.dumps({"id": DM}).encode()
        if url.endswith(f"/channels/{DM}/messages"):
            return 200, b'{"id": "1"}'
        return 404, b""

    def dms(self):
        return [c for c in self.calls if c["url"].endswith("/messages")]


def notifier(vm=None, transport=None, logs=None, token=BOT):
    vm = vm or FakeVm()
    vm.state.env["DISCORD_GUILD_ID"] = GUILD
    return notify_mod.Notifier(token, vm, "https://portal.example", transport=transport or Discord(), background=False,
                               log=(logs.append if logs is not None else (lambda line: None)))


def shop_with(n):
    clock = Clock()
    home = home_mod.Home(FakeVm(), clock=clock)
    home.accept({"capabilities": {"workshop": {"ready": True}}})
    ws = workshop_mod.Workshop(home, clock=clock)
    told = []

    def ended(news):
        told.append(news)
        n.finished(news["owner"], news["task"], "SkippyTel", news["request"], news["state"], news["error"],
                   news["last_text"], news["finished_age_s"])
    ws.on_final = ended
    return ws, told


# ── the message ────────────────────────────────────────────────────────────
def test_one_short_line_to_the_captain_and_nobody_else():
    d = Discord()
    n = notifier(transport=d)
    assert n.finished(ADMIN, "abcd1234", "SkippyTel", "make the `games` tile @everyone orange\nplease", "done", None,
                      "Done, it's live.", 3.0)
    open_dm, post = d.calls[1], d.calls[2]
    assert d.calls[0]["url"].endswith(f"/guilds/{GUILD}")                       # who owns the server: the Captain
    assert open_dm["body"] == {"recipient_id": OWNER_ID}
    text = post["body"]["content"]
    assert text == "Skippy · SkippyTel: “make the games tile everyone orange please” done. https://portal.example/"
    assert post["body"]["allowed_mentions"] == {"parse": []}                     # pings nobody, whatever the text says
    assert all(c["auth"] == f"Bot {BOT}" for c in d.calls) and BOT not in json.dumps([c["body"] for c in d.calls])


def test_discord_captain_id_wins_and_a_bad_one_sends_nothing():
    vm = FakeVm()
    vm.state.env["DISCORD_CAPTAIN_ID"] = CAPTAIN_ID
    d = Discord()
    notifier(vm, d).finished(ADMIN, "abcd1234", "SkippyTel", "x", "done", None, "", 1.0)
    assert d.calls[0]["body"] == {"recipient_id": CAPTAIN_ID} and len(d.calls) == 2      # no guild lookup needed
    vm.state.env["DISCORD_CAPTAIN_ID"] = "everyone"
    d2, logs = Discord(), []
    assert not notifier(vm, d2, logs).finished(ADMIN, "abcd5678", "SkippyTel", "x", "done", None, "", 1.0)
    assert d2.calls == [] and "no_captain" in logs[0]


@pytest.mark.parametrize("state, error, last, outcome", [
    ("done", None, "All green.", "done"),
    ("done", None, "Should I also redeploy the reporter?", "has a question for you"),
    ("error", "stopped", "", "stopped"),
    ("error", "tests failed: KeyError in portal/app.py", "", "failed"),           # the error text itself is never sent
])
def test_the_outcome_is_one_word_or_the_question(state, error, last, outcome):
    d = Discord()
    notifier(transport=d).finished(ADMIN, "abcd1234", "sigma-ground", "fix it", state, error, last, 1.0)
    text = d.dms()[0]["body"]["content"]
    assert text == f"Skippy · sigma-ground: “fix it” {outcome}. https://portal.example/"
    assert "KeyError" not in text


def test_a_long_request_is_cut_and_the_whole_line_stays_under_200():
    d = Discord()
    long_url = "https://" + "p" * 150 + ".example"
    n = notify_mod.Notifier(BOT, FakeVm(), long_url, transport=d, background=False)
    n.vm.state.env["DISCORD_GUILD_ID"] = GUILD
    n.finished(ADMIN, "abcd1234", "SkippyTel", "word " * 200, "done", None, "", 1.0)
    text = d.dms()[0]["body"]["content"]
    assert len(text) <= notify_mod.MAX_CHARS and text.endswith(long_url + "/")
    assert notify_mod.first_words("word " * 200).endswith("…") and len(notify_mod.first_words("word " * 200)) <= 61


# ── once, and only for news ────────────────────────────────────────────────
def test_a_run_this_portal_saw_going_dms_once_when_it_ends():
    d = Discord()
    ws, told = shop_with(notifier(transport=d))
    task = ws.send(ADMIN, CONV, "make it green")
    ws.push(task, ADMIN, CONV, 0, [TEXT], "running", None)
    ws.push(task, ADMIN, CONV, 1, [], "done", None, "", 0.5)
    ws.push(task, ADMIN, CONV, 1, [], "done", None, "", 6.0)                    # the reconciler repeating the end
    ws.push(task, ADMIN, CONV, 1, [], "error", "late guess", "", 9.0)           # a different end replacing it
    assert len(told) == 1 and told[0]["request"] == "make it green" and len(d.dms()) == 1


def test_a_quick_run_that_goes_from_queued_straight_to_done_also_dms():
    d = Discord()
    ws, _ = shop_with(notifier(transport=d))
    task = ws.send(ADMIN, CONV, "tiny")
    ws.push(task, ADMIN, CONV, 0, [TEXT], "done", None, "", 0.1)
    assert len(d.dms()) == 1


def test_a_restarted_portal_relearning_a_finished_run_never_dms_again():
    d = Discord()
    ws, told = shop_with(notifier(transport=d))
    ws.push("abcd1234", ADMIN, CONV, 0, [TEXT], "done", None, "", 30.0)        # created already over
    ws.push("abcd5678", ADMIN, CONV, 0, [TEXT] * 100, "running", None, "", 30.0)  # caught up in chunks: first chunk ...
    ws.push("abcd5678", ADMIN, CONV, 100, [TEXT], "done", None, "", 31.0)       # ... then the end
    assert told == [] and d.dms() == []


def test_a_run_rebuilt_while_still_going_dms_when_it_ends():
    d = Discord()
    ws, told = shop_with(notifier(transport=d))
    ws.push("abcd1234", ADMIN, CONV, 0, [TEXT], "running", None)                # the portal redeployed mid-run
    ws.push("abcd1234", ADMIN, CONV, 1, [], "done", None, "", 1.0)
    assert len(told) == 1 and told[0]["request"] == "" and len(d.dms()) == 1
    assert "“your request”" in d.dms()[0]["body"]["content"]


def test_a_run_that_ended_over_ten_minutes_ago_never_dms():
    d = Discord()
    n = notifier(transport=d)
    assert not n.finished(ADMIN, "abcd1234", "SkippyTel", "x", "done", None, "", notify_mod.DEDUP_S + 1)
    assert n.finished(ADMIN, "abcd5678", "SkippyTel", "x", "done", None, "", None)    # an older reporter: no age given
    assert not n.finished(ADMIN, "abcd5678", "SkippyTel", "x", "done", None, "", 1.0)  # the same task again: never
    assert len(d.dms()) == 1


# ── the switch ─────────────────────────────────────────────────────────────
def test_the_switch_turns_it_off_per_person_and_survives_a_restart():
    vm, d = FakeVm(), Discord()
    n = notifier(vm, d)
    assert n.enabled(ADMIN)                                                     # default on
    n.set_enabled(ADMIN, False)
    assert not n.finished(ADMIN, "abcd1234", "SkippyTel", "x", "done", None, "", 1.0) and d.calls == []
    assert json.loads(vm.metadata[notify_mod.KEY]) == {"off": [ADMIN]}
    again = notifier(vm, d)                                                    # a new process reads the same switch
    assert not again.enabled(ADMIN) and again.enabled(FRIEND)
    again.set_enabled(ADMIN, True)
    assert again.finished(ADMIN, "abcd5678", "SkippyTel", "x", "done", None, "", 1.0) and len(d.dms()) == 1


def test_no_bot_token_means_no_dm_and_no_call():
    d = Discord()
    assert not notifier(transport=d, token="").finished(ADMIN, "abcd1234", "SkippyTel", "x", "done", None, "", 1.0)
    assert d.calls == [] and "SECRET" not in repr(notifier())


def test_discord_trouble_is_swallowed_and_logged_once():
    logs = []
    n = notifier(transport=Discord(status=503), logs=logs)
    for task in ("abcd0001", "abcd0002", "abcd0003"):
        assert n.finished(ADMIN, task, "SkippyTel", "x", "done", None, "", 1.0) is False
    assert len(logs) == 1 and BOT not in logs[0]

    def boom(*a):
        raise OSError("network down")
    n2 = notifier(transport=boom, logs=logs)
    assert n2.finished(ADMIN, "abcd0004", "SkippyTel", "x", "done", None, "", 1.0) is False and len(logs) == 2


# ── the app ────────────────────────────────────────────────────────────────
@pytest.fixture
def env():
    vm = FakeVm()
    vm.state.env["DISCORD_GUILD_ID"] = GUILD
    app = portal.create_app({**SETTINGS, "DISCORD_BOT_TOKEN": BOT}, vm=vm, skippy=FakeSkippy())
    app.extensions["home"].accept({"capabilities": {"workshop": {"ready": True}}})
    d = Discord()
    n = app.extensions["notifier"]
    n.transport, n.background = d, False
    return app.test_client(), app, d


def test_a_run_ending_through_the_reporters_push_dms_with_the_portals_link(env):
    client, app, d = env
    login(client, ADMIN)
    task = client.post("/me/workshop/send", json={"conv": CONV, "message": "make it green"}, headers=H).get_json()["task"]
    auth = {"Authorization": "Bearer home"}
    app.extensions["home"]._hash_cache = (1e18, home_mod._hash("home"))
    assert client.post(f"/home/workshop/{task}", json={"who": ADMIN, "conv": CONV, "start": 0, "events": [TEXT],
                                                       "state": "running"}, headers=auth).status_code == 200
    assert client.post(f"/home/workshop/{task}", json={"who": ADMIN, "conv": CONV, "start": 1, "events": [],
                                                       "state": "done", "finished_age_s": 0.4}, headers=auth).status_code == 200
    [dm] = d.dms()
    assert dm["body"]["content"] == "Skippy · SkippyTel: “make it green” done. http://localhost/"


def test_the_switch_route_is_admin_only_needs_the_header_and_a_boolean(env):
    client, app, _ = env
    login(client, FRIEND)
    assert client.post("/me/workshop/notify", json={"on": False}, headers=H).status_code == 403
    login(client, ADMIN)
    assert client.post("/me/workshop/notify", json={"on": False}).status_code == 400            # no X-Workshop
    assert client.post("/me/workshop/notify", json={"on": "no"}, headers=H).status_code == 400
    resp = client.post("/me/workshop/notify", json={"on": False}, headers=H)
    assert resp.get_json() == {"on": False, "configured": True}
    assert 'id="ps-notify" data-tel="chat:notify"> Discord me when done' in client.get("/chat").data.decode()   # unchecked by default
    client.post("/me/workshop/notify", json={"on": True}, headers=H)
    assert 'id="ps-notify" data-tel="chat:notify"> Discord me when done' in client.get("/chat").data.decode()   # still unchecked (default off)


def test_without_a_bot_the_switch_is_shown_disabled(env):
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    client = app.test_client()
    login(client, ADMIN)
    page = client.get("/chat").data.decode()
    assert 'id="ps-notify" data-tel="chat:notify" disabled title="The portal has no Discord bot (DISCORD_BOT_TOKEN)"' in page
