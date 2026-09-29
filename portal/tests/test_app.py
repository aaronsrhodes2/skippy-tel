"""Flask app tests with a fake VM and a fake Claude. Need the real Flask +
Authlib (installed in CI); skipped where they aren't installed."""
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import chat  # noqa: E402
from gcp import VmStatus  # noqa: E402

ADMIN = "aaron.s.rhodes@gmail.com"
FRIEND = "friend@example.com"


class FakeVm:
    def __init__(self, state="TERMINATED"):
        self.state = VmStatus(status=state, machine_type="e2-standard-4", external_ip="35.1.2.3",
                              games=["valheim"], idle_minutes="30",
                              env={"VALHEIM_PASSWORD": "hunter22", "DISCORD_GUILD_ID": "g1",
                                   "DRAGONWILDS_OWNER_ID": "owner1", "DRAGONWILDS_ADMIN_PASSWORD": "adm1n",
                                   "DRAGONWILDS_WORLD_PASSWORD": "dragonpw",
                                   "DISCORD_CHANNEL_VALHEIM": "c1", "DISCORD_VOICE_VALHEIM": "v1"})
        self.calls = []
        self.metadata = {}
        self.serial = {
            1: "boot\nstartup-script: === skippy-games boot now ===\nstartup-script: started: dragonwilds\n",
            2: "=== skippy-diag old ===\nstale\n=== end ===\n"
               "=== skippy-diag new ===\ndragonwilds Up 3 minutes\npassword=dragonpw\n=== end ===\n",
        }

    def status(self):
        return self.state

    def start(self, games, idle_minutes=30):
        self.calls.append(("start", games, idle_minutes))
        self.state.status = "RUNNING"
        return self.state

    def stop(self):
        self.calls.append(("stop",))
        self.state.status = "TERMINATED"
        return self.state

    def restart(self):
        self.calls.append(("restart",))
        return self.state

    def serial_output(self, port):
        return self.serial[port]

    def get_metadata(self, key):
        return self.metadata.get(key, "")

    def set_metadata(self, key, value):
        self.calls.append(("set_metadata", key))
        self.metadata[key] = value

    def update_env(self, updates):
        self.calls.append(("update_env", updates))
        self.state.env.update(updates)


class FakeSkippy:
    def __init__(self):
        self.seen = None

    def reply(self, system, messages, tools=None, run_tool=None, model=None, force_tool=None):
        self.seen = (system, messages)
        self.model, self.force_tool = model, force_tool
        self.tools, self.run_tool = tools, run_tool
        return "Obviously."


SETTINGS = {
    "SECRET_KEY": "test", "SESSION_COOKIE_SECURE": False, "SESSION_COOKIE_SAMESITE": "Lax",
    "GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret",
    "ALLOWED_EMAILS": frozenset({ADMIN, FRIEND}), "ADMIN_EMAILS": frozenset({ADMIN}),
    "GCP_PROJECT": "p", "GCP_ZONE": "z", "GCP_INSTANCE": "i",
    "CHAT_MODEL": "claude-haiku-4-5", "CHAT_ENABLED": True,
    "DISCORD_INVITE": "gPTVS7Hmj", "DISCORD_BOT_TOKEN": "",
    "OPS_EMAILS": frozenset({"skippy-deployer@skippy-tel.iam.gserviceaccount.com"}),
}


@pytest.fixture
def env():
    vm, skippy = FakeVm(), FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy)
    return app.test_client(), vm, skippy


def login(client, email):
    with client.session_transaction() as s:
        s["user"] = {"email": email, "name": "Captain", "picture": None}


def test_signed_out_sees_login(env):
    client, _, _ = env
    assert b"Sign in with Google" in client.get("/").data
    assert client.get("/dashboard").status_code == 302
    assert client.get("/health").data == b"ok"


def test_unlisted_email_is_bounced(env):
    client, _, _ = env
    login(client, "stranger@example.com")
    resp = client.get("/dashboard")
    assert resp.status_code == 302 and "/unauthorized" in resp.location


def test_dashboard_shows_valheim_with_discord_links(env):
    client, _, _ = env
    login(client, FRIEND)
    page = client.get("/dashboard").data.decode()
    assert "Valheim" in page
    assert "https://discord.com/channels/g1/c1" in page
    assert "https://discord.com/channels/g1/v1" in page
    assert "Admin" not in page            # friends don't get the admin link


def test_start_refuses_when_settings_missing(env):
    client, vm, _ = env
    login(client, FRIEND)
    vm.state.env.pop("VALHEIM_PASSWORD")
    client.post("/games/start", data={"games": ["valheim"], "idle_minutes": "45"})
    assert vm.calls == []


def test_start_and_stop(env):
    client, vm, _ = env
    login(client, FRIEND)
    client.post("/games/start", data={"games": ["valheim"], "idle_minutes": "45"})
    assert vm.calls[-1] == ("start", ["valheim"], 45)
    client.post("/games/start", data={"games": ["dragonwilds"]})   # already running: refused
    assert [c for c in vm.calls if c[0] == "start"] == [("start", ["valheim"], 45)]
    blocked = json.loads(vm.metadata["portal-watch"])["blocked_starts"]  # …and the watcher noticed
    assert [(b["who"], b["wanted"], b["running"]) for b in blocked] == [(FRIEND, ["dragonwilds"], ["valheim"])]
    client.post("/games/stop")
    assert vm.calls[-1] == ("stop",)


def test_admin_is_admins_only(env):
    client, _, _ = env
    login(client, FRIEND)
    assert client.get("/admin").status_code == 403
    assert client.post("/admin/restart").status_code == 403
    login(client, ADMIN)
    assert client.get("/admin").status_code == 200


def test_admin_settings_keep_blank_secrets(env):
    client, vm, _ = env
    login(client, ADMIN)
    client.post("/admin/settings", data={"VALHEIM_SERVER_NAME": " Skippy ", "VALHEIM_PASSWORD": ""})
    updates = vm.calls[-1][1]
    assert updates["VALHEIM_SERVER_NAME"] == "Skippy"
    assert "VALHEIM_PASSWORD" not in updates           # blank secret = unchanged
    page = client.get("/admin").data.decode()
    assert "hunter22" not in page                      # secrets never rendered


def test_admin_restart(env):
    client, vm, _ = env
    login(client, ADMIN)
    client.post("/admin/restart")
    assert vm.calls[-1] == ("restart",)


def test_chat_round_trip(env):
    client, _, skippy = env
    login(client, FRIEND)
    resp = client.post("/chat/send", json={"message": "status?",
                                           "history": [{"role": "user", "content": "hi"},
                                                       {"role": "assistant", "content": "yo"}]})
    assert resp.get_json() == {"reply": "Obviously.", "model": None, "tools": [],
                                "mentat_available": False, "mentat_where": None}
    system, messages = skippy.seen
    assert "Captain" in system
    assert messages[-1] == {"role": "user", "content": "status?"}


def test_chat_errors_are_shown_not_raised(env):
    client, _, skippy = env
    login(client, FRIEND)

    def boom(system, messages, **_):
        raise chat.ChatError("Skippy is rate-limited right now.")
    skippy.reply = boom
    resp = client.post("/chat/send", json={"message": "hi"})
    assert resp.status_code == 502
    assert "rate-limited" in resp.get_json()["error"]


def test_friend_one_click_play_dragonwilds_with_guardrail(env):
    client, vm, _ = env
    login(client, FRIEND)
    page = client.get("/dashboard").data.decode()
    assert "Play RuneScape: Dragonwilds" in page
    client.post("/games/start", data={"games": ["dragonwilds"], "idle_minutes": "0"})
    assert vm.calls[-1] == ("start", ["dragonwilds"], 15)          # "never sleep" is admin-only


def test_join_password_shown_to_players_only_while_running(env):
    client, vm, _ = env
    login(client, FRIEND)
    assert "dragonpw" not in client.get("/dashboard").data.decode()   # asleep: nothing to join
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]
    page = client.get("/dashboard").data.decode()
    assert "dragonpw" in page
    assert "Invite Code" in page                               # the reliable way in comes first...
    assert page.index("Invite Code") < page.index("Public</b> tab")   # ...the list search is the fallback
    assert "search <b>Skippy</b>" in page                      # SERVER_NAME default is what players search


def test_settings_reject_values_compose_cant_carry(env):
    client, vm, _ = env
    login(client, ADMIN)
    before = len(vm.calls)
    resp = client.post("/admin/settings", data={"DRAGONWILDS_WORLD_PASSWORD": "it's",
                                                "DRAGONWILDS_SERVER_NAME": "SkippyScape"},
                       follow_redirects=True)
    assert "single quote" in resp.data.decode()
    assert len(vm.calls) == before                             # nothing saved, not even the valid field


# ── Skippy's chat tools ───────────────────────────────────────────────────────

def _tools_for(client, skippy, email):
    login(client, email)
    client.post("/chat/send", json={"message": "is it up?"})
    return {t["name"] for t in skippy.tools}, skippy.run_tool


def test_chat_tools_match_dashboard_rights(env):
    client, _, skippy = env
    names, _ = _tools_for(client, skippy, FRIEND)
    assert names == {"server_status", "start_game", "stop_server", "now_playing"}   # core users have Bilby
    names, _ = _tools_for(client, skippy, ADMIN)
    assert "restart_server" in names
    _, run = _tools_for(client, skippy, FRIEND)
    with pytest.raises(Exception, match="isn't available"):
        run("restart_server", {})                       # not offered, and refused if tried anyway


def test_chat_status_tool_never_leaks_settings(env):
    import json
    client, vm, skippy = env
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds", "valheim"]
    _, run = _tools_for(client, skippy, FRIEND)
    out = run("server_status", {})
    for secret in ("hunter22", "adm1n", "dragonpw", "owner1"):
        assert secret not in out
    status = json.loads(out)
    assert status["state"] == "online"
    assert set(status["games"]) == {"dragonwilds", "valheim"}
    assert "35.1.2.3:2456" in status["games"]["valheim"]["how_to_connect"]   # HTML stripped
    assert "<" not in status["games"]["valheim"]["how_to_connect"]


def test_chat_start_tool_uses_dashboard_guardrails(env):
    client, vm, skippy = env
    _, run = _tools_for(client, skippy, FRIEND)
    del vm.state.env["DRAGONWILDS_OWNER_ID"]
    with pytest.raises(Exception, match="Player ID"):
        run("start_game", {"games": ["dragonwilds"]})
    assert not [c for c in vm.calls if c[0] == "start"]
    vm.state.env["DRAGONWILDS_OWNER_ID"] = "owner1"
    assert "Starting RuneScape: Dragonwilds" in run("start_game", {"games": ["dragonwilds"]})
    assert vm.calls[-1] == ("start", ["dragonwilds"], 30)            # default idle, player-clamped
    with pytest.raises(Exception, match="already running"):
        run("start_game", {"games": ["valheim"]})


def test_admin_logs_page_shows_latest_snapshot_masked(env):
    client, _, _ = env
    login(client, FRIEND)
    assert client.get("/admin/logs").status_code == 403
    login(client, ADMIN)
    page = client.get("/admin/logs").data.decode()
    assert "Up 3 minutes" in page and "stale" not in page
    assert "started: dragonwilds" in page
    assert "dragonpw" not in page                               # secrets masked


def test_server_logs_tool_is_admin_only(env):
    client, _, skippy = env
    names, _ = _tools_for(client, skippy, FRIEND)
    assert "server_logs" not in names
    names, run = _tools_for(client, skippy, ADMIN)
    assert "server_logs" in names
    out = run("server_logs", {})
    assert "Up 3 minutes" in out and "dragonpw" not in out


# ── per-game pages ────────────────────────────────────────────────────────────

def test_every_game_has_a_themed_page(env):
    client, _, _ = env
    login(client, FRIEND)
    for gid, game in portal.GAMES.items():
        page = client.get(f"/games/{gid}").data.decode()
        assert game.name in page and game.theme.tagline.replace("'", "&#39;") in page
        assert game.theme.accent in page
    assert client.get("/games/nope").status_code == 404


def test_game_page_admin_section_only_for_admins(env):
    client, _, _ = env
    login(client, FRIEND)
    page = client.get("/games/dragonwilds").data.decode()
    assert "Owner Player ID" not in page and "Restart" not in page
    login(client, ADMIN)
    page = client.get("/games/dragonwilds").data.decode()
    assert "Owner Player ID" in page and "Restart &amp; update" in page
    assert "Valheim" not in page.split("<h2>Admin</h2>")[1]      # only this game's settings
    assert "adm1n" not in page and "hunter22" not in page         # secrets never rendered


def test_game_page_forms_come_back_to_the_game_page(env):
    client, vm, _ = env
    login(client, ADMIN)
    resp = client.post("/admin/settings", data={"DRAGONWILDS_SERVER_NAME": "SkippyScape",
                                                 "next": "/games/dragonwilds"})
    assert resp.headers["Location"].endswith("/games/dragonwilds")
    assert vm.state.env["DRAGONWILDS_SERVER_NAME"] == "SkippyScape"
    resp = client.post("/admin/restart", data={"next": "https://evil.example/"})
    assert resp.headers["Location"].endswith("/admin")            # off-site next ignored


def test_banner_must_be_https(env):
    client, vm, _ = env
    login(client, ADMIN)
    client.post("/admin/settings", data={"DRAGONWILDS_BANNER_URL": "javascript:alert(1)"})
    assert "DRAGONWILDS_BANNER_URL" not in vm.state.env
    client.post("/admin/settings", data={"DRAGONWILDS_BANNER_URL": "https://img.example/d.jpg"})
    assert 'src="https://img.example/d.jpg"' in client.get("/games/dragonwilds").data.decode()


def test_game_logs_are_filtered_to_that_game(env):
    client, vm, _ = env
    vm.serial[2] = ("=== skippy-diag x ===\nNAMES\tSTATUS\tPORTS\n"
                    "dragonwilds\tUp 2 minutes\t\nvalheim\tExited (1)\t\n"
                    "--- dragonwilds: last 40 log lines\nsteamcmd 42%\n"
                    "--- valheim: last 40 log lines\nvalheim noise\n=== end ===\n")
    login(client, ADMIN)
    page = client.get("/admin/logs?game=dragonwilds").data.decode()
    assert "steamcmd 42%" in page and "Up 2 minutes" in page
    assert "valheim noise" not in page and "Exited" not in page


# ── Dragonwilds invite code ───────────────────────────────────────────────────

def test_invite_code_shown_live_while_running(env):
    import json
    client, vm, skippy = env
    vm.serial[2] = "=== skippy-diag t ===\nJOIN_CODE dragonwilds=YTKM-EKL1\n=== end ===\n"
    login(client, FRIEND)
    assert "YTKM-EKL1" not in client.get("/games/dragonwilds").data.decode()   # asleep: no code
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]
    assert "YTKM-EKL1" in client.get("/games/dragonwilds").data.decode()
    assert "YTKM-EKL1" in client.get("/dashboard").data.decode()
    vm.serial[2] += "=== skippy-diag t2 ===\nJOIN_CODE dragonwilds=NEW1-CODE\n=== end ===\n"
    page = client.get("/games/dragonwilds").data.decode()                  # after a reboot
    assert "NEW1-CODE" in page and "YTKM-EKL1" not in page
    _, run = _tools_for(client, skippy, FRIEND)
    assert json.loads(run("server_status", {}))["games"]["dragonwilds"]["invite_code"] == "NEW1-CODE"


def test_invite_code_problems_never_break_the_page(env):
    client, vm, _ = env
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]

    def broken(port):
        raise RuntimeError("serial port unavailable")
    vm.serial_output = broken
    login(client, FRIEND)
    assert client.get("/games/dragonwilds").status_code == 200


def test_game_page_discord_feed(env, monkeypatch):
    import discord_admin
    client, vm, _ = env
    login(client, FRIEND)
    resp = client.get("/games/valheim/discord")
    assert resp.status_code == 404                                   # no bot token configured
    client.application.config["DISCORD_BOT_TOKEN"] = "tok"
    monkeypatch.setattr(discord_admin, "recent_messages",
                        lambda token, channel: [{"id": "1", "author": "pal", "bot": False,
                                                  "text": f"hi from {channel}", "extra": [], "at": "t"}])
    assert client.get("/games/valheim/discord").get_json()["messages"][0]["text"] == "hi from c1"
    vm.state.env["DISCORD_INVITE_VALHEIM"] = "inv1"
    page = client.get("/games/valheim").data.decode()
    assert "https://discord.gg/inv1" in page and "Join the conversation" in page


def test_invite_code_sits_with_the_join_password(env):
    client, vm, _ = env
    vm.serial[2] = "=== skippy-diag t ===\nJOIN_CODE dragonwilds=YTKM-EKL1\n=== end ===\n"
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]
    login(client, FRIEND)
    for url in ("/games/dragonwilds", "/dashboard"):
        page = client.get(url).data.decode()
        join = page[page.index('<div class="join">'):]
        join = join[:join.index("</div>")]
        assert "YTKM-EKL1" in join and "dragonpw" in join         # code + password in one block
        assert "35.1.2.3:7778" in join                            # direct ip:port for the Direct tab


# ── friends: added on the Admin page, per-game access ─────────────────────────

PAL = "pal@example.com"


def _add_friend(client, email, games):
    login(client, ADMIN)
    return client.post("/admin/friends", data={"email": email, "portals": games})


def test_friend_added_on_admin_page_can_sign_in_and_sees_only_their_games(env):
    client, vm, _ = env
    login(client, PAL)
    assert client.get("/dashboard").status_code == 302              # not invited yet → out
    _add_friend(client, " Pal@Example.com ", ["dragonwilds"])
    login(client, PAL)
    page = client.get("/dashboard").data.decode()
    assert "RuneScape: Dragonwilds" in page and "Valheim" not in page
    assert client.get("/games/dragonwilds").status_code == 200
    assert client.get("/games/valheim").status_code == 404
    assert client.get("/games/valheim/discord").status_code == 404
    client.post("/games/start", data={"games": ["valheim"]})
    assert not [c for c in vm.calls if c[0] == "start"]              # can't start a hidden game
    assert client.get("/admin").status_code == 403                   # friends aren't admins


def test_removing_a_friend_locks_them_out_next_page_load(env):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    assert client.get("/dashboard").status_code == 200
    login(client, ADMIN)
    client.post("/admin/friends/remove", data={"email": PAL})
    login(client, PAL)
    assert client.get("/dashboard").status_code == 302


def test_changing_a_friends_games_applies_immediately(env):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    _add_friend(client, PAL, ["valheim"])                             # update, not duplicate
    login(client, PAL)
    assert client.get("/games/dragonwilds").status_code == 404
    assert client.get("/games/valheim").status_code == 200


def test_friend_cannot_stop_server_running_games_they_cant_see(env):
    client, vm, _ = env
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds", "valheim"]
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    client.post("/games/stop")
    assert ("stop",) not in vm.calls
    vm.state.games = ["dragonwilds"]
    client.post("/games/stop")
    assert ("stop",) in vm.calls


def test_chat_tools_follow_friend_access(env):
    import json
    client, vm, skippy = env
    _add_friend(client, PAL, ["dragonwilds", "skippy"])
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds", "valheim"]
    names, run = _tools_for(client, skippy, PAL)
    start = next(t for t in skippy.tools if t["name"] == "start_game")
    assert start["input_schema"]["properties"]["games"]["items"]["enum"] == ["dragonwilds"]
    status = json.loads(run("server_status", {}))
    assert set(status["games"]) == {"dragonwilds"} and status["other_games_running"]
    with pytest.raises(Exception, match="access"):
        run("start_game", {"games": ["valheim"]})
    with pytest.raises(Exception, match="can't be stopped"):
        run("stop_server", {})


def test_admin_friend_list_has_invite_email(env):
    from urllib.parse import unquote
    client, _, _ = env
    resp = _add_friend(client, PAL, ["dragonwilds"])
    assert resp.headers["Location"].endswith("/admin#friends")
    page = client.get("/admin").data.decode()
    assert PAL in page and "Invite" in page
    mailto = unquote(page[page.index('href="mailto:') + 6:].split('"', 1)[0])
    assert mailto.startswith("mailto:" + PAL)
    assert "RuneScape: Dragonwilds" in mailto and "http://localhost/" in mailto


def test_core_users_and_bad_emails_are_not_added_as_friends(env):
    client, vm, _ = env
    _add_friend(client, FRIEND, ["dragonwilds"])                     # already core (deploy config)
    _add_friend(client, "not-an-email", ["dragonwilds"])
    assert "portal-friends" not in vm.metadata


def test_play_card_has_players_online_and_facts(env):
    client, vm, _ = env
    login(client, FRIEND)
    page = client.get("/games/dragonwilds").data.decode()
    play = page[page.index("<h2>Play</h2>"):page.index("on Discord</h2>")]
    assert "Players online" in play and "0 / 6" in play               # asleep: nobody on
    assert "Crossplay" in play and "At a glance" not in page          # facts merged into Play
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]
    assert "– / 6" in client.get("/games/dragonwilds").data.decode()   # count not known yet
    vm.serial[2] = "=== skippy-diag t ===\nPLAYERS dragonwilds=2\n=== end ===\n"
    assert "2 / 6" in client.get("/games/dragonwilds").data.decode()


# ── remote-operator task queue (tasks.py) ─────────────────────────────────────

OPS = {"Authorization": "Bearer ops-id-token"}


def _as_ops(monkeypatch):
    """Pretend Google verified the ops workflow's ID token for the deployer."""
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", lambda *a, **k: {
        "email": "skippy-deployer@skippy-tel.iam.gserviceaccount.com", "email_verified": True})


def test_task_endpoint_is_for_the_ops_workflow_only(env):
    client, vm, _ = env
    vm.metadata["portal-tasks"] = json.dumps([{"id": "t1", "task": "discord-setup"}])
    assert client.post("/tasks/run").status_code == 401
    assert json.loads(vm.metadata["portal-tasks"])                     # nothing ran


def test_task_endpoint_runs_only_what_was_queued(env, monkeypatch):
    import json
    import discord_admin
    client, vm, _ = env
    _as_ops(monkeypatch)
    assert client.post("/tasks/run", headers=OPS).get_json() == {"results": []}   # nothing queued
    client.application.config["DISCORD_BOT_TOKEN"] = "tok"
    monkeypatch.setattr(discord_admin, "setup",
                        lambda token, invite, games, env: {"DISCORD_GUILD_ID": "g1", "DISCORD_CHANNEL_DRAGONWILDS": "c9"})
    vm.metadata["portal-tasks"] = json.dumps([{"id": "t1", "task": "discord-setup"},
                                              {"id": "t2", "task": "rm-rf"}])
    results = {r["id"]: r for r in client.post("/tasks/run", headers=OPS).get_json()["results"]}
    assert results["t1"]["ok"] and "Discord ready" in results["t1"]["message"]
    assert not results["t2"]["ok"] and "unknown task" in results["t2"]["message"]
    assert vm.state.env["DISCORD_CHANNEL_DRAGONWILDS"] == "c9"        # settings saved to the VM
    assert json.loads(vm.metadata["portal-tasks"]) == []              # queue emptied
    assert [r["id"] for r in json.loads(vm.metadata["portal-task-results"])] == ["t1", "t2"]
    assert client.post("/tasks/run", headers=OPS).get_json() == {"results": []}   # and never re-run


def test_task_failure_is_reported_not_raised(env, monkeypatch):
    import json
    client, vm, _ = env
    _as_ops(monkeypatch)
    vm.metadata["portal-tasks"] = json.dumps([{"id": "t1", "task": "discord-setup"}])   # no bot token
    [result] = client.post("/tasks/run", headers=OPS).get_json()["results"]
    assert not result["ok"] and "bot token" in result["message"]


def test_tier_phrases_pick_the_model_for_that_message(env):
    client, _, skippy = env
    login(client, ADMIN)
    assert client.post("/chat/send", json={"message": "hi"}).get_json()["model"] is None
    assert skippy.model == SETTINGS["CHAT_MODEL"] == "claude-haiku-4-5"
    resp = client.post("/chat/send", json={"message": "Put on your Thinking Cap: why is it lagging?"})
    assert resp.get_json()["model"] == "Sonnet 5" and skippy.model == "claude-sonnet-5"
    resp = client.post("/chat/send", json={"message": "trust the awesomeness and plan the base"})
    assert resp.get_json()["model"] == "Opus 5" and skippy.model == "claude-opus-5"
    client.post("/chat/send", json={"message": "thanks"})
    assert skippy.model == SETTINGS["CHAT_MODEL"]                       # back to the default


def test_opus_tier_is_admin_only(env):
    client, _, skippy = env
    login(client, FRIEND)
    resp = client.post("/chat/send", json={"message": "trust the awesomeness"})
    assert resp.get_json()["model"] is None and skippy.model == SETTINGS["CHAT_MODEL"]
    client.post("/chat/send", json={"message": "thinking cap please"})
    assert skippy.model == "claude-sonnet-5"                            # Sonnet is for everyone


def test_skippy_is_docked_on_every_page_but_the_chat_page(env):
    client, _, skippy = env
    login(client, FRIEND)
    for url in ("/dashboard", "/games/dragonwilds"):
        assert 'id="skippy-dock"' in client.get(url).data.decode()
    assert 'id="skippy-dock"' not in client.get("/chat").data.decode()
    client.post("/chat/send", json={"message": "hi", "page": "/games/dragonwilds"})
    system, _ = skippy.seen
    assert "/games/dragonwilds" in system and "NOT the Captain" in system   # FRIEND isn't an admin


# ── portals: Skippy is its own grant ──────────────────────────────────────────

def test_skippy_is_a_grant(env):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])                         # no skippy
    login(client, PAL)
    assert 'id="skippy-dock"' not in client.get("/dashboard").data.decode()
    assert client.get("/chat").status_code == 403
    assert client.post("/chat/send", json={"message": "hi"}).status_code == 403
    _add_friend(client, PAL, ["dragonwilds", "skippy"])
    login(client, PAL)
    assert 'id="skippy-dock"' in client.get("/dashboard").data.decode()
    assert client.post("/chat/send", json={"message": "hi"}).status_code == 200


def test_pre_portal_friend_entries_keep_their_games_and_chat(env):
    import json
    client, vm, _ = env
    vm.metadata["portal-friends"] = json.dumps({PAL: {"games": ["valheim"], "added_by": ADMIN, "added": "2026-09-29"}})
    login(client, PAL)
    assert client.get("/games/valheim").status_code == 200
    assert client.post("/chat/send", json={"message": "hi"}).status_code == 200


# ── front page ────────────────────────────────────────────────────────────────

def test_front_page_shows_cards_for_granted_portals(env):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    page = client.get("/").data.decode()
    assert "RuneScape: Dragonwilds" in page and "Valheim" not in page
    assert 'class="portal skippy"' not in page and 'class="portal experiments"' not in page
    _add_friend(client, PAL, ["dragonwilds", "skippy", "experiments"])
    login(client, PAL)
    page = client.get("/").data.decode()
    assert 'class="portal skippy"' in page and 'class="portal experiments"' in page


def test_experiments_are_public(env):
    client, _, _ = env
    assert client.get("/experiments").status_code == 200              # signed out
    assert "Experiments" in client.get("/").data.decode()             # linked from sign-in


# ── Mentat physics tools in chat ──────────────────────────────────────────────

class FakeMentat:
    def __init__(self, up=True):
        self.up, self.calls = up, []

    def tools(self):
        import mentat_client
        if not self.up:
            raise mentat_client.MentatError("down")
        return [{"name": "convert_units", "domain": "Units", "description": "Convert units.",
                 "input_schema": {"type": "object", "properties": {"value": {"type": "number"}}}},
                {"name": "wind_wave_analysis", "domain": "Analysis", "description": "Wind over water.",
                 "input_schema": {"type": "object", "properties": {}}}]

    def call(self, name, args):
        self.calls.append((name, args))
        return {"value": 1.609344, "units": "kilometer", "source": "pint UnitRegistry"}

    def search(self, query, limit=12):
        return [t for t in self.tools() if query.split()[0] in t["name"]]

    def ask(self, question):
        self.calls.append(("ask", question))
        return {"value": 1.609344, "units": "km", "source": "pint UnitRegistry"}


def _chat_with(mentat, email=FRIEND):
    vm, skippy = FakeVm(), FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy, mentat=mentat)
    client = app.test_client()
    login(client, email)
    client.post("/chat/send", json={"message": "how far is a mile in km?"})
    return {t["name"] for t in skippy.tools}, skippy.run_tool


def _home_with_mentat(client, answer):
    """Pair skippy-pc, report Mentat + Qwen up, and run a fake reporter that
    answers physics jobs with `answer(question)`."""
    import threading, time
    token = _pair(client)
    report = {**json.loads(json.dumps(REPORT)), "mentat_home": True}
    client.post("/home/report", json=report, headers={"Authorization": f"Bearer {token}"})
    stop = threading.Event()

    def reporter():
        while not stop.is_set():
            for job in client.get("/home/jobs", headers={"Authorization": f"Bearer {token}"}).get_json()["jobs"]:
                client.post(f"/home/jobs/{job['id']}", json=answer(job["question"]),
                            headers={"Authorization": f"Bearer {token}"})
            time.sleep(0.01)
    threading.Thread(target=reporter, daemon=True).start()
    return stop


def test_physics_goes_to_mentat_with_qwen_at_home():
    vm, skippy = FakeVm(), FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy)
    client = app.test_client()
    asked = []
    stop = _home_with_mentat(app.test_client(), lambda q: asked.append(q) or
                             {"result": {"value": 159.6, "units": "m/s", "interpreter": "keywords + qwen"}})
    login(client, FRIEND)
    skippy.reply = lambda system, messages, tools=None, run_tool=None, **_: (
        setattr(skippy, "tools", tools) or run_tool("ask_mentat", {"question": "5 cm copper ball from 10 km?"}))
    data = client.post("/chat/send", json={"message": "/physics 5 cm copper ball from 10 km?"}).get_json()
    stop.set()
    assert asked == ["5 cm copper ball from 10 km?"]                     # passed through, not pre-solved
    assert json.loads(data["reply"])["value"] == 159.6 and data["mentat_where"] == "home"
    assert not any(t["name"].startswith("mentat_") for t in skippy.tools)  # no maths sub-tools for Haiku


def test_no_qwen_at_home_means_no_physics_answer():
    vm, skippy = FakeVm(), FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy)
    client = app.test_client()
    stop = _home_with_mentat(app.test_client(), lambda q: {"error": "Qwen (Ollama) is unreachable"})
    login(client, FRIEND)
    errors = []
    def reply(system, messages, tools=None, run_tool=None, **_):
        try:
            run_tool("ask_mentat", {"question": "escape velocity of Earth?"})
        except Exception as exc:
            errors.append(str(exc))
        return "Mentat's offline."
    skippy.reply = reply
    client.post("/chat/send", json={"message": "escape velocity of Earth?"})
    stop.set()
    assert errors and "offline" in errors[0] and "Qwen" in errors[0]


def test_direct_verbs_force_the_tool_and_badges_list_what_you_have():
    vm, skippy = FakeVm(), FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy)
    client = app.test_client()
    stop = _home_with_mentat(app.test_client(), lambda q: {"result": {"value": 1}})
    login(client, FRIEND)
    client.post("/chat/send", json={"message": "/physics how fast does a 5 cm copper ball fall 10 km?"})
    assert skippy.force_tool == "ask_mentat"
    assert skippy.seen[1][-1]["content"] == "how fast does a 5 cm copper ball fall 10 km?"   # verb stripped
    client.post("/chat/send", json={"message": "/mentat 1 mile in km"})                  # old name still works
    assert skippy.force_tool == "ask_mentat"
    client.post("/chat/send", json={"message": "/status"})
    assert skippy.force_tool == "server_status"
    client.post("/chat/send", json={"message": "is it up?"})
    assert skippy.force_tool is None
    badges = client.get("/chat/badges").get_json()
    stop.set()
    assert [b["verb"] for b in badges["Physics"]] == ["physics"]
    assert "restart" not in [b["verb"] for b in badges["Game server"]]                    # admin-only
    assert "Music" in badges                                                             # core users have Bilby
    _add_friend(client, PAL, ["dragonwilds", "skippy"])
    login(client, PAL)
    assert "Music" not in client.get("/chat/badges").get_json()                           # no Bilby grant


def test_chat_works_without_mentat():
    names, _ = _chat_with(None)
    assert "ask_mentat" not in names and "server_status" in names




# ── Experiments (public) ──────────────────────────────────────────────────────

@pytest.fixture
def lab(tmp_path, monkeypatch):
    import json
    import experiments
    (tmp_path / "benchmark").mkdir()
    (tmp_path / "radiance").mkdir()
    summaries = {"sigma_ground_scored.json": (90.7, 136), "gemini_scored.json": (62.0, 93),
                 "wolfram_merged_scored.json": (37.3, 56), "wolfram_scored.json": (20.0, 30)}
    for name, (pct, correct) in summaries.items():
        (tmp_path / "benchmark" / name).write_text(json.dumps({"summary": {
            "overall_accuracy_pct": pct, "overall_correct": correct, "overall_total": 150,
            "by_domain": {"waves_optics": {"correct": 1, "total": 2, "pct": pct}}}}))
    (tmp_path / "radiance" / "index.html").write_text("<html><body><script src='viewer.js'></script></body></html>")
    (tmp_path / "radiance" / "viewer.js").write_text("// viewer")
    monkeypatch.setattr(experiments, "ROOT", tmp_path)
    vm, skippy = FakeVm(), FakeSkippy()
    return portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy, mentat=FakeMentat()).test_client()


def test_experiments_pages_are_public_and_show_the_benchmark(lab):
    page = lab.get("/experiments").data.decode()
    assert "90.7%" in page and "Mentat" in page and "Radiance" in page
    bench = lab.get("/experiments/benchmark").data.decode()
    assert "Wolfram Alpha (single pass)" in bench and "20.0%" in bench and "waves optics" in bench
    assert lab.get("/experiments/mentat").status_code == 200


def test_radiance_served_with_chat_hidden(lab):
    page = lab.get("/experiments/radiance/").data.decode()
    assert "viewer.js" in page and "#composer" in page             # showcase snippet injected
    assert lab.get("/experiments/radiance/viewer.js").status_code == 200
    assert lab.get("/experiments/radiance/../../app.py").status_code == 404
    assert lab.post("/probe").status_code == 204


def test_playground_proxies_mentat(lab):
    assert lab.get("/experiments/mentat/api/tools").get_json()[0]["name"] == "convert_units"
    resp = lab.post("/experiments/mentat/api/call/convert_units", json={"value": 1})
    assert resp.get_json()["result"]["units"] == "kilometer"


def test_experiments_degrade_without_bundle_or_mentat(env):
    client, _, _ = env                                              # no mentat, default (empty) bundle dir
    assert client.get("/experiments").status_code == 200
    assert client.get("/experiments/mentat/api/tools").status_code == 503


# ── personal dashboard ────────────────────────────────────────────────────────

def test_dashboard_is_admin_only_and_shows_the_widgets(env):
    client, vm, _ = env
    login(client, FRIEND)
    assert client.get("/me").status_code == 403
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]
    vm.serial[2] = ("=== skippy-diag t ===\nJOIN_CODE dragonwilds=AB12-CD34\n--- memory\n"
                    "               total        used\nMem:           16002        14500\n"
                    "--- data disk\n/dev/sda         59G  50G  9G  85% /srv/games/data\n=== end ===\n")
    login(client, ADMIN)
    page = client.get("/me").data.decode()
    assert "AB12-CD34" in page and "Spend (estimate)" in page and "Friends" in page
    assert "Memory at 91%" in page and "World disk at 85%" in page      # watcher warnings
    assert 'class="portal me"' in client.get("/").data.decode()          # card on the front page


def test_watcher_flags_crashes_and_blocked_starts(env):
    client, vm, _ = env
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]
    vm.serial[2] = "=== skippy-diag t ===\ndragonwilds  Restarting (1) 5 seconds ago\n=== end ===\n"
    login(client, FRIEND)
    client.post("/games/start", data={"games": ["valheim"]})
    client.post("/games/start", data={"games": ["dragonwilds"]})       # already what's running: not a block
    login(client, ADMIN)
    page = client.get("/me").data.decode()
    assert "dragonwilds is restarting" in page
    assert "1 blocked start this week" in page and FRIEND in page


def test_hiding_an_experiment_takes_it_off_the_public_page(env):
    client, vm, _ = env
    login(client, ADMIN)
    client.post("/me/experiments", data={"slug": "radiance"})
    client.get("/logout")
    assert "Radiance" not in client.get("/experiments").data.decode()
    login(client, ADMIN)
    client.post("/me/experiments", data={"slug": "radiance"})           # toggle back
    assert "Radiance" in client.get("/experiments").data.decode()


# ── home reporter + Bilby ─────────────────────────────────────────────────────

REPORT = {
    "status": {"services": [{"name": "ollama", "port": "11434", "ok": True, "detail": "HTTP 200"},
                            {"name": "whisper", "port": "8000", "ok": False, "detail": "refused"}],
               "ollama_models": ["qwen2.5:7b"], "ups": {"on_ac": True, "charge": 97, "runtime": 41, "alert": None},
               "photo_tags": {"total": 10, "tagged": 7, "db": "ok"}, "telemetry": []},
    "services": [{"id": "bilby", "name": "Bilby", "available": True, "status_detail": None}],
    "music_session": {"mode": "dj", "status": "playing", "queue_remaining": 4, "source": "local",
                      "now_playing": {"artist": "Phone Artist", "title": "Phone Song"}},
    "bilby": {"online": True, "now_playing": {"deck": "a", "artist": "Daft Punk", "title": "Veridis Quo",
                                              "bpm": 107.0, "key": "8A", "genre": "House", "duration_s": 345,
                                              "elapsed_s": 60.0, "lyric_summary": None,
                                              "art_url": "http://skippy-mac:7334/art/x.jpg"},
              "deck_a": {"artist": "Daft Punk", "title": "Veridis Quo"}, "deck_b": None,
              "playing_a": True, "playing_b": False},
    "setlist": [{"artist": "Justice", "title": "Genesis", "played_at": "21:04"}],
    "up_next": {"artist": "Air", "title": "La Femme d'Argent", "bpm": 90},
}


def _pair(client):
    login(client, ADMIN)
    page = client.post("/me/pair-home").data.decode()
    return page.split("SKIPPY_HOME_TOKEN=")[1].split("<")[0].strip()


def test_home_report_needs_the_paired_token(env):
    import json
    client, vm, _ = env
    assert client.post("/home/report", json=REPORT).status_code == 401            # not paired
    token = _pair(client)
    assert token not in vm.metadata["portal-home"]                               # only the hash is kept
    assert client.post("/home/report", json=REPORT, headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.post("/home/report", json=json.loads(json.dumps(REPORT)), headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200 and ok.get_json() == {"commands": [], "need_art": True, "jobs": [], "fast_for_s": 0,
                                                "imports_wanted": ["everquest"]}
    page = client.get("/me").data.decode()
    assert "qwen2.5:7b" in page and "whisper" in page and "97%" in page and "Veridis Quo" in page


def test_bilby_shows_now_playing_and_carries_controls_home(env):
    import base64, json
    client, _, _ = env
    token = _pair(client)
    auth_h = {"Authorization": f"Bearer {token}"}
    report = json.loads(json.dumps(REPORT))
    report["art"] = {"key": REPORT["bilby"]["now_playing"]["art_url"], "type": "image/jpeg",
                     "b64": base64.b64encode(b"JPEGDATA").decode()}
    assert client.post("/home/report", json=report, headers=auth_h).get_json()["need_art"] is False
    state = client.get("/bilby/state.json").get_json()
    assert state["traktor"]["now_playing"]["title"] == "Veridis Quo" and state["has_art"]
    assert "art_url" not in state["traktor"]["now_playing"]                      # home hostnames stay home
    assert client.get("/bilby/art").data == b"JPEGDATA"
    assert "Veridis Quo" in client.get("/").data.decode()                        # front-page card
    client.post("/bilby/control", data={"action": "skip"})
    reply = client.post("/home/report", json=json.loads(json.dumps(REPORT)), headers=auth_h).get_json()
    assert [(c["target"], c["action"]) for c in reply["commands"]] == [("music", "skip")]
    assert client.post("/home/report", json=json.loads(json.dumps(REPORT)), headers=auth_h).get_json()["commands"] == []


# ── core health (skippy-tel-core) on the dashboard ────────────────────────────

CORE_OK = {"status": "ok", "spec_version": "1", "bridge": True, "hung_tiers": None,
           "tiers": {"claude": True, "nagatha": True, "elevenlabs": True},
           "ollama": {"available": True, "default": "qwen2.5:7b", "default_present": True}}


def _core_report(health, containers_ok=True):
    return {"health": health,
            "status": {"services": [{"name": "flask-api", "ok": True, "detail": "running"},
                                    {"name": "whisper", "ok": containers_ok, "detail": "refused"}]},
            "services": [{"id": "intent", "name": "Skippy AI", "available": True, "status_detail": "default model: qwen2.5:7b"},
                         {"id": "image", "name": "Image Generator", "available": False, "status_detail": "not deployed"}]}


def test_core_check_is_unknown_until_home_reports():
    import home as home_mod
    assert home_mod.core_check(None)["verdict"] == "unknown"
    # a reporter from before this feature sends no `health` at all: that's "can't tell", not "down"
    assert home_mod.core_check({"status": {"services": []}, "services": []})["verdict"] == "unknown"
    assert home_mod.core_check({"health": None})["verdict"] == "down"      # explicit None: the core didn't answer


def test_core_check_healthy_lists_tiers_and_every_service():
    import home as home_mod
    core = home_mod.core_check(_core_report(CORE_OK))
    assert core["verdict"] == "ok" and core["problems"] == [] and core["api_version"] == "1"
    assert [t["name"] for t in core["tiers"]] == ["ollama", "claude", "nagatha", "elevenlabs", "bridge"]
    assert all(t["ok"] for t in core["tiers"])
    assert [s["name"] for s in core["services"]] == ["Skippy AI", "Image Generator"]     # not just three of them
    assert core["services"][1]["available"] is False and core["problems"] == []          # not-deployed isn't a fault


def test_core_check_catches_a_default_model_ollama_doesnt_have():
    import copy, home as home_mod
    health = copy.deepcopy(CORE_OK)
    health["ollama"].update(default="qwen3:8b", default_present=False)
    core = home_mod.core_check(_core_report(health))
    assert core["verdict"] == "degraded"                       # /health itself still says "ok"; that's the trap
    assert any("qwen3:8b" in p and "503" in p for p in core["problems"])
    assert next(t for t in core["tiers"] if t["name"] == "ollama")["ok"] is False
    # an older core that doesn't report default_present isn't accused of anything
    del health["ollama"]["default_present"]
    assert home_mod.core_check(_core_report(health))["verdict"] == "ok"


def test_core_check_down_when_the_core_or_ollama_is_gone():
    import copy, home as home_mod
    assert home_mod.core_check(_core_report(None))["verdict"] == "down"
    no_ollama = copy.deepcopy(CORE_OK)
    no_ollama["ollama"] = {"available": False, "default": "qwen2.5:7b", "default_present": False}
    core = home_mod.core_check(_core_report(no_ollama))
    assert core["verdict"] == "down" and any("Ollama isn't reachable" in p for p in core["problems"])


def test_core_check_reports_missing_keys_and_downed_containers():
    import copy, home as home_mod
    health = copy.deepcopy(CORE_OK)
    health["tiers"]["nagatha"] = False
    health["bridge"] = False                                   # the Claude Code bridge is optional: shown, not a fault
    core = home_mod.core_check(_core_report(health, containers_ok=False))
    assert core["verdict"] == "degraded"
    assert any("Nagatha" in p for p in core["problems"]) and any("whisper is down" in p for p in core["problems"])
    assert not any("bridge" in p.lower() for p in core["problems"])


def test_core_check_trims_whatever_the_reporter_sends():
    import home as home_mod
    report = _core_report(CORE_OK)
    report["services"][0]["status_detail"] = "x" * 5000
    report["services"][0]["name"] = "n" * 500
    core = home_mod.core_check(report)
    assert len(core["services"][0]["detail"]) == 120 and len(core["services"][0]["name"]) == 40


def test_me_shows_core_health_and_the_watcher_repeats_the_problems(env):
    import copy, json
    client, _, _ = env
    token = _pair(client)
    health = copy.deepcopy(CORE_OK)
    health["ollama"].update(default="qwen3:8b", default_present=False)
    client.post("/home/report", json=_core_report(health), headers={"Authorization": f"Bearer {token}"})
    page = client.get("/me").data.decode()
    assert "Core · skippy-tel" in page and "Degraded" in page and "API v1" in page
    assert page.count("qwen3:8b") >= 2                        # in the Core widget and again in the Watcher
    assert "Core: Skippy" in page and "Image Generator" in page
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    assert client.get("/me").status_code == 403               # a friend never sees it


def test_bilby_is_a_grant_and_controls_are_admin_only(env):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    assert client.get("/bilby").status_code == 403
    _add_friend(client, PAL, ["bilby"])
    login(client, PAL)
    assert client.get("/bilby").status_code == 200
    assert client.post("/bilby/control", data={"action": "skip"}).status_code in (302, 403)
    assert client.get("/me").status_code == 403                                  # home status stays private


def test_chat_reply_says_which_tools_did_the_work(env):
    client, _, skippy = env
    login(client, ADMIN)
    data = client.post("/chat/send", json={"message": "physics?"}).get_json()
    assert data["tools"] == [] and data["mentat_available"] is False   # no Mentat in tests
    skippy.reply = lambda system, messages, tools=None, run_tool=None, model=None, **_: (
        run_tool("server_status", {}) and "Up.")
    assert client.post("/chat/send", json={"message": "up?"}).get_json()["tools"] == ["server_status"]


def test_ops_home_needs_the_deployers_google_token(env, monkeypatch):
    import auth
    client, _, _ = env
    assert client.get("/ops/home").status_code == 401
    assert client.get("/ops/home", headers={"Authorization": "Bearer junk"}).status_code == 401
    seen = {}
    def fake_verify(token, request, audience):
        seen["aud"] = audience
        return {"email": "skippy-deployer@skippy-tel.iam.gserviceaccount.com", "email_verified": True}
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", fake_verify)
    data = client.get("/ops/home", headers={"Authorization": "Bearer t"}).get_json()
    assert seen["aud"] == auth.OPS_AUDIENCE and data["paired"] is False and data["online"] is False
    assert data["core"] == {"verdict": "unknown", "problems": []}          # `ops home` sees the core's verdict too
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token",
                        lambda *a, **k: {"email": "someone@evil.example", "email_verified": True})
    assert client.get("/ops/home", headers={"Authorization": "Bearer t"}).status_code == 401


def test_everquest_database_relays_from_home_to_the_vm(env, tmp_path, monkeypatch):
    import gzip, hashlib, imports
    monkeypatch.setattr(imports, "DIR", tmp_path)
    client, vm, _ = env
    token = _pair(client)
    home_auth = {"Authorization": f"Bearer {token}"}
    dump = gzip.compress(b"CREATE DATABASE peq; -- Gonif the dwarf cleric\n" * 1000)
    sha, half = hashlib.sha256(dump).hexdigest(), len(dump) // 2
    put = lambda off, chunk, s=sha, h=home_auth: client.put(
        f"/home/imports/everquest?offset={off}&total={len(dump)}&sha256={s}", data=chunk, headers=h)

    assert put(0, dump[:half], h={}).status_code == 401                        # skippy-pc only
    assert put(0, dump[:half]).get_json() == {"have": half}
    assert put(5, dump[half:]).status_code == 409                              # wrong offset: resume point
    assert put(half, dump[half:]).get_json()["complete"] is True
    vm_token, url = vm.metadata["eq-import-token"], vm.metadata["eq-import-url"]
    assert url.endswith("/games/everquest/import") and len(vm_token) > 30

    assert client.get("/games/everquest/import").status_code == 401            # the VM's token only
    got = client.get("/games/everquest/import", headers={"Authorization": f"Bearer {vm_token}"})
    assert got.status_code == 200 and got.data == dump
    report = client.post("/home/report", json=json.loads(json.dumps(REPORT)), headers=home_auth).get_json()
    assert report["imports_wanted"] == []                                      # has it: stop asking

    assert client.post("/games/everquest/import/done",
                       headers={"Authorization": f"Bearer {vm_token}"}).get_json() == {"ok": True}
    assert vm.metadata["eq-import-token"] == "" and not list(tmp_path.iterdir())
    assert json.loads(vm.metadata["portal-imports"])["everquest"]["state"] == "done"


def test_bad_checksum_is_rejected(env, tmp_path, monkeypatch):
    import imports
    monkeypatch.setattr(imports, "DIR", tmp_path)
    client, vm, _ = env
    token = _pair(client)
    r = client.put("/home/imports/everquest?offset=0&total=3&sha256=" + "0" * 64, data=b"abc",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400 and "eq-import-token" not in vm.metadata


def test_friends_chat_is_rate_limited_but_the_captain_is_not(env):
    client, _, _ = env
    _add_friend(client, PAL, ["skippy"])
    login(client, PAL)
    codes = [client.post("/chat/send", json={"message": "hi"}).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429
    login(client, ADMIN)
    assert all(client.post("/chat/send", json={"message": "hi"}).status_code == 200 for _ in range(65))


def test_thinking_cap_has_its_own_smaller_limit(env):
    client, _, _ = env
    _add_friend(client, PAL, ["skippy"])
    login(client, PAL)
    codes = [client.post("/chat/send", json={"message": "thinking cap: why?"}).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    assert client.post("/chat/send", json={"message": "plain question"}).status_code == 200


def test_public_mentat_playground_is_rate_limited_per_visitor():
    import ratelimit
    vm, skippy = FakeVm(), FakeSkippy()
    client = portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy, mentat=FakeMentat()).test_client()
    call = lambda ip: client.post("/experiments/mentat/api/call/convert_units", json={"value": 1},
                                  headers={"X-Forwarded-For": f"6.6.6.6, {ip}"}).status_code
    assert [call("1.1.1.1") for _ in range(11)][-1] == 429           # spoofed first hop doesn't help
    assert call("2.2.2.2") == 200                                   # a different real visitor is fine


def test_radiance_only_passes_a_plain_scene_id(env):
    client, _, _ = env
    r = client.get("/experiments/radiance/?scene=<img src=x onerror=alert(1)>")
    assert r.status_code == 302 and r.location.endswith("/experiments/radiance/")
    r = client.get("/experiments/radiance/?scene=double_slit&evil=1")
    assert r.status_code == 302 and r.location.endswith("/experiments/radiance/?scene=double_slit")
    assert client.get("/experiments/radiance/?scene=double_slit").status_code != 302


def test_bilby_art_is_always_served_as_an_image(env):
    import base64
    client, _, _ = env
    token = _pair(client)
    report = json.loads(json.dumps(REPORT))
    report["art"] = {"key": REPORT["bilby"]["now_playing"]["art_url"], "type": "text/html",
                     "b64": base64.b64encode(b"<script>alert(1)</script>").decode()}
    client.post("/home/report", json=report, headers={"Authorization": f"Bearer {token}"})
    r = client.get("/bilby/art")
    assert r.mimetype == "image/jpeg" and r.headers["X-Content-Type-Options"] == "nosniff"
