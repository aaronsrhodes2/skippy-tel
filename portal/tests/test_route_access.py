"""Who can reach what, forever: every route of the portal is CLASSIFIED here, and the test fails on any route that is not (so a new
route can never slip in unguarded) or that is less guarded than its class says.

The classes:
  PUBLIC   anyone, signed in or not (pages and files with nothing private in them)
  MACHINE  skippy-pc / CI only: a bearer token or a Google ID token; no cookie of any user opens it (401)
  BEACON   the signed-in page's own tap beacon (401 without a session)
  MEMBER   anyone on the list (sign-in first); what they see inside is limited by their grants
  GRANT:x  a member holding the grant "x" (the Captain holds them all)
  ADMIN    the Captain only: anything that can deploy, spend, change settings, or reach his private data

This is the guard behind "visitors can never reach deploy-capable code" (the private Skippy lives under /me/workshop, ADMIN, and each of
its calls also needs the X-Workshop header). Probed as: nobody, a stranger (signed in with Google but not on the list), and a friend
who holds ONLY the chat grant."""
import re

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import chat_tools  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm, login, _add_friend  # noqa: E402

PUBLIC, MACHINE, BEACON, MEMBER, ADMIN_ONLY = "public", "machine", "beacon", "member", "admin"
G = lambda name: "grant:" + name                                                                           # noqa: E731

REGISTRY = {
    # the Captain only
    **{e: ADMIN_ONLY for e in (
        "admin.autoconfigure", "admin.discord_setup", "admin.friends_remove", "admin.friends_save", "admin.index", "admin.logs",
        "admin.module_switch", "admin.requests_dismiss", "admin.restart", "admin.settings", "recovery.act", "server_admin.fragment",
        "me.index", "me.pair_home", "me.toggle_experiment", "me.workshop_events", "me.workshop_history", "me.workshop_history_result",
        "me.workshop_image", "me.workshop_notify", "me.workshop_resync", "me.workshop_send", "me.workshop_ship", "me.workshop_stop",
        "chat.memory_ask", "chat.memory_result")},
    # granted apps
    "music.player_page": G("audio"), "music.player_gate": G("audio"), "music.player_checkin": G("audio"), "music.player_command": G("audio"),
    "video.video_page": G("video"), "video.video_gate": G("video"),
    "chat.index": G("skippy"), "chat.send": G("skippy"), "chat.badges": G("skippy"), "chat.voice_gate": G("skippy"), "chat.media_file": G("skippy"),
    # members
    **{e: MEMBER for e in (
        "apps", "dashboard.game", "dashboard.game_discord", "dashboard.game_live", "dashboard.games_start", "dashboard.games_stop", "dashboard.index",
        "doomloop.page", "doomloop.game_file", "starcontrol.page", "starcontrol.game_file")},
    # skippy-pc, CI
    **{e: MACHINE for e in (
        "game_import_done", "game_import_download", "home_import_chunk", "home_job_done", "home_jobs", "home_media", "home_report",
        "home_telemetry", "home_workshop", "home_workshop_list", "ops_home", "tasks_run")},
    "telemetry_tap": BEACON,
    # public: sign-in, the front door, static and informational pages, the public experiments (free local compute, rate limited)
    **{e: PUBLIC for e in (
        "auth.callback", "auth.login", "auth.logout", "auth.unauthorized", "health", "version", "index", "privacy", "request_access",
        "site_icons", "legacy_bilby", "dashboard.legacy_dashboard", "music.player_manifest", "video.video_manifest",
        "doomloop.version", "starcontrol.version", "play_index", "orion_trail", "radiance_probe",
        "experiments.benchmark", "experiments.double_slit", "experiments.index", "experiments.mentat_call", "experiments.mentat_page",
        "experiments.mentat_tools", "experiments.nbody", "experiments.radiance_file", "experiments.radiance_index",
        "experiments.weapon_vs_armor", "experiments.wva_catalog", "experiments.wva_solve")},
}
FRIEND_CHAT_ONLY = "pal@example.com"
STRANGER = "stranger@example.com"
LOGIN_BOUNCE = (301, 302, 303, 307, 308, 401, 403)


@pytest.fixture(scope="module")
def world():
    fake = FakeSkippy()
    app = portal.create_app({**SETTINGS}, vm=FakeVm(), skippy=fake)
    app.fake_skippy = fake                                                       # (so the chat tests can see what the model was offered)
    c = app.test_client()
    _add_friend(c, FRIEND_CHAT_ONLY, ["skippy"])                                  # a friend with ONLY the chat grant
    return app


def url_for_rule(rule) -> str:
    url = rule.rule
    for arg in rule.arguments:
        kind = type(rule._converters.get(arg)).__name__
        url = re.sub(r"<(?:[^:>]+:)?" + arg + ">", "1" if kind == "IntegerConverter" else "x1234567", url)
    return url


def probe(app, who, method, url):
    client = app.test_client()
    if who:
        login(client, who)
    kw = {"data": b"x"} if method == "PUT" else ({"json": {}} if method == "POST" else {})
    return client.open(url, method=method, **kw).status_code


def routes(app):
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.endpoint, r.rule)):
        if rule.endpoint != "static":
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                yield rule, method, url_for_rule(rule)


def test_every_route_is_classified_and_nothing_stale_is_listed(world):
    live = {r.endpoint for r in world.url_map.iter_rules() if r.endpoint != "static"}
    assert live - set(REGISTRY) == set(), f"unclassified routes (decide who may reach them, then list them above): {sorted(live - set(REGISTRY))}"
    assert set(REGISTRY) - live == set(), f"stale entries: {sorted(set(REGISTRY) - live)}"


def test_anything_under_admin_or_me_or_that_can_reach_the_captains_private_data_is_admin_only():
    for endpoint, cls in REGISTRY.items():
        if endpoint.startswith(("admin.", "me.", "recovery.", "server_admin.")):
            assert cls == ADMIN_ONLY, endpoint


def test_the_default_deny_matrix(world):
    """Nobody, a stranger and a chat-only friend are each refused wherever their class says they must be."""
    failures = []
    for rule, method, url in routes(world):
        cls = REGISTRY[rule.endpoint]
        anon, stranger, friend = (probe(world, who, method, url) for who in (None, STRANGER, FRIEND_CHAT_ONLY))
        if cls == PUBLIC:
            continue
        if cls in (MACHINE, BEACON):
            if anon != 401:
                failures.append(f"{cls} {method} {url}: nobody got {anon}, expected 401")
            if cls == MACHINE and (stranger != 401 or friend != 401):
                failures.append(f"{cls} {method} {url}: a signed-in user got {stranger}/{friend}: a cookie must never open a machine route")
            continue
        for label, got in (("nobody", anon), ("a stranger", stranger)):
            if got not in LOGIN_BOUNCE:
                failures.append(f"{cls} {method} {url}: {label} got {got}")
        if cls == ADMIN_ONLY and friend != 403:
            failures.append(f"admin {method} {url}: a friend got {friend}, expected 403")
        if cls.startswith("grant:") and cls != G("skippy") and friend != 403:
            failures.append(f"{cls} {method} {url}: a friend without that grant got {friend}, expected 403")
        if cls == G("skippy") and friend == 403 and not rule.endpoint.startswith("chat.memory"):
            failures.append(f"{cls} {method} {url}: a friend WITH the chat grant was refused ({friend})")
    assert not failures, "\n".join(failures)


def test_a_friend_cannot_start_a_game_they_were_not_given(world):
    client = world.test_client()
    login(client, FRIEND_CHAT_ONLY)
    resp = client.post("/games/start", data={"games": ["valheim"]})
    assert resp.status_code in (302, 400, 403)                                    # never a 200 that woke the VM
    assert not world.extensions["vm"].started if hasattr(world.extensions.get("vm"), "started") else True


def test_the_agents_endpoints_need_the_header_even_for_the_captain(world):
    """A page on another site can make the Captain's browser POST, but cannot add this header without a CORS preflight."""
    client = world.test_client()
    login(client, ADMIN)
    for method, url in (("POST", "/me/workshop/send"), ("POST", "/me/workshop/stop"), ("POST", "/me/workshop/history"),
                        ("POST", "/me/workshop/resync"), ("POST", "/me/workshop/notify")):
        assert client.open(url, method=method, json={"message": "deploy"}).status_code == 400, url
    with_header = client.post("/me/workshop/send", json={"conv": "conv-12345678", "message": "hi"}, headers={"X-Workshop": "1"})
    assert with_header.status_code in (200, 409, 503)                              # with the header it is the Captain's call (503: home is not reporting here)


# ── the public chat: no write, no execute, whatever the page says ──
ALLOWED_PUBLIC_TOOLS = {"server_status", "start_game", "stop_server", "ask_mentat", "now_playing"}
ADMIN_TOOLS = {"restart_server", "server_logs", "make_music", "music_result"}


def test_the_public_chats_tools_are_only_the_portals_own_and_never_a_shell_a_file_or_a_deploy():
    assert set(chat_tools._EVERYONE) <= ALLOWED_PUBLIC_TOOLS
    assert set(chat_tools._ADMIN) - set(chat_tools._EVERYONE) <= ADMIN_TOOLS
    for name in set(chat_tools._EVERYONE) | set(chat_tools._ADMIN):
        assert not re.search(r"exec|shell|bash|write|edit|deploy|push|commit|run_|file", name), name


def test_barney_style_gives_haiku_no_tools_at_all_and_a_friend_never_gets_admin_tools(world):
    fake = world.fake_skippy
    client = world.test_client()
    login(client, FRIEND_CHAT_ONLY)
    client.post("/chat/send", json={"message": "Give it to me Barney Style", "history": []})
    assert not fake.tools                                                          # Barney Style: no tools at all (nothing is computed or looked up)
    client.post("/chat/send", json={"message": "is the server up?", "history": []})
    names = {t["name"] for t in (fake.tools or [])}
    assert names and names <= ALLOWED_PUBLIC_TOOLS and not names & ADMIN_TOOLS    # a friend's normal chat: the portal's own tools, none of the Captain's


def test_the_front_page_shows_a_friend_none_of_the_agent_and_nothing_that_could_reach_it(world):
    """The Captain sees Skippy Desktop on the front page; a friend's page must not even MENTION it: no markup, no script, no endpoint."""
    friend = world.test_client()
    login(friend, FRIEND_CHAT_ONLY)
    page = friend.get("/").data.decode()
    assert 'class="skippy-chat chat-bridge"' in page                                   # the public chat is there
    for needle in ("private-skippy", "/me/workshop", "X-Workshop", "ps-form", "workshop_", "mode-agent", "modebar", "captain"):
        assert needle not in page, needle
    captain = world.test_client()
    login(captain, ADMIN)
    page = captain.get("/").data.decode()
    assert "private-skippy" in page and "/me/workshop/send" in page and 'class="bridge captain"' in page


def test_the_front_page_for_someone_without_the_chat_grant_is_the_apps_and_signed_out_is_the_sign_in(world):
    only_games = world.test_client()
    login(only_games, ADMIN)
    only_games.post("/admin/friends", data={"email": "gamer@example.com", "portals": ["valheim"]})
    login(only_games, "gamer@example.com")
    assert only_games.get("/").headers["Location"].endswith("/apps")
    anon = world.test_client()
    assert anon.get("/").status_code == 200 and b"Sign in" in anon.get("/").data and b"private-skippy" not in anon.get("/").data
