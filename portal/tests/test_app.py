"""Flask app tests with a fake VM and a fake Claude. Need the real Flask +
Authlib (installed in CI); skipped where they aren't installed."""
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

    def reply(self, system, messages, tools=None, run_tool=None):
        self.seen = (system, messages)
        self.tools, self.run_tool = tools, run_tool
        return "Obviously."


SETTINGS = {
    "SECRET_KEY": "test", "SESSION_COOKIE_SECURE": False, "SESSION_COOKIE_SAMESITE": "Lax",
    "GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret",
    "ALLOWED_EMAILS": frozenset({ADMIN, FRIEND}), "ADMIN_EMAILS": frozenset({ADMIN}),
    "GCP_PROJECT": "p", "GCP_ZONE": "z", "GCP_INSTANCE": "i",
    "CHAT_MODEL": "claude-opus-5", "CHAT_ENABLED": True,
    "DISCORD_INVITE": "gPTVS7Hmj", "DISCORD_BOT_TOKEN": "",
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
    assert vm.calls[-1] == ("start", ["valheim"], 45)
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
    assert resp.get_json() == {"reply": "Obviously."}
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
    assert names == {"server_status", "start_game", "stop_server"}
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
    return client.post("/admin/friends", data={"email": email, "games": games})


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
    _add_friend(client, PAL, ["dragonwilds"])
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
