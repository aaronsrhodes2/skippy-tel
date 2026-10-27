"""Flask app tests with a fake VM and a fake Claude. Need the real Flask +
Authlib (installed in CI); skipped where they aren't installed."""
import html
import json
import re

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

    def restart(self, games=None):
        self.calls.append(("restart",) if games is None else ("restart", tuple(games)))
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
    assert client.get("/games").status_code == 302
    assert client.get("/health").data == b"ok"


def test_portal_is_all_cards_with_no_top_tabs(env):
    client, _, _ = env
    login(client, ADMIN)
    launcher = client.get("/apps").data.decode()
    assert "<nav" not in launcher and 'id="home-btn"' not in launcher          # the launcher is home: nowhere to go "back" to
    assert 'aria-label="Sign out"' in launcher                                 # the account lives on the home screen (a glyph key)
    for path in ("/games", "/play", "/me", "/admin", "/music", "/experiments"):   # everywhere the old tabs led is now a tile
        assert f'href="{path}"' in launcher, path
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    friend = client.get("/apps").data.decode()
    assert 'href="/admin"' not in friend and 'href="/me"' not in friend         # admin tiles stay admin-only


def test_every_app_page_has_a_back_button_a_home_button_and_its_own_title(env):
    client, _, _ = env
    login(client, ADMIN)
    page = client.get("/games").data.decode()
    assert "<nav" not in page
    assert 'aria-label="Back"' in page and 'aria-label="Home"' in page         # a bridge page: back and home are glyph keys
    assert 'aria-label="Sign out"' not in page                                 # the strip is just back, what this is, home
    admin = client.get("/admin").data.decode()
    assert 'aria-label="Back"' in admin and 'aria-label="Home"' in admin and '<body class="bridge"' in admin    # Admin is on the bridge too


def test_the_launcher_has_one_tile_per_app_and_no_duplicate_paths(env):
    import re
    client, _, _ = env
    login(client, ADMIN)
    launcher = client.get("/apps").data.decode()
    assert re.findall(r'class="unit(?: named)?" data-app="(\w+)"', launcher) == \
        ["skippy", "games", "play", "music", "video", "experiments", "tabletop", "car", "dashboard", "admin"]
    assert launcher.count('href="/games"') == 1 and 'href="/games/' not in launcher      # games live in one app, not six tiles
    assert "Server room" not in launcher and "<nav" not in launcher
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    assert re.findall(r'class="unit(?: named)?" data-app="(\w+)"', client.get("/apps").data.decode()) == ["games"]   # each tile comes with its own grant (test_grants.py)


def test_the_old_dashboard_path_redirects_to_games(env):
    client, _, _ = env
    resp = client.get("/dashboard")
    assert resp.status_code == 301 and resp.location.endswith("/games")
    login(client, ADMIN)
    assert client.get("/dashboard", follow_redirects=True).status_code == 200


def test_mesh_only_links_are_for_the_captain_not_the_friends_pages(env):
    client, _, _ = env
    login(client, FRIEND)
    assert "skippy-pc:8096" not in client.get("/games").data.decode()              # a friend can't reach the mesh
    login(client, ADMIN)
    assert "Home links" in client.get("/me").data.decode() and "skippy-pc:8096" in client.get("/me").data.decode()


def test_every_page_belongs_to_an_app_with_a_colour():
    """The design rule, enforced: pages extend the one base, say which app they are, and that app has an accent."""
    import re
    from pathlib import Path
    templates = Path(__file__).resolve().parents[1] / "templates"
    base = (templates / "base.html").read_text(encoding="utf-8")
    colours = set(re.findall(r'\[data-app="(\w+)"\]\s*\{\s*--accent', base))
    exempt = {"home.html", "login.html", "unauthorized.html"}         # the launcher and the signed-out pages
    paper = {"tabletop_print.html"}                                    # made for a printer: black on white, no portal chrome
    for t in sorted(templates.glob("*.html")):
        text = t.read_text(encoding="utf-8")
        if t.name.startswith("_") or t.name == "base.html" or t.name in paper:
            continue
        assert 'extends "base.html"' in text, f"{t.name} doesn't use the one base template"
        if t.name in exempt:
            continue
        found = re.search(r"\{% block app %\}(\w+)\{% endblock %\}", text)
        assert found, f"{t.name} doesn't say which app it belongs to"
        assert found.group(1) in colours, f"{t.name}: app {found.group(1)!r} has no accent in base.html"


def test_unlisted_email_is_bounced(env):
    client, _, _ = env
    login(client, "stranger@example.com")
    resp = client.get("/games")
    assert resp.status_code == 302 and "/unauthorized" in resp.location


def test_dashboard_shows_valheim_with_discord_links(env):
    client, _, _ = env
    login(client, FRIEND)
    page = client.get("/games/valheim").data.decode()               # the game's own page carries the Discord links
    assert "Valheim" in page
    assert "https://discord.com/channels/g1/c1" in page
    assert "https://discord.com/channels/g1/v1" in page
    assert "Admin" not in page            # friends don't get the admin link
    assert "discord.com/channels" not in client.get("/games").data.decode()     # the list is just cards


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
    assert vm.calls[-1] == ("restart",)                                         # the Admin page: the saved list


def test_a_game_pages_restart_brings_back_that_game_only(env):
    client, vm, _ = env
    login(client, ADMIN)
    page = client.get("/games/dragonwilds").data.decode()
    assert '<input type="hidden" name="game" value="dragonwilds">' in page and 'aria-label="Update &amp; play ' in page
    client.post("/admin/restart", data={"game": "dragonwilds", "next": "/games/dragonwilds"})
    assert vm.calls[-1] == ("restart", ("dragonwilds",))
    assert client.post("/admin/restart", data={"game": "minecraft"}).status_code == 400


def test_chat_round_trip(env):
    client, _, skippy = env
    login(client, ADMIN)
    resp = client.post("/chat/send", json={"message": "status?",
                                           "history": [{"role": "user", "content": "hi"},
                                                       {"role": "assistant", "content": "yo"}]})
    assert resp.get_json() == {"reply": "Obviously.", "tools": [], "mentat_available": False, "mentat_where": None,
                               "media": [], "brain": "core", "mentat": [], "barney": False}
    system, messages = skippy.seen
    assert "Captain" in system
    assert messages[-1] == {"role": "user", "content": "status?"}


def test_chat_errors_are_shown_not_raised(env):
    client, _, skippy = env
    login(client, ADMIN)

    def boom(system, messages, **_):
        raise chat.ChatError("Skippy is rate-limited right now.")
    skippy.reply = boom
    resp = client.post("/chat/send", json={"message": "hi"})
    assert resp.status_code == 502
    assert "rate-limited" in resp.get_json()["error"]


def test_friend_one_click_play_the_current_game_with_guardrail(env):
    client, vm, _ = env
    login(client, FRIEND)
    vm.state.games = ["dragonwilds"]                               # the game the Captain has set on the server
    page = client.get("/games/dragonwilds").data.decode()          # Play is on the game's page
    assert "Play RuneScape: Dragonwilds" in page
    client.post("/games/start", data={"games": ["dragonwilds"], "idle_minutes": "0"})
    assert vm.calls[-1] == ("start", ["dragonwilds"], 15)          # "never sleep" is admin-only


def test_friends_cannot_start_a_different_game_and_are_told_to_ask_aaron(env):
    client, vm, _ = env
    login(client, FRIEND)                                          # the server is set to Valheim, asleep
    assert 'aria-label="Play Valheim"' in client.get("/games/valheim").data.decode()
    page = client.get("/games").data.decode()                      # the list says so on the control (a dark one), and offers no multi-start
    assert 'href="/games/dragonwilds"' in page and 'data-s="off" data-tel="tile:game:dragonwilds"' in page
    assert 'data-open="multi"' not in page and 'aria-label="Play ' not in page
    body = client.post("/games/start", data={"games": ["dragonwilds"]}, follow_redirects=True).data.decode()
    assert "Valheim is the game on the server right now" in body
    assert "To play RuneScape: Dragonwilds instead, ask Aaron to switch the server" in body
    assert not [c for c in vm.calls if c[0] == "start"]
    blocked = json.loads(vm.metadata["portal-watch"])["blocked_starts"]      # the Captain's watcher hears about it
    assert [(b["who"], b["wanted"], b["running"]) for b in blocked] == [(FRIEND, ["dragonwilds"], ["valheim"])]
    game_page = client.get("/games/dragonwilds").data.decode()
    assert 'aria-label="Play RuneScape' not in game_page and "ask Aaron" in game_page
    vm.state.status = "RUNNING"                                    # the same rule while a game is up
    assert 'data-s="off" data-tel="tile:game:dragonwilds"' in client.get("/games").data.decode()


def test_the_captain_can_start_any_game_and_several_at_once(env):
    client, vm, _ = env
    login(client, ADMIN)
    assert "Start several games at once" in client.get("/games").data.decode()
    assert "Play RuneScape: Dragonwilds" in client.get("/games/dragonwilds").data.decode()
    client.post("/games/start", data={"games": ["dragonwilds", "valheim"], "idle_minutes": "45"})
    assert vm.calls[-1] == ("start", ["dragonwilds", "valheim"], 45)


def test_the_switch_message_never_names_a_game_the_friend_cant_see(env):
    client, vm, _ = env
    _add_friend(client, PAL, ["dragonwilds"])                      # PAL can't see Valheim, which is set
    login(client, PAL)
    body = client.post("/games/start", data={"games": ["dragonwilds"]}, follow_redirects=True).data.decode()
    assert "Valheim" not in body and "ask Aaron to switch the server" in body
    assert not [c for c in vm.calls if c[0] == "start"]


def test_with_no_game_set_a_friend_may_pick_one(env):
    client, vm, _ = env
    vm.state.games = []                                            # first ever start: nothing to switch away from
    login(client, FRIEND)
    client.post("/games/start", data={"games": ["valheim"]})
    assert vm.calls[-1] == ("start", ["valheim"], 30)


def test_the_games_tile_shows_which_game_server_is_up(env):
    import re
    client, vm, _ = env
    login(client, FRIEND)
    def games_unit(page):
        start = re.search(r'<div class="unit(?: named)?" data-app="games"', page).start()
        return page[start:page.index("</div>", start)]

    tile = games_unit(client.get("/apps").data.decode())
    assert 'data-s="idle"' in tile and "Valheim" not in tile                   # asleep: a ring lamp, no name (nothing is up)
    vm.state.status = "RUNNING"
    tile = games_unit(client.get("/apps").data.decode())
    assert 'data-s="on"' in tile and ">Valheim</a>" in tile                    # up: a lit lamp and the name of what is up
    _add_friend(client, PAL, ["dragonwilds"])                      # a game PAL can't see is up: it is lit, but never named
    login(client, PAL)
    tile = games_unit(client.get("/apps").data.decode())
    assert 'data-s="on"' in tile and "Valheim" not in tile


def _anchors(page: str):
    """Every <a> in the page with its href, and whether it sits inside another <a> (invalid HTML)."""
    from html.parser import HTMLParser

    class Scan(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth, self.found, self.nested = 0, [], []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                href = dict(attrs).get("href")
                self.found.append(href)
                if self.depth:
                    self.nested.append(href)
                self.depth += 1

        def handle_endtag(self, tag):
            if tag == "a":
                self.depth = max(0, self.depth - 1)
    scan = Scan()
    scan.feed(page)
    return scan.found, scan.nested


def test_the_live_game_badge_links_straight_to_that_games_page(env):
    client, vm, _ = env
    login(client, FRIEND)
    vm.state.status = "RUNNING"
    page = client.get("/apps").data.decode()
    links, nested = _anchors(page)
    assert "/games/valheim" in links and "/games" in links            # the badge goes to the game; the tile still opens the app
    assert nested == []                                               # and no link sits inside another link
    names = page[page.index('<span class="names">'):page.index("</span>", page.index('<span class="names">'))]
    assert 'href="/games/valheim"' in names and 'title="Valheim"' in names and 'data-tel="tile:games:valheim"' in names
    assert 'class="hit" href="/games" aria-label="Game servers"' in page         # tapping anywhere else opens the Games app


def test_the_badge_links_every_live_game_and_never_a_hidden_or_sleeping_one(env):
    client, vm, _ = env
    login(client, ADMIN)
    vm.state.status, vm.state.games = "RUNNING", ["valheim", "dragonwilds"]
    links, nested = _anchors(client.get("/apps").data.decode())
    assert {"/games/valheim", "/games/dragonwilds"} <= set(links) and nested == []
    vm.state.status = "TERMINATED"                                    # asleep: nothing is live, so nothing to link to
    links, _ = _anchors(client.get("/apps").data.decode())
    assert "/games/valheim" not in links and "/games/dragonwilds" not in links and "/games" in links
    vm.state.status = "RUNNING"
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)                                                # PAL can see Dragonwilds only
    links, _ = _anchors(client.get("/apps").data.decode())
    assert "/games/dragonwilds" in links and "/games/valheim" not in links


def test_the_games_page_is_a_grid_of_whole_card_links_like_the_launcher(env):
    import re
    from catalog import GAMES
    client, _, _ = env
    login(client, ADMIN)
    page = client.get("/games").data.decode()
    links, nested = _anchors(page)
    assert nested == []
    assert re.findall(r'<div class="unit named" data-s="\w+" data-tel="tile:game:(\w+)"', page) == list(GAMES)   # one control per game, in order
    assert page.count('class="deck"') == 1
    for gid, game in GAMES.items():
        assert f"/games/{gid}" in links and f'href="#g-{game.glyph}"' in page.replace("<use ", "<use ") and f"--accent: {game.theme.accent}" in page
        name = game.name.replace("'", "&#39;")
        assert f'aria-label="{name}"' in page and f'<span class="name">{name}</span>' in page   # its name is the one word on it
        assert game.theme.tagline.replace("'", "&#39;") in page                       # just the tagline (a tooltip), not the full description
        assert game.description.replace("'", "&#39;") not in page
    assert "fonts.googleapis.com" not in page                                          # and no third-party request on this hub


def test_the_running_games_card_is_the_highlighted_one(env):
    import re
    client, vm, _ = env
    login(client, ADMIN)
    lit = lambda page: re.findall(r'data-s="(?:on|boot)" data-tel="tile:game:(\w+)"', page)   # noqa: E731 (up, or coming up)
    assert lit(client.get("/games").data.decode()) == []                                 # asleep: nothing is lit
    vm.state.status, vm.state.games = "RUNNING", ["valheim"]
    page = client.get("/games").data.decode()
    assert lit(page) == ["valheim"]                                                      # only the game that's up glows
    assert len(re.findall(r'data-s="(?:idle|off)" data-tel="tile:game:', page)) == len(portal.GAMES) - 1 and 'data-live="/games/valheim/live"' in page
    vm.state.games = ["valheim", "dragonwilds"]                                          # the Captain started two
    page = client.get("/games").data.decode()
    assert sorted(lit(page)) == ["dragonwilds", "valheim"]
    _add_friend(client, PAL, ["factorio"])
    login(client, PAL)                                                                   # a game PAL can't see is up: nothing lit
    assert lit(client.get("/games").data.decode()) == []


def test_a_games_card_only_says_how_that_game_is_doing(env):
    import re
    client, vm, _ = env
    login(client, FRIEND)
    page = client.get("/games").data.decode()
    assert 'data-s="idle" data-tel="tile:game:valheim"' in page                          # asleep: a ring lamp
    vm.state.status = "RUNNING"
    page = client.get("/games").data.decode()
    assert re.search(r'data-s="(?:on|boot)" data-tel="tile:game:valheim"', page)         # up: lit
    for secret in ("hunter22", "Invite Code", "Play Valheim", "Show join password"):
        assert secret not in page                                                      # details are for the game's own page


def test_the_games_page_lists_only_the_games_you_can_see_and_never_reads_the_vm_for_join_codes(env):
    client, vm, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]

    def refuse(port):
        raise AssertionError("the list page must not read the VM's serial output")
    vm.serial_output = refuse
    page = client.get("/games").data.decode()
    assert 'href="/games/dragonwilds"' in page and 'href="/games/valheim"' not in page


def test_join_password_shown_to_players_only_while_running(env):
    client, vm, _ = env
    login(client, FRIEND)
    assert "dragonpw" not in client.get("/games/dragonwilds").data.decode()   # asleep: nothing to join
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]
    assert "dragonpw" not in client.get("/games").data.decode()    # the list of games never carries the password
    page = client.get("/games/dragonwilds").data.decode()
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
    """The tools the chat would hand this person's model. The Captain's chat is the general Skippy and reaches the model; a visitor's is Mentat
    and Barney Style only (no tools reach any model), so theirs are read straight from the app's tool builder, the one the Captain's uses."""
    login(client, email)
    if email == ADMIN:
        client.post("/chat/send", json={"message": "is it up?"})
    else:
        with client.application.test_request_context("/"):
            tools = client.application.extensions["chat_tools_for"]({"email": email})
        skippy.tools, skippy.run_tool = tools.schemas(), tools.run
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
    vm.state.games = ["dragonwilds"]                                 # the game set on the server
    del vm.state.env["DRAGONWILDS_OWNER_ID"]
    with pytest.raises(Exception, match="Player ID"):
        run("start_game", {"games": ["dragonwilds"]})
    assert not [c for c in vm.calls if c[0] == "start"]
    vm.state.env["DRAGONWILDS_OWNER_ID"] = "owner1"
    assert "Starting RuneScape: Dragonwilds" in run("start_game", {"games": ["dragonwilds"]})
    assert vm.calls[-1] == ("start", ["dragonwilds"], 30)            # default idle, player-clamped
    with pytest.raises(Exception, match="ask Aaron"):                # a different game: only the Captain switches it
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
        assert game.name.replace("'", "&#39;") in page and game.theme.tagline.replace("'", "&#39;") in page
        assert game.theme.accent in page
    assert client.get("/games/nope").status_code == 404


def test_game_page_admin_section_only_for_admins(env):
    client, _, _ = env
    login(client, FRIEND)
    page = client.get("/games/dragonwilds").data.decode()
    assert "Owner Player ID" not in page and "Restart" not in page
    login(client, ADMIN)
    page = client.get("/games/dragonwilds").data.decode()
    assert "Owner Player ID" in page and "Update &amp; play" in page
    assert "Valheim" not in page.split('data-pane="admin"')[1].split('<div class="pips"')[0]      # only this game's settings
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
    assert "YTKM-EKL1" not in client.get("/games").data.decode()     # the list doesn't show it (or read the VM for it)
    vm.serial[2] +="=== skippy-diag t2 ===\nJOIN_CODE dragonwilds=NEW1-CODE\n=== end ===\n"
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
    page = client.get("/games/dragonwilds").data.decode()
    join = page[page.index('<div class="join">'):]
    join = join[:join.index("</div>")]
    assert "YTKM-EKL1" in join and "dragonpw" in join             # code + password in one block
    assert "35.1.2.3:7778" in join                                # direct ip:port for the Direct tab
    listing = client.get("/games").data.decode()
    assert '<div class="join">' not in listing and "YTKM-EKL1" not in listing and "dragonpw" not in listing


def _join_block(client, gid):
    page = client.get(f"/games/{gid}").data.decode()
    join = page[page.index('<div class="join">'):]
    return join[:join.index("</div>")]


def test_valheim_without_crossplay_gives_the_address_and_no_code(env):
    """The default: the IP works, and there is no join code to show (the server makes one only with crossplay on)."""
    client, vm, _ = env
    vm.serial[2] = "=== skippy-diag t ===\nJOIN_CODE valheim=106969\n=== end ===\n"     # even a stale code line isn't shown
    vm.state.status = "RUNNING"                                                     # the server is set to Valheim
    login(client, FRIEND)
    join = _join_block(client, "valheim")
    assert "35.1.2.3:2456" in join and "hunter22" in join and "Add server" in join
    assert "106969" not in join and "Invite code" not in join and 'id="join-code"' not in join
    assert "Show join password" not in join and "<details" not in join                  # no click to reveal it: shown on purpose
    assert "Or search the list" not in join                                             # Valheim has no list to search


def test_valheim_with_crossplay_leads_with_its_join_code_and_drops_the_address_that_no_longer_works(env):
    client, vm, _ = env
    vm.serial[2] = "=== skippy-diag t ===\nJOIN_CODE valheim=106969\n=== end ===\n"
    vm.state.status = "RUNNING"
    vm.state.env["VALHEIM_CROSSPLAY"] = "true"
    login(client, FRIEND)
    join = _join_block(client, "valheim")
    assert join.index("106969") < join.index("hunter22")                                # code, then password
    assert "35.1.2.3" not in join and "2456" not in join                                # crossplay servers don't listen on 2456
    assert "Add server" in join and "paste the code" in join
    vm.serial[2] = "=== skippy-diag t2 ===\nJOIN_CODE valheim=222333\n=== end ===\n"    # a reboot makes a new session and code
    assert "222333" in _join_block(client, "valheim") and "106969" not in _join_block(client, "valheim")
    assert client.get("/games/valheim/live").get_json()["code"] == "222333"


def test_every_game_says_what_to_type_and_where(env):
    from catalog import GAMES  # noqa: PLC0415
    client, vm, _ = env
    login(client, ADMIN)
    vm.state.status = "RUNNING"
    for gid in GAMES:
        vm.state.games = [gid]
        join = _join_block(client, gid)
        assert "35.1.2.3" in join and 'class="code"' in join and 'class="help"' in join, gid    # an address to copy, and a hint
    vm.state.games = ["factorio"]
    assert "Connect to address" in _join_block(client, "factorio")
    vm.state.games = ["satisfactory"]
    assert "Add Server" in _join_block(client, "satisfactory") and "7777" in _join_block(client, "satisfactory")
    vm.state.games = ["everquest"]
    import html  # noqa: PLC0415
    page = html.unescape(client.get("/games/everquest").data.decode())                          # what the browser copies
    assert '<pre class="copy">[LoginServer]\nHost=35.1.2.3:5999\nHost1=</pre>' in page             # RoF2's format (the Titanium braces one is thrown away)
    assert "[Registration Servers]" not in page.split('<pre class="copy">')[1].split("</pre>")[0]
    assert "click to copy" in page and '<script src="/static/copy.js" defer></script>' in page     # the copy behaviour ships on every page


def test_the_game_page_keeps_itself_current(env):
    client, vm, _ = env
    login(client, FRIEND)
    vm.serial[2] = "=== skippy-diag t ===\nJOIN_CODE dragonwilds=YTKM-EKL1\n=== end ===\n"
    live = client.get("/games/dragonwilds/live")
    assert live.get_json() == {"on": False, "starting": False, "code": None, "players": None}    # asleep: nothing to show
    assert live.headers["Cache-Control"] == "no-store"
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]
    assert client.get("/games/dragonwilds/live").get_json()["code"] == "YTKM-EKL1"
    vm.serial[2] += "=== skippy-diag t2 ===\nJOIN_CODE dragonwilds=NEW1-CODE\n=== end ===\n"
    assert client.get("/games/dragonwilds/live").get_json()["code"] == "NEW1-CODE"              # picked up without a reload
    page = client.get("/games/dragonwilds").data.decode()
    assert "/games/dragonwilds/live" in page and 'id="join-code"' in page and "location.reload()" in page
    vm.state.games = ["valheim"]                                                                # another game is up: not "on" for this one
    assert client.get("/games/dragonwilds/live").get_json()["on"] is False


def test_the_live_endpoint_follows_the_game_pages_access_rules(env):
    client, vm, _ = env
    assert client.get("/games/valheim/live").status_code == 302                     # signed out
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    assert client.get("/games/valheim/live").status_code == 404                     # no access to that game
    assert client.get("/games/dragonwilds/live").status_code == 200
    vm.status = lambda: (_ for _ in ()).throw(RuntimeError("compute API down"))
    assert client.get("/games/dragonwilds/live").get_json()["on"] is False           # a VM error reads as "not up", never a 500


# ── friends: added on the Admin page, per-game access ─────────────────────────

PAL = "pal@example.com"


def _add_friend(client, email, games):
    login(client, ADMIN)
    return client.post("/admin/friends", data={"email": email, "portals": games})


def test_friend_added_on_admin_page_can_sign_in_and_sees_only_their_games(env):
    client, vm, _ = env
    login(client, PAL)
    assert client.get("/games").status_code == 302              # not invited yet → out
    _add_friend(client, " Pal@Example.com ", ["dragonwilds"])
    login(client, PAL)
    page = client.get("/games").data.decode()
    assert "RuneScape: Dragonwilds" in page and "Valheim" not in page
    assert client.get("/games/dragonwilds").status_code == 200
    ask = client.get("/games/valheim")                                # a real game they can't see: offered a way to ask
    assert ask.status_code == 302 and ask.location.endswith("/request-access?game=valheim")
    assert client.get("/games/no-such-game").status_code == 404
    assert client.get("/games/valheim/discord").status_code == 404
    client.post("/games/start", data={"games": ["valheim"]})
    assert not [c for c in vm.calls if c[0] == "start"]              # can't start a hidden game
    assert client.get("/admin").status_code == 403                   # friends aren't admins


def test_removing_a_friend_locks_them_out_next_page_load(env):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    assert client.get("/games").status_code == 200
    login(client, ADMIN)
    client.post("/admin/friends/remove", data={"email": PAL})
    login(client, PAL)
    assert client.get("/games").status_code == 302


def test_changing_a_friends_games_applies_immediately(env):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    _add_friend(client, PAL, ["valheim"])                             # update, not duplicate
    login(client, PAL)
    assert client.get("/games/dragonwilds").location.endswith("/request-access?game=dragonwilds")   # taken away: ask again
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
    play = page[page.index('data-pane="play"'):page.index('data-pane="talk"')]
    assert "<b>0</b> / 6" in play                                      # asleep: nobody on (a person glyph, then the count)
    assert "Crossplay" in page and "At a glance" not in page          # the facts have their own page
    vm.state.status, vm.state.games = "RUNNING", ["dragonwilds"]
    assert "<b>–</b> / 6" in client.get("/games/dragonwilds").data.decode()   # count not known yet
    vm.serial[2] = "=== skippy-diag t ===\nPLAYERS dragonwilds=2\n=== end ===\n"
    assert "<b>2</b> / 6" in client.get("/games/dragonwilds").data.decode()


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
    seen = {}

    def fake_setup(token, invite, games, env, portal_url=None):
        seen["portal_url"] = portal_url                                   # the pins link to where the portal is served
        return {"DISCORD_GUILD_ID": "g1", "DISCORD_CHANNEL_DRAGONWILDS": "c9"}
    monkeypatch.setattr(discord_admin, "setup", fake_setup)
    vm.metadata["portal-tasks"] = json.dumps([{"id": "t1", "task": "discord-setup"},
                                              {"id": "t2", "task": "rm-rf"}])
    results = {r["id"]: r for r in client.post("/tasks/run", headers=OPS).get_json()["results"]}
    assert results["t1"]["ok"] and "Discord ready" in results["t1"]["message"]
    assert not results["t2"]["ok"] and "unknown task" in results["t2"]["message"]
    assert vm.state.env["DISCORD_CHANNEL_DRAGONWILDS"] == "c9"        # settings saved to the VM
    assert seen["portal_url"] == "http://localhost/"
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


def test_chat_send_is_skippy_core_for_everyone_and_barney_style_too(env):
    client, _, skippy = env
    login(client, ADMIN)
    for phrase in ("hi", "trust the awesomeness", "Put on your Thinking Cap", "use sonnet", "model: claude-opus-5", "solid gold"):
        resp = client.post("/chat/send", json={"message": phrase, "model": "claude-opus-5"})        # words and a "model" field pick nothing
        assert resp.status_code == 200 and resp.get_json()["brain"] == "core" and skippy.model == "chat", phrase
    for mode in ("public", "core"):
        assert client.post("/chat/send", json={"message": "hi", "mode": mode}).get_json()["brain"] == "core"
    for asked in ({"brain": "opus"}, {"brain": "gpt-9"}, {"mode": "private"}, {"mode": "auto"}):
        assert client.post("/chat/send", json={"message": "hi", **asked}).status_code == 403, asked   # Private/Auto are the agent's
    resp = client.post("/chat/send", json={"message": "explain", "barney": True})                    # Barney Style is Skippy Core
    assert resp.get_json()["brain"] == "core" and skippy.model == "chat" and skippy.tools is None and "Barney Style" in skippy.seen[0]
    login(client, FRIEND)
    resp = client.post("/chat/send", json={"message": "hi"})                                        # a guest: Skippy Core
    assert resp.status_code == 200 and resp.get_json()["brain"] == "core" and skippy.model == "chat"
    for brain in ("haiku", "sonnet", "opus", "gemini", "gemini-free", "private", "auto"):
        assert client.post("/chat/send", json={"message": "hi", "brain": brain}).status_code == 403, brain
    resp = client.post("/chat/send", json={"message": "Give it to me Barney Style"})                 # Barney Style: no tools at all
    assert resp.status_code == 200 and skippy.tools is None and skippy.force_tool is None and "Barney Style" in skippy.seen[0]


def test_only_the_captain_sees_the_mode_key(env):
    client, _, _ = env
    login(client, ADMIN)
    page = client.get("/chat").data.decode()
    assert 'data-sheet="brain"' in page
    for mode in ("public", "private", "auto"):
        assert f'data-mode="{mode}"' in page, mode
    assert 'data-brain="opus"' not in page and "gemini" not in page.lower().replace("g-gemini", "")
    login(client, FRIEND)
    page = client.get("/chat").data.decode()
    assert 'data-sheet="brain"' not in page and 'data-mode=' not in page and "claude-opus" not in page


def test_there_is_one_chat_interface_and_it_is_the_skippy_app(env):
    client, _, skippy = env
    login(client, FRIEND)
    for url in ("/games", "/games/dragonwilds", "/experiments", "/"):
        page = client.get(url).data.decode()
        assert 'id="skippy-dock"' not in page and 'class="skippy-chat"' not in page, url   # no second chat UI anywhere
    assert client.get("/chat").data.decode().count('class="skippy-chat chat-bridge"') == 1
    client.post("/chat/send", json={"message": "Barney Style please", "page": "/games/dragonwilds"})   # a visitor reaches Haiku only this way
    system, _ = skippy.seen
    assert "/games/dragonwilds" in system and "NOT the Captain" in system   # FRIEND isn't an admin


# ── portals: Skippy is its own grant ──────────────────────────────────────────

def test_skippy_is_a_grant(env):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])                         # no skippy
    login(client, PAL)
    assert 'data-tel="tile:skippy"' not in client.get("/apps").data.decode()     # no Skippy tile without the grant
    assert client.get("/chat").status_code in (302, 403)                   # and the Skippy app is closed to them
    assert client.post("/chat/send", json={"message": "hi"}).status_code == 403
    _add_friend(client, PAL, ["dragonwilds", "skippy"])
    login(client, PAL)
    assert 'data-tel="tile:skippy"' in client.get("/apps").data.decode()
    assert client.get("/chat").status_code == 200                          # the Skippy app
    assert client.post("/chat/send", json={"message": "hi"}).status_code in (200, 503)   # a visitor's is Mentat's (503: none home in this test)


def test_pre_portal_friend_entries_keep_their_games_and_chat(env):
    import json
    client, vm, _ = env
    vm.metadata["portal-friends"] = json.dumps({PAL: {"games": ["valheim"], "added_by": ADMIN, "added": "2026-09-29"}})
    login(client, PAL)
    assert client.get("/games/valheim").status_code == 200
    assert client.post("/chat/send", json={"message": "hi"}).status_code != 403


# ── front page ────────────────────────────────────────────────────────────────

def test_front_page_shows_cards_for_granted_portals(env):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    page = client.get("/apps").data.decode()
    assert "RuneScape: Dragonwilds" in page and "Valheim" not in page
    assert 'data-tel="tile:skippy"' not in page and 'data-tel="tile:experiments"' not in page
    _add_friend(client, PAL, ["dragonwilds", "skippy", "experiments"])
    login(client, PAL)
    page = client.get("/apps").data.decode()
    assert 'data-tel="tile:skippy"' in page and 'data-tel="tile:experiments"' in page


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

    # Weapon vs. Armor (the real shapes come from mentat/wva.py; mentat/tests checks them against the game)
    def wva_catalog(self):
        import mentat_client
        if not self.up:
            raise mentat_client.MentatError("Mentat is unreachable (connection refused)")
        return WVA_CATALOG

    def wva_solve(self, fight):
        import mentat_client
        self.calls.append(("wva_solve", fight))
        if not self.up:
            raise mentat_client.MentatError("Mentat is unreachable (connection refused)")
        if fight.get("environment") not in {"vacuum", "atmosphere_earth"}:
            raise mentat_client.MentatError("environment must be one of: vacuum, atmosphere_earth", 400)
        return {**WVA_RESULT, "inputs": fight}


WVA_CATALOG = {"sigma_ground_ref": "a763071b4beb87129f2672624e989a39ef7a1199",
               "types": {"kinetic": {"label": "Kinetic", "available": True, "params": []}},
               "weapons": [{"key": "ak47", "name": "AK-47 (7.62x39mm)", "type": "kinetic",
                            "weapon": {"type": "kinetic", "mass_kg": 0.0079, "velocity_ms": 715.0,
                                       "radius_m": 0.0038, "material": "steel_mild"}}],
               "armors": [{"key": "nij_iv", "name": "NIJ Level IV Plate (SAPI)",
                           "layers": [{"material": "ceramic_alumina", "thickness_m": 0.015}]}],
               "materials": [], "environments": [{"key": "vacuum", "label": "Vacuum (space)", "about": ""}],
               "limits": {"layers_max": 10, "thickness_m": [0.001, 2.0], "distance_m": [1.0, 10000.0]},
               "defaults": {"weapon": "ak47", "armor": "nij_iv", "environment": "vacuum", "distance_m": 100.0}}
WVA_RESULT = {"verdict": "ARMOR HOLDS — first layer stopped it", "breached": False, "layers_penetrated": 0,
              "total_layers": 1, "weapon_type": "kinetic", "environment": "atmosphere_earth",
              "summary_lines": ["Layer 1 [ceramic_alumina 15.0mm]: Poncelet, stopped at 0.005m, T=3191K"],
              "layers": [{"material": "ceramic_alumina", "gap": False, "thickness_m": 0.015, "penetrated": False,
                          "damage_depth_m": 0.0045, "damage_fraction": 0.3, "energy_deposited_j": 0.0,
                          "temperature_k": 3191.0, "notes": "Poncelet, stopped at 0.005m, T=3191K",
                          "color": "#c85014"}],
              "clamped": [], "sigma_ground_ref": "a763071b4beb87129f2672624e989a39ef7a1199"}
FIGHT = {"weapon": WVA_CATALOG["weapons"][0]["weapon"], "armor_layers": WVA_CATALOG["armors"][0]["layers"],
         "environment": "atmosphere_earth", "distance_m": 100}


def _chat_with(mentat, email=ADMIN):
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
    login(client, ADMIN)
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
    login(client, ADMIN)
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
    login(client, ADMIN)
    client.post("/chat/send", json={"message": "/physics how fast does a 5 cm copper ball fall 10 km?"})
    assert skippy.force_tool == "ask_mentat"
    assert skippy.seen[1][-1]["content"] == "how fast does a 5 cm copper ball fall 10 km?"   # verb stripped
    client.post("/chat/send", json={"message": "/mentat 1 mile in km"})                  # old name still works
    assert skippy.force_tool == "ask_mentat"
    client.post("/chat/send", json={"message": "/status"})
    assert skippy.force_tool == "server_status"
    client.post("/chat/send", json={"message": "is it up?"})
    assert skippy.force_tool is None
    login(client, FRIEND)
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
    assert "AB12-CD34" in page and "Spend (estimate)" not in page and "Friends" in page
    assert "Memory at 91%" in page and "World disk at 85%" in page      # watcher warnings
    assert 'data-tel="tile:dashboard"' in client.get("/apps").data.decode()   # tile on the front page


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


def test_the_home_report_keeps_what_bilby_plays_and_carries_queued_controls_home(env):
    """The old /bilby page is gone, but its data is not: /me's widget and the chat's now_playing tool read home.music(), and the
    reporter still asks for the cover and collects queued commands. (Nothing in the portal queues a music command any more.)"""
    import base64, json
    client, _, _ = env
    token = _pair(client)
    home = client.application.extensions["home"]
    auth_h = {"Authorization": f"Bearer {token}"}
    report = json.loads(json.dumps(REPORT))
    report["art"] = {"key": REPORT["bilby"]["now_playing"]["art_url"], "type": "image/jpeg",
                     "b64": base64.b64encode(b"JPEGDATA").decode()}
    assert client.post("/home/report", json=report, headers=auth_h).get_json()["need_art"] is False
    state = home.music()
    assert state["traktor"]["now_playing"]["title"] == "Veridis Quo" and state["has_art"]
    assert "art_url" not in state["traktor"]["now_playing"]                      # home hostnames stay home
    assert "Veridis Quo" in client.get("/me").data.decode()                      # the dashboard's now-playing line
    assert "Veridis Quo" not in client.get("/").data.decode()                    # and no card for it on the launcher
    assert home.queue("music", "skip")
    reply = client.post("/home/report", json=json.loads(json.dumps(REPORT)), headers=auth_h).get_json()
    assert [(c["target"], c["action"]) for c in reply["commands"]] == [("music", "skip")]
    assert client.post("/home/report", json=json.loads(json.dumps(REPORT)), headers=auth_h).get_json()["commands"] == []


def test_the_old_bilby_page_is_gone_and_its_addresses_lead_to_the_audio_service(env):
    client, _, _ = env
    for path in ("/bilby", "/bilby/", "/bilby/state.json", "/bilby/art", "/bilby/anything/else"):
        r = client.get(path)
        assert r.status_code == 301 and r.location.endswith("/music"), path          # signed out too: /music asks for the sign-in
    assert client.post("/bilby/control", data={"action": "skip"}).status_code == 405   # nothing steers a phone session any more
    login(client, ADMIN)
    assert 'data-app="bilby"' not in client.get("/apps").data.decode()
    assert client.get("/music").status_code == 200
    assert 'data-tel="tile:music"' in client.get("/apps").data.decode()


# ── core health (skippy-tel-core) on the dashboard ────────────────────────────

CORE_OK = {"status": "ok", "spec_version": "1", "bridge": True, "hung_tiers": None,
           "tiers": {"nagatha": True, "elevenlabs": True},
           "ollama": {"available": True, "default": "qwen2.5:7b", "default_present": True}}


def _core_report(health, containers_ok=True):
    return {"health": health,
            "status": {"services": [{"name": "flask-api", "ok": True, "detail": "running"},
                                    {"name": "whisper", "ok": containers_ok, "detail": "refused"}]},
            "services": [{"id": "intent", "name": "Skippy AI", "available": True, "status_detail": "default model: qwen2.5:7b"},
                         {"id": "image", "name": "Image Generator", "available": False, "status_detail": "not deployed"}]}


def test_core_check_carries_planned_so_a_not_set_up_row_is_not_an_outage():
    import home as home_mod
    got = home_mod.core_check({"health": {"status": "ok"}, "services": [
        {"id": "ups", "name": "UPS Monitor", "available": False, "planned": True, "status_detail": "not set up"},
        {"id": "x", "name": "X", "available": False, "status_detail": "down"}]})
    assert [s["planned"] for s in got["services"]] == [True, False]


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
    assert [t["name"] for t in core["tiers"]] == ["ollama", "nagatha", "elevenlabs", "bridge"]
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


def test_the_audio_grant_opens_bilbys_audio_service_but_never_home_status(env):
    """The "audio" grant (the old "bilby" one reads as it: test_grants.py) opens the player and the chat's now_playing tool.
    Home status stays private."""
    client, _, _ = env
    _add_friend(client, PAL, ["audio"])
    login(client, PAL)
    assert client.get("/music").status_code == 200
    assert client.get("/bilby").status_code == 301                               # a redirect gives nothing away
    assert b'data-tel="tile:music"' in client.get("/apps").data
    assert client.get("/video").status_code == 403 and client.get("/me").status_code == 403


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
    got.close()                                       # send_file streams from an open file: release it, or Windows can't delete it below
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
    codes = [client.post("/chat/send", json={"message": "hi"}).status_code for _ in range(61)]     # a visitor's messages are Mentat's (503: none home here)
    assert 429 not in codes[:60] and codes[60] == 429
    login(client, ADMIN)
    assert all(client.post("/chat/send", json={"message": "hi"}).status_code == 200 for _ in range(65))


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


def test_the_cover_the_reporter_sends_is_kept_but_nothing_serves_it_any_more(env):
    """/bilby/art (which forced an image type so a hostile cover could never be HTML) is removed with the page. The cover is still
    held (home.current_art) until the reporter stops sending it; if a route ever serves it again, it must force an image type."""
    import base64
    client, _, _ = env
    token = _pair(client)
    report = json.loads(json.dumps(REPORT))
    report["art"] = {"key": REPORT["bilby"]["now_playing"]["art_url"], "type": "text/html",
                     "b64": base64.b64encode(b"<script>alert(1)</script>").decode()}
    client.post("/home/report", json=report, headers={"Authorization": f"Bearer {token}"})
    assert client.application.extensions["home"].current_art() is not None
    assert client.get("/bilby/art").status_code == 301 and client.get("/bilby/art").location.endswith("/music")


# ── local capabilities in the portal chat: music made on skippy-pc ────────────

def _advertise(client, token, **caps):
    """skippy-pc's report with its capabilities, as the home reporter sends it."""
    body = {"status": {}, "services": [],
            "capabilities": {name: {"ready": ready} for name, ready in caps.items()}}
    return client.post("/home/report", json=body, headers={"Authorization": f"Bearer {token}"})


def _reporter(app, token, handler):
    """Play skippy-pc's home reporter: poll the portal for jobs and answer them, on a thread."""
    import threading, time
    stop = threading.Event()
    client, headers = app.test_client(), {"Authorization": f"Bearer {token}"}

    def loop():
        while not stop.is_set():
            for job in client.get("/home/jobs", headers=headers).get_json()["jobs"]:
                handler(client, headers, job)
            time.sleep(0.02)
    threading.Thread(target=loop, daemon=True).start()
    return stop


def _music_app():
    skippy = FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy)
    client = app.test_client()
    token = _pair(client)                                            # logs in as the admin
    _advertise(client, token, make_music=True)
    app.extensions["home"].hold = None                               # (songs are patient jobs: test_patient.py; here, the music flow itself)
    return app, client, token, skippy


def _tool_names(skippy):
    return [t["name"] for t in skippy.tools]


def test_only_known_capabilities_count_and_only_while_home_is_reporting():
    import home as home_mod
    now = [1000.0]
    h = home_mod.Home(vm=None, clock=lambda: now[0])
    assert h.capabilities() == {} and h.can("make_music") is False                     # no report yet
    h.accept({"capabilities": {"make_music": {"ready": True}, "format_c_drive": {"ready": True}}})
    import popups
    assert h.capabilities() == {"make_music": True, "make_image": False, "make_image_hq": False, "workshop": False, "music_gate": False,
                                "video_gate": False, "voice_gate": False, "house": False, "car": False,
                                **{name: False for name in popups.load()}}   # unknown names are ignored (registered pop-ups count: popups.py)
    assert h.can("make_music") and not h.can("format_c_drive")
    h.accept({"capabilities": {"make_music": {"ready": "yes"}}})
    assert h.can("make_music") is False                                                # only a real True counts
    h.accept({"capabilities": "lots"})
    assert h.capabilities() == {}                                                      # malformed: nothing
    h.accept({"capabilities": {"make_music": {"ready": True}}})
    now[0] += home_mod.STALE_AFTER + 1
    assert h.capabilities() == {}                                                      # a silent home offers nothing


def test_a_long_job_can_be_started_looked_at_and_finished_without_being_consumed():
    import home as home_mod
    now = [1000.0]
    h = home_mod.Home(vm=None, clock=lambda: now[0])
    job = h.start("make_music", owner="aaron@example.com", prompt="lofi")
    assert h.owner_of(job) == "aaron@example.com" and h.owner_of("nope") is None
    assert h.peek(job) == {"state": "running", "age_s": 0, "handed_out": False} and h.peek("nope") == {"state": "unknown"}
    assert h.wait_result(job, 0)["state"] == "running"
    assert h.finish(job, {"result": {"seconds": 30}})
    assert h.peek(job) == h.peek(job) == {"state": "done", "result": {"seconds": 30}}   # peeking doesn't consume it
    failed = h.start("make_music", prompt="x")
    h.finish(failed, {"error": "GPU out of memory"})
    assert h.peek(failed) == {"state": "error", "error": "GPU out of memory"}
    assert h.owner_of(failed) == ""


def test_music_is_offered_to_the_admin_only_and_only_while_home_says_it_can(env):
    client, _, skippy = env
    token = _pair(client)
    for who, ready, expected in ((ADMIN, False, False), (ADMIN, True, True), (FRIEND, True, False)):
        _advertise(client, token, make_music=ready)
        login(client, who)
        if who == ADMIN:
            client.post("/chat/send", json={"message": "hi"})
        else:                                             # a visitor's chat reaches no model; ask the tool builder
            with client.application.test_request_context("/"):
                skippy.tools = client.application.extensions["chat_tools_for"]({"email": who}).schemas()
        names = _tool_names(skippy)
        assert ("make_music" in names and "music_result" in names) is expected, (who, ready)


def test_the_admin_prompt_knows_about_music_and_a_friends_does_not():
    assert "Music is made at home" in chat.system_prompt("Aaron", is_admin=True)
    assert "Music is made at home" not in chat.system_prompt("Pal", is_admin=False)


def test_admin_makes_music_end_to_end_and_hears_it():
    app, client, token, skippy = _music_app()
    jobs = []

    def skippy_pc(c, headers, job):
        jobs.append(job)
        c.put(f"/home/media/{job['id']}?title=lofi", data=b"ID3fake-mp3-bytes",
              headers={**headers, "Content-Type": "audio/mpeg"})
        c.post(f"/home/jobs/{job['id']}", json={"result": {"title": job["prompt"], "seconds": 30}}, headers=headers)

    stop = _reporter(app, token, skippy_pc)
    try:
        skippy.reply = lambda system, messages, tools=None, run_tool=None, **_: (
            run_tool("make_music", {"prompt": "  dreamy lofi beat, rain, 80 bpm  ", "lyrics": "", "duration_s": 5000}) and "Here you go.")
        data = client.post("/chat/send", json={"message": "make me a lofi beat"}).get_json()
    finally:
        stop.set()
    assert data["reply"] == "Here you go." and data["tools"] == ["make_music"]
    assert len(jobs) == 1 and jobs[0]["kind"] == "make_music"
    assert jobs[0]["prompt"] == "dreamy lofi beat, rain, 80 bpm" and jobs[0]["duration_s"] == 120   # trimmed, clamped
    (clip,) = data["media"]
    assert clip["kind"] == "audio" and clip["url"] == f"/chat/media/{jobs[0]['id']}" and clip["seconds"] == 30
    played = client.get(clip["url"])
    assert played.status_code == 200 and played.data == b"ID3fake-mp3-bytes" and played.mimetype == "audio/mpeg"
    assert played.headers["X-Content-Type-Options"] == "nosniff" and "no-store" in played.headers["Cache-Control"]
    part = client.get(clip["url"], headers={"Range": "bytes=0-2"})                 # so the player can seek
    assert part.status_code == 206 and part.data == b"ID3"


def test_a_clip_is_private_to_whoever_asked_for_it_and_admins(env):
    app, client, token, skippy = _music_app()

    def skippy_pc(c, headers, job):
        c.put(f"/home/media/{job['id']}", data=b"ID3x", headers={**headers, "Content-Type": "audio/mpeg"})
        c.post(f"/home/jobs/{job['id']}", json={"result": {"seconds": 5}}, headers=headers)

    stop = _reporter(app, token, skippy_pc)
    try:
        skippy.reply = lambda system, messages, tools=None, run_tool=None, **_: run_tool("make_music", {"prompt": "x"}) and "ok"
        url = client.post("/chat/send", json={"message": "music"}).get_json()["media"][0]["url"]
    finally:
        stop.set()
    friend = app.test_client()
    login(friend, FRIEND)
    assert friend.get(url).status_code == 404                                      # not 403: it isn't confirmed to exist
    assert app.test_client().get(url).status_code == 302                           # signed out: to the login page
    assert client.get(url).status_code == 200


def test_the_media_upload_is_only_for_skippy_pc_and_only_for_a_job_we_are_waiting_on():
    app, client, token, skippy = _music_app()
    home = app.extensions["home"]
    job = home.start("make_music", owner=ADMIN, prompt="x")
    auth_h = {"Authorization": f"Bearer {token}", "Content-Type": "audio/mpeg"}
    url = f"/home/media/{job}"
    assert client.put(url, data=b"ID3", headers={"Content-Type": "audio/mpeg"}).status_code == 401        # no token
    assert client.put(url, data=b"ID3", headers={**auth_h, "Authorization": "Bearer nope"}).status_code == 401
    assert client.put("/home/media/nosuchjob", data=b"ID3", headers=auth_h).status_code == 404            # nobody's waiting
    assert client.put(url, data=b"<script>", headers={**auth_h, "Content-Type": "text/html"}).status_code == 415
    assert client.put(url, data=b"", headers=auth_h).status_code == 400
    assert client.put(url, data=b"ID3", headers=auth_h).status_code == 200


def test_the_upload_size_cap_is_enforced_by_the_server(monkeypatch):
    import media
    monkeypatch.setattr(media, "MAX_ONE", 10)
    app, client, token, _ = _music_app()
    job = app.extensions["home"].start("make_music", owner=ADMIN, prompt="x")
    resp = client.put(f"/home/media/{job}", data=b"x" * 50,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "audio/mpeg"})
    assert resp.status_code == 413


def test_a_song_that_isnt_ready_says_so_and_music_result_finds_it_later(monkeypatch):
    import chat_tools
    monkeypatch.setattr(chat_tools, "MUSIC_WAIT_S", 0.05)                          # don't hold the chat hostage
    app, client, token, skippy = _music_app()
    home = app.extensions["home"]
    seen = {}

    def first(system, messages, tools=None, run_tool=None, **_):
        seen["first"] = run_tool("make_music", {"prompt": "slow blues"})
        return "It's cooking."
    skippy.reply = first
    data = client.post("/chat/send", json={"message": "a slow blues"}).get_json()
    started = json.loads(seen["first"])
    assert started["status"] == "queued" and started["job_id"] and [m["pending"] for m in data["media"]] == [True]
    job_id = started["job_id"]
    home.finish(job_id, {"result": {"title": "slow blues", "seconds": 42}})         # skippy-pc finishes, later
    client.put(f"/home/media/{job_id}", data=b"ID3late",
               headers={"Authorization": f"Bearer {token}", "Content-Type": "audio/mpeg"})

    skippy.reply = lambda system, messages, tools=None, run_tool=None, **_: (
        seen.update(second=run_tool("music_result", {"job_id": job_id})) or "Ready.")
    data = client.post("/chat/send", json={"message": "is it done?"}).get_json()
    assert json.loads(seen["second"])["status"] == "done"
    assert data["media"][0]["url"] == f"/chat/media/{job_id}" and client.get(data["media"][0]["url"]).data == b"ID3late"


def test_a_failed_song_is_reported_plainly_and_a_gone_one_is_not_a_crash():
    import chat_tools
    app, client, token, skippy = _music_app()
    home = app.extensions["home"]
    out = {}

    def ask(system, messages, tools=None, run_tool=None, **_):
        for label, args, name in (("fail", {"prompt": "x"}, "make_music"), ("gone", {"job_id": "deadbeef"}, "music_result"),
                                  ("empty", {"prompt": "  "}, "make_music")):
            try:
                out[label] = run_tool(name, args)
            except chat_tools.ToolError as exc:
                out[label] = "ERR " + str(exc)
        return "ok"
    skippy.reply = ask
    stop = _reporter(app, token, lambda c, h, job: c.post(f"/home/jobs/{job['id']}", json={"error": "GPU out of memory"}, headers=h))
    try:
        client.post("/chat/send", json={"message": "music"})
    finally:
        stop.set()
    assert out["fail"].startswith("ERR Music failed: GPU out of memory")
    assert out["gone"].startswith("ERR That music job is gone")
    assert out["empty"].startswith("ERR make_music needs a style description")


def test_the_music_verb_with_nothing_after_it_asks_instead_of_forcing_an_empty_tool_call():
    app, client, token, skippy = _music_app()
    client.post("/chat/send", json={"message": "/music"})
    assert skippy.force_tool is None and "what kind of music" in skippy.seen[1][-1]["content"]
    client.post("/chat/send", json={"message": "/music a sea shanty"})
    assert skippy.force_tool == "make_music" and skippy.seen[1][-1]["content"] == "a sea shanty"


# ── Experiments: one inventory of every simulation and demonstration ──────────

DEMO_PAGES = ("/experiments/double-slit", "/experiments/nbody", "/experiments/mentat", "/experiments/benchmark")


def test_the_inventory_highlights_the_demos_and_lists_everything_else(env):
    import re
    client, _, _ = env
    page = client.get("/experiments").data.decode()                             # public: signed out
    featured = re.findall(r'class="tile featured"[^>]*data-tel="demo:([\w-]+)"', page)
    assert featured == ["nbody", "double-slit"]
    for title in ("N-Body Shootout", "Double-Slit", "Mentat", "Radiance"):
        assert title in page, title
    assert "Weapon vs. Armor" not in page                                         # it moved to Browser Games
    assert 'id="find"' in page and 'aria-label="Filter by kind"' in page       # search + kind filters
    assert json.loads(html.unescape(re.search(r"data-kinds='([^']*)'", page).group(1))).__len__() == 4   # the four kinds that exist (the key steps All, then these)
    assert 'id="kind-key"' in page
    import experiments
    assert len(re.findall(r'<a class="tile( featured)?"', page)) == len(experiments.EXPERIMENTS)   # every experiment is one tile: no duplicates
    assert "Coming next" not in page                                              # the three it promised are here now


def test_every_demo_is_public_and_is_a_bridge_page_that_goes_back_to_the_inventory(env):
    import re
    client, _, _ = env
    for url in DEMO_PAGES:
        page = client.get(url).data.decode()
        assert '<body class="bridge"' in page and 'data-app="experiments"' in page, url   # the bridge shell, in the experiments' accent
        assert 'aria-label="Back"' in page and 'href="/experiments"' in page, url         # back goes to the inventory
        assert 'aria-label="Sign in"' in page, url                                       # signed out: the strip says how to sign in
        assert '<header class="appbar">' not in page and 'id="home-btn"' not in page, url   # none of the old pages' chrome
        assert not re.search(r'<(script|link|img)[^>]+(src|href)="https?://', page), url   # no third-party requests
    login(client, ADMIN)
    signed_in = client.get("/experiments/double-slit").data.decode()
    assert 'aria-label="Home"' in signed_in and 'aria-label="Sign in"' not in signed_in   # and the way home when signed in


def test_the_double_slit_demo_keeps_its_simulation_and_its_controls(env):
    client, _, _ = env
    page = client.get("/experiments/double-slit").data.decode()
    for needle in ('id="scatter"', 'id="theory-overlay"', 'id="histogram"', 'id="d-sl"', 'id="btn-play"',
                   "requestAnimationFrame", "D² + V² ≤ 1"):
        assert needle in page, needle
    assert "{% raw %}" not in page and "{{" not in page.split("<script>")[0].split("<main")[1].replace("{{ ", "")   # rendered, not leaked


def test_the_nbody_page_shows_the_scoreboard_including_what_doesnt_flatter_it(env):
    client, _, _ = env
    page = client.get("/experiments/nbody").data.decode()
    assert "ΔE/E = 1.5×10⁻⁶" in page and "ΔL/L = 4×10⁻¹⁵" in page and "+178° (alive!)" in page
    assert "±23° drift (needs J2)" in page and "Missing physics" in page              # the drift is in the table on purpose
    assert "worst is Mercury at 74%" in page


# ── Weapon vs. Armor: played in the browser, computed by sigma-ground's solver on Mentat ──

def _wva(mentat=None):
    vm, skippy = FakeVm(), FakeSkippy()
    return portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy, mentat=mentat).test_client()


def test_weapon_vs_armor_is_playable_in_the_browser():
    import experiments
    page = _wva(FakeMentat()).get("/experiments/weapon-vs-armor").data.decode()
    for needle in ('<canvas id="stage"', 'id="fire"', 'id="w-preset"', 'id="w-params"', 'id="a-preset"', 'id="layers"',
                   'id="add-layer"', 'id="add-gap"', 'id="env"', 'id="dist"', 'id="results"', "requestAnimationFrame",
                   'data-catalog="/experiments/weapon-vs-armor/api/catalog"',
                   'data-solve="/experiments/weapon-vs-armor/api/solve"', 'data-available="1"', "No hit points"):
        assert needle in page, needle
    assert "isn't built yet" not in page and "pip install" not in page                     # a game, not a download
    assert "{% raw %}" not in page and "{{" not in page.split("<script>")[0]                 # rendered, not leaked
    assert '<body data-app="play">' in page and "&lsaquo; Browser Games</a>" in page   # it lives in Browser Games now
    assert '<h1 class="apptitle">Weapon vs. Armor</h1>' in page
    assert not any(e.slug == "weapon-vs-armor" for e in experiments.EXPERIMENTS)


def test_browser_games_lists_weapon_vs_armor_star_control_zero_orion_trail_doom_loop_and_shifting_prophesy(env):
    import re
    client, _, _ = env
    page = client.get("/play").data.decode()                                     # public, like the experiments
    assert '<body class="bridge" data-app="play">' in page and "Browser Games" not in page.split("<main")[1]   # glyphs, not a title
    assert re.findall(r'data-tel="play:([\w-]+)"', page) == ["weapon-vs-armor", "star-control-zero", "orion-trail", "doom-loop", "shifting-prophesy"]
    assert 'class="hit" href="/experiments/weapon-vs-armor"' in page
    assert re.search(r'data-s="off" data-tel="play:doom-loop"', page) and 'title="Doom Loop"' in page      # no build yet: dark, no link
    assert "Star Control Zero: The Precursors" in page                                    # a game's name is a label, so it is text
    assert re.search(r'data-s="off" data-tel="play:star-control-zero"', page)           # no link to nowhere
    assert 'aria-label="Sign in"' in page                                                # signed out: the way in, as a key
    login(client, ADMIN)
    signed_in = client.get("/play").data.decode()
    assert 'aria-label="Home"' in signed_in and 'aria-label="Back"' in signed_in


def test_weapon_vs_armor_numbers_only_ever_come_from_mentat():
    """The page draws; it has no physics of its own and no numbers to fall back on."""
    page = _wva(FakeMentat()).get("/experiments/weapon-vs-armor").data.decode()
    script = page.split("<script>")[1]
    for physics in ("Poncelet", "Birkhoff", "yield_stress", "Beer", "Bethe", "Math.exp(", "hit points"):
        assert physics not in script, physics
    assert "never makes numbers up" in script and "No result was made up" in script


def test_the_weapon_vs_armor_api_proxies_mentat():
    mentat = FakeMentat()
    client = _wva(mentat)
    catalog = client.get("/experiments/weapon-vs-armor/api/catalog").get_json()
    assert catalog["weapons"][0]["key"] == "ak47" and catalog["limits"]["layers_max"] == 10
    resp = client.post("/experiments/weapon-vs-armor/api/solve", json=FIGHT)
    assert resp.status_code == 200
    result = resp.get_json()["result"]
    assert result["verdict"].startswith("ARMOR HOLDS") and result["layers"][0]["temperature_k"] == 3191.0
    assert mentat.calls == [("wva_solve", FIGHT)]                                          # passed on as sent


def test_the_weapon_vs_armor_api_refuses_bad_fights_before_or_with_mentat():
    mentat = FakeMentat()
    client = _wva(mentat)
    solve = "/experiments/weapon-vs-armor/api/solve"
    for bad in ([1, 2], "fire!", None):
        assert client.post(solve, json=bad).status_code == 400
    huge = {**FIGHT, "pad": "x" * 9000}
    assert client.post(solve, json=huge).status_code == 413
    assert mentat.calls == []                                                              # none of those reached Mentat
    refused = client.post(solve, json={**FIGHT, "environment": "mars"})
    assert refused.status_code == 400 and "environment must be one of" in refused.get_json()["error"]
    assert "result" not in refused.get_json()


def test_weapon_vs_armor_says_plainly_when_mentat_is_offline_and_never_fakes_a_result():
    import mentat_client
    down = _wva(FakeMentat(up=False))
    for resp in (down.get("/experiments/weapon-vs-armor/api/catalog"),
                 down.post("/experiments/weapon-vs-armor/api/solve", json=FIGHT)):
        body = resp.get_json()
        assert resp.status_code == 502 and body["offline"] is True and "result" not in body
        assert body["error"] == "Mentat is offline right now."                           # no raw socket errors
    missing = _wva(None)                                                                   # no Mentat deployed at all
    page = missing.get("/experiments/weapon-vs-armor").data.decode()
    assert 'data-available=""' in page and "Mentat isn't deployed yet." in page
    for resp in (missing.get("/experiments/weapon-vs-armor/api/catalog"),
                 missing.post("/experiments/weapon-vs-armor/api/solve", json=FIGHT)):
        assert resp.status_code == 503 and resp.get_json()["offline"] is True

    class OldMentat(FakeMentat):                                                           # deployed before the game existed
        def wva_catalog(self):
            raise mentat_client.MentatError("Mentat answered 404", 404)
    old = _wva(OldMentat()).get("/experiments/weapon-vs-armor/api/catalog")
    assert old.status_code == 502 and "doesn't have Weapon vs. Armor yet" in old.get_json()["error"]

    class GameWontLoad(FakeMentat):                                                        # Mentat up, game broken at this commit
        def wva_solve(self, fight):
            raise mentat_client.MentatError("Weapon vs. Armor didn't load at this sigma-ground commit: boom", 503)
    broken = _wva(GameWontLoad()).post("/experiments/weapon-vs-armor/api/solve", json=FIGHT)
    assert broken.status_code == 502 and broken.get_json()["error"] == \
        "Mentat couldn't run it: Weapon vs. Armor didn't load at this sigma-ground commit: boom."


def test_weapon_vs_armor_is_rate_limited_per_visitor_from_the_playgrounds_budget():
    client = _wva(FakeMentat())
    fire = lambda ip: client.post("/experiments/weapon-vs-armor/api/solve", json=FIGHT,
                                  headers={"X-Forwarded-For": f"6.6.6.6, {ip}"}).status_code
    assert [fire("1.1.1.1") for _ in range(11)] == [200] * 10 + [429]                     # spoofed first hop doesn't help
    assert fire("2.2.2.2") == 200                                                           # a different real visitor is fine
    for _ in range(5):                                                                     # one budget for all public Mentat calls
        client.post("/experiments/mentat/api/call/convert_units", json={"value": 1},
                    headers={"X-Forwarded-For": "3.3.3.3"})
    assert [fire("3.3.3.3") for _ in range(6)][-1] == 429
    assert client.get("/experiments/weapon-vs-armor/api/catalog",                           # the catalog is cached: free
                      headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200


def test_the_result_figures_are_served_as_static_files(env):
    client, _, _ = env
    svg = client.get("/static/demos/nbody_results.svg")
    assert svg.status_code == 200 and svg.mimetype == "image/svg+xml" and b"Scoreboard" in svg.data
    assert client.get("/static/demos/nbody_errors.png").status_code == 200


def test_kinds_only_appear_as_filters_when_something_has_them(env):
    import re
    client, vm, _ = env
    login(client, ADMIN)
    for slug in ("mentat", "benchmark", "radiance"):                                # hide the tool, results and recorded ones
        client.post("/me/experiments", data={"slug": slug})
    client.get("/logout")
    page = client.get("/experiments").data.decode()
    kinds = json.loads(html.unescape(re.search(r"data-kinds='([^']*)'", page).group(1)))
    assert "tool" not in kinds and "live" in kinds                                  # no "Tools" kind to step to with nothing behind it
    assert "Live in your browser" in page


def test_every_experiment_has_a_known_kind_and_a_page_that_exists(env):
    import experiments
    client, _, _ = env
    for x in experiments.EXPERIMENTS:
        assert x.kind in experiments.KINDS, x.slug
        assert x.endpoint, x.slug
    assert sum(x.featured for x in experiments.EXPERIMENTS) == 2


# ── admin: set the games up automatically ─────────────────────────────────────

def test_the_admin_can_set_every_game_up_automatically_with_one_join_password(env):
    client, vm, _ = env
    login(client, ADMIN)
    resp = client.post("/admin/autoconfigure", data={"join_password": "groggy"}, follow_redirects=True)
    page = resp.data.decode()
    (call,) = [c for c in vm.calls if c[0] == "update_env"]
    assert call[1]["VALHEIM_PASSWORD"] == "groggy" and call[1]["DRAGONWILDS_WORLD_PASSWORD"] == "groggy"
    assert "Set up automatically" in page and "groggy" not in page.split("<form")[0]   # said in words; the value isn't in the message
    assert "Restart the server to apply them" in page


def test_setting_up_automatically_with_no_password_only_fills_blanks(env):
    client, vm, _ = env
    login(client, ADMIN)
    vm.state.env.pop("VALHEIM_PASSWORD")
    client.post("/admin/autoconfigure", data={"join_password": ""})
    (call,) = [c for c in vm.calls if c[0] == "update_env"]
    assert "VALHEIM_PASSWORD" in call[1] and len(call[1]["VALHEIM_PASSWORD"]) >= 5
    assert "DRAGONWILDS_WORLD_PASSWORD" not in call[1]                                 # already set: untouched


def test_a_bad_join_password_saves_nothing_and_says_why(env):
    client, vm, _ = env
    login(client, ADMIN)
    page = client.post("/admin/autoconfigure", data={"join_password": "no"}, follow_redirects=True).data.decode()
    assert "at least 5" in page and not [c for c in vm.calls if c[0] == "update_env"]


def test_only_admins_can_set_the_games_up(env):
    client, vm, _ = env
    login(client, FRIEND)
    assert client.post("/admin/autoconfigure", data={"join_password": "groggy"}).status_code == 403
    assert not [c for c in vm.calls if c[0] == "update_env"]


def test_the_admin_page_says_what_is_missing_and_offers_the_button(env):
    client, vm, _ = env
    login(client, ADMIN)
    vm.state.env.pop("VALHEIM_PASSWORD")
    page = client.get("/admin").data.decode()
    assert "Set up automatically" in page and "Valheim needs a join password" in page
    vm.state.env["VALHEIM_PASSWORD"] = "hunter22"
    assert "Every game can start" in client.get("/admin").data.decode()


def test_ops_configure_previews_by_default_and_writes_only_on_apply(env, capsys):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import remote
    _, vm, _ = env
    vm.state.env.pop("VALHEIM_PASSWORD")
    assert remote.configure(vm, [], dict(vm.state.env)) == 0
    assert "PREVIEW" in capsys.readouterr().out and not [c for c in vm.calls if c[0] == "update_env"]   # nothing written
    assert remote.configure(vm, ["apply", "groggy"], dict(vm.state.env)) == 0
    out = capsys.readouterr().out
    assert "APPLIED" in out and "groggy" not in out.replace("::add-mask::groggy", "")                 # keys, never values
    (call,) = [c for c in vm.calls if c[0] == "update_env"]
    assert call[1]["VALHEIM_PASSWORD"] == "groggy"
    assert remote.configure(vm, ["apply", "x"], dict(vm.state.env)) == 1                              # refused, and says so


def test_every_page_wears_the_admiral_beercan_and_the_icons_are_public(env):
    import re
    from pathlib import Path
    client, _, _ = env
    app = client.application
    for page in (client.get("/").data.decode(), client.get("/experiments").data.decode()):        # signed out: login + a public page
        assert 'rel="icon" href="/static/favicon.svg" type="image/svg+xml"' in page
        assert 'href="/static/favicon-32.png"' in page and 'rel="apple-touch-icon" href="/static/apple-touch-icon.png"' in page
    for url, kind, magic in (("/static/favicon.svg", "image/svg+xml", b"<svg"), ("/static/favicon-32.png", "image/png", b"\x89PNG"),
                             ("/static/apple-touch-icon.png", "image/png", b"\x89PNG"),
                             ("/favicon.ico", "image/x-icon image/vnd.microsoft.icon", b"\x00\x00\x01\x00"),   # (the name varies by OS)
                             ("/apple-touch-icon.png", "image/png", b"\x89PNG")):
        resp = client.get(url)                                                                    # no sign-in needed for any of them
        assert resp.status_code == 200 and resp.mimetype in kind.split() and resp.data.lstrip()[:len(magic)] == magic, url
        assert "max-age" in resp.headers.get("Cache-Control", "") or url.startswith("/static/"), url
    icon = (Path(app.static_folder) / "favicon.svg").read_text(encoding="utf-8")
    assert "<title>Skippy the Magnificent</title>" in icon and "http" not in re.sub(r'xmlns="[^"]*"', "", icon)   # self-contained: nothing fetched
    from PIL import Image  # noqa: PLC0415
    assert Image.open(Path(app.static_folder) / "apple-touch-icon.png").size == (180, 180)
    assert sorted(Image.open(Path(app.static_folder) / "favicon.ico").info["sizes"]) == [(16, 16), (32, 32), (48, 48)]


# ── the chat is the front page; the other apps are in its tray ────────────────────────────────────────────────

def test_home_is_the_chat_for_anyone_with_the_grant_and_the_launcher_otherwise(env):
    client, _, _ = env
    login(client, FRIEND)
    home = client.get("/").data.decode()
    assert 'class="skippy-chat chat-bridge"' in home and 'data-open="apps"' in home and 'data-open="chats"' in home
    assert 'data-tel="tile:' in client.get("/apps").data.decode()               # the launcher is still at /apps
    _add_friend(client, PAL, ["dragonwilds"])                                   # no Skippy grant
    login(client, PAL)
    home = client.get("/").data.decode()
    assert 'class="skippy-chat chat-bridge"' not in home and 'data-tel="tile:' in home   # no chat for them: the launcher is home


def test_the_tray_lists_only_the_apps_this_person_may_use_and_never_the_chat_itself(env):
    client, _, _ = env
    login(client, ADMIN)
    ids = [t["id"] for t in client.get("/apps/tiles").get_json()["tiles"]]
    assert "admin" in ids and "skippy" not in ids
    login(client, FRIEND)
    d = client.get("/apps/tiles").get_json()
    assert "admin" not in [t["id"] for t in d["tiles"]] and "skippy" not in [t["id"] for t in d["tiles"]] and d["all"] == "/apps"
    assert set(d["tiles"][0]) <= {"id", "glyph", "label", "href", "state", "badge"} if d["tiles"] else True


def test_your_chats_stay_in_the_browser_and_forgetting_one_asks_nothing(env):
    client, _, _ = env
    login(client, FRIEND)
    page = client.get("/chat").data.decode()
    assert 'const STORE = "skippy-chats:" + OWNER' in page and "localStorage.setItem(STORE" in page    # this browser only, per person
    owner = re.search(r'const OWNER = "([0-9a-f]{16})"', page)
    assert owner and FRIEND not in page.split("const OWNER")[1][:200]                    # a one-way id, never the address
    login(client, ADMIN)
    assert re.search(r'const OWNER = "([0-9a-f]{16})"', client.get("/chat").data.decode()).group(1) != owner.group(1)   # his own key
    assert "confirm(" not in page                                                          # no "are you sure" boxes


def test_a_picture_asked_for_but_never_drawn_is_drawn_by_the_portal(monkeypatch):
    """Skippy Core writes [IMAGE: ...] or just describes a picture instead of calling make_image (2026-10-23: nothing came
    back): the portal starts the job itself, with the tag's description, and says it's still drawing."""
    import chat_tools as ct
    monkeypatch.setattr(ct, "IMAGE_WAIT_S", 0.05)
    skippy = FakeSkippy()
    skippy.reply = lambda *a, **k: (setattr(skippy, "tools", k.get("tools")) or "Behold.\n[IMAGE: a red barn at dusk]")
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=skippy)
    client = app.test_client()
    token = _pair(client)
    _advertise(client, token, make_image=True)
    app.extensions["home"].hold = None                               # (pictures are patient jobs: test_patient.py)
    r = client.post("/chat/send", json={"message": "draw me a barn"}).get_json()
    assert r["tools"] == ["make_image"] and "[IMAGE" not in r["reply"]
    assert [(m["kind"], m.get("pending")) for m in r["media"]] == [("image", True)]       # a "drawing" card that fills in by itself
    jobs = client.get("/home/jobs", headers={"Authorization": f"Bearer {token}"}).get_json()["jobs"]
    assert [(j["kind"], j["prompt"]) for j in jobs] == [("make_image", "a red barn at dusk")]
    skippy.reply = lambda *a, **k: "The speed of light is fast."
    assert client.post("/chat/send", json={"message": "how fast is light?"}).get_json()["tools"] == []   # no ask, no picture


def test_version_says_how_many_pop_up_tools_the_portal_knows(env):
    client, _, _ = env
    got = client.get("/version").get_json()
    import popups as popups_mod
    assert got["popups"] == len(popups_mod.names()) and got["popups"] >= 13 and got["commit"]      # the deploy's smoke test compares this with the registry
