"""Site telemetry: the recorder's rules (what is kept, what is scrubbed, what is never recorded), its wiring into the
portal (page views, actions, taps, chats, errors; signed-in people only), the home reporter's pull, the public /privacy
page rendered from the recorder's own lists, the notices, and the dashboard's Activity widget."""
import html
import json
import re
import time
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import chat  # noqa: E402
import telemetry as tel_mod  # noqa: E402
import telemetry_web  # noqa: E402
from catalog import GAMES  # noqa: E402
from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402

PAL = "pal@example.com"                 # a friend (in the friends list, not core)
CORE = "core@example.com"               # core: in ALLOWED_EMAILS, not an admin
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def make(enabled=True, skippy=None):
    vm = FakeVm()
    vm.metadata["portal-friends"] = json.dumps({PAL: {"portals": ["valheim", "skippy"], "added_by": ADMIN}})
    settings = {**SETTINGS, "ALLOWED_EMAILS": frozenset({ADMIN, CORE}), "TELEMETRY_ENABLED": enabled}
    app = portal.create_app(settings, vm=vm, skippy=skippy or FakeSkippy())
    return app, app.test_client(), app.extensions["telemetry"], vm


def login(client, email, name="Someone"):
    with client.session_transaction() as s:
        s["user"] = {"email": email, "name": name, "picture": None}


def events(tel, kind=None):
    got = tel.since(tel.boot, 0, 500)["events"]
    return [e for e in got if kind is None or e["kind"] == kind]


def everything(tel) -> str:
    return json.dumps(tel.since(tel.boot, 0, 500)["events"])


# ── the recorder ────────────────────────────────────────────────────────────────

def test_events_are_numbered_and_carry_who_and_when():
    tel = tel_mod.Telemetry(clock=lambda: 1000.0)
    first = tel.record("page", "/games", user="a@x.com", role="admin", sid="ab12", status=200)
    second = tel.record("ui", "tile:games", user="a@x.com", role="admin", sid="ab12")
    assert (first["seq"], second["seq"]) == (1, 2) and first["boot"] == second["boot"] == tel.boot
    assert first == {"ts": 1000.0, "user": "a@x.com", "role": "admin", "sid": "ab12", "kind": "page",
                     "name": "/games", "detail": {"status": 200}, "seq": 1, "boot": tel.boot}


def test_a_long_email_is_kept_as_it_is_and_is_not_mistaken_for_a_token():
    tel = tel_mod.Telemetry()
    email = "a" * 45 + "@example.com"
    assert tel.record("page", "/", user=email)["user"] == email
    assert tel.record("page", "/", user="x" * 500)["user"] == "x" * 120


def test_a_pull_acknowledges_what_it_has_stored_and_the_portal_forgets_it():
    tel = tel_mod.Telemetry()
    for n in range(5):
        tel.record("page", f"/p{n}", user="a@x.com")
    got = tel.since("", 0)                                   # first ever pull: an unknown boot starts from the top
    assert [e["seq"] for e in got["events"]] == [1, 2, 3, 4, 5] and got["boot"] == tel.boot and not got["more"]
    assert tel.status()["waiting"] == 5                      # not forgotten until it is acknowledged
    again = tel.since(tel.boot, 3)                           # "I have stored up to 3"
    assert [e["seq"] for e in again["events"]] == [4, 5] and tel.status()["waiting"] == 2
    assert tel.since(tel.boot, 5)["events"] == [] and tel.status()["waiting"] == 0


def test_a_pull_that_names_another_boot_starts_again_from_the_top_and_forgets_nothing():
    tel = tel_mod.Telemetry()
    for n in range(3):
        tel.record("page", f"/p{n}", user="a@x.com")
    got = tel.since("deadbeef", 99)                          # the collector last talked to a previous portal process
    assert [e["seq"] for e in got["events"]] == [1, 2, 3] and tel.status()["waiting"] == 3


def test_a_pull_is_limited_and_says_there_is_more():
    tel = tel_mod.Telemetry()
    for n in range(7):
        tel.record("page", f"/p{n}", user="a@x.com")
    got = tel.since(tel.boot, 0, 3)
    assert [e["seq"] for e in got["events"]] == [1, 2, 3] and got["more"] is True
    assert len(tel.since(tel.boot, 0, 10_000)["events"]) == 7          # a caller can't ask for the moon
    assert len(tel.since(tel.boot, 0, 0)["events"]) == 1               # nor for nothing


def test_the_buffer_is_bounded_and_counts_what_it_had_to_drop():
    tel = tel_mod.Telemetry(max_events=3)
    for n in range(5):
        tel.record("page", f"/p{n}", user="a@x.com")
    assert tel.status()["waiting"] == 3 and tel.status()["dropped"] == 2
    assert [e["name"] for e in tel.since(tel.boot, 0)["events"]] == ["/p2", "/p3", "/p4"]      # the oldest went


def test_a_disabled_recorder_records_nothing():
    tel = tel_mod.Telemetry(enabled=False)
    assert tel.record("page", "/games", user="a@x.com") is None and tel.status()["waiting"] == 0


def test_the_recorders_own_health_says_when_home_last_collected():
    now = [1000.0]
    tel = tel_mod.Telemetry(clock=lambda: now[0])
    assert tel.status()["last_collected_s"] is None                    # nobody has ever pulled
    tel.record("page", "/games", user="a@x.com")
    tel.since(tel.boot, 0)
    now[0] += 42
    assert tel.status() == {"enabled": True, "waiting": 1, "dropped": 0, "kinds": {"page": 1}, "last_collected_s": 42}
    assert [e["name"] for e in tel.recent(5)] == ["/games"]


@pytest.mark.parametrize("said, gone", [
    ("the valheim password is hunter22", "hunter22"),
    ("password for valheim is hunter22 ok", "hunter22"),
    ("set the password to hunter22.", "hunter22"),
    ("Join password: skippy123 and again password=skippy123", "skippy123"),
    ("VALHEIM_PASSWORD=hunter22", "hunter22"),
    ("DRAGONWILDS_ADMIN_PASSWORD: adm1n-pw", "adm1n-pw"),
    ('{"password": "my secret phrase", "x": 1}', "my secret phrase"),
    ("api_key = 'a b c'", "a b c"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345", "abcdefghijklmnopqrstuvwxyz012345"),
    ("my token is ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("here: sk-ant-api03-abcdefghijklmnopqrstuvwx", "sk-ant-api03-abcdefghijklmnopqrstuvwx"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123456ghiJKL", "eyJzdWIiOiIxMjM0NTY3ODkwIn0"),
    ("a bare one: " + "Ab3-" * 12, "Ab3-Ab3-Ab3-Ab3"),
])
def test_secrets_are_scrubbed_from_what_people_type(said, gone):
    scrubbed = tel_mod.redact(said)
    assert gone not in scrubbed and "[redacted]" in scrubbed


@pytest.mark.parametrize("fine", [
    "How do I reset my password? I forgot it.", "what is the world password for dragonwilds? is it set",
    "start valheim please", "compass: north, passport: none", "how many people are on the server right now"])
def test_ordinary_text_is_left_alone(fine):
    assert tel_mod.redact(fine) == fine


def test_a_field_that_sounds_secret_is_dropped_whatever_it_holds():
    got = tel_mod.clean({"password": "x", "Authorization": "y", "tools": [{"name": "start_game",
                         "args": {"games": ["valheim"], "api_key": "zzz"}}], "ok": True, "n": 3, "empty": ""})
    assert got["password"] == got["Authorization"] == "[redacted]"
    assert got["tools"][0]["args"] == {"games": ["valheim"], "api_key": "[redacted]"}
    assert got["ok"] is True and got["n"] == 3 and got["empty"] == ""


def test_clean_is_json_safe_and_size_capped():
    got = tel_mod.clean({"n": float("nan"), "inf": float("inf"), "bad": object(), "long": "word " * 1000,
                         "message": "say " * 3000, "reply": "yes " * 3000, "many": list(range(100))})
    json.dumps(got, allow_nan=False)                                    # NaN/Infinity aren't JSON: they became null
    assert got["n"] is None and got["inf"] is None
    assert len(got["long"]) == tel_mod.DEFAULT_LIMIT and len(got["message"]) == 4000 and len(got["reply"]) == 8000
    assert len(got["many"]) == 30


def test_a_code_a_tool_returned_is_scrubbed_if_skippy_repeats_it():
    results = ['{"games": {"dragonwilds": {"invite_code": "QX7-4419", "name": "Dragonwilds"}}}', "not json", "[1, 2]"]
    hidden = tel_mod.secret_values(results)
    assert hidden == {"QX7-4419"}
    assert tel_mod.scrub_values("Your invite code is QX7-4419, enjoy.", hidden) == "Your invite code is [redacted], enjoy."


# ── wired into the portal ───────────────────────────────────────────────────────

def test_signed_out_people_and_visitors_are_never_recorded():
    app, client, tel, _ = make()
    for path in ("/", "/experiments", "/experiments/mentat", "/privacy", "/health", "/request-access", "/games", "/login"):
        client.get(path)
    client.post("/telemetry/tap", data='{"e": "tile:games"}')
    with client.session_transaction() as s:                              # signed in with Google, not on the list
        s["visitor"] = {"email": "stranger@example.com", "name": "S"}
    client.get("/request-access")
    client.post("/request-access", data={"games": ["valheim"], "note": "please"})
    assert tel.status()["waiting"] == 0


def test_a_removed_friend_still_holding_a_session_is_not_recorded():
    app, client, tel, vm = make()
    login(client, PAL)
    vm.metadata["portal-friends"] = "{}"                                 # they were removed
    app.extensions["friends"]._cache = None
    client.get("/experiments")                                           # a public page: no sign-in check of its own
    assert tel.status()["waiting"] == 0


def test_signed_in_page_views_are_recorded_by_path_without_the_query_string():
    app, client, tel, _ = make()
    login(client, ADMIN)
    client.get("/games?token=supersecret&x=1")
    client.get("/games/valheim")
    client.get("/")
    pages = events(tel, "page")
    assert [e["name"] for e in pages] == ["/games", "/games/valheim", "/"]
    assert pages[0]["user"] == ADMIN and pages[0]["role"] == "admin"
    assert pages[0]["detail"]["endpoint"] == "dashboard.index" and pages[0]["detail"]["status"] == 200
    assert "supersecret" not in everything(tel) and "token=" not in everything(tel)


def test_roles_say_captain_core_or_friend():
    app, client, tel, _ = make()
    for email in (ADMIN, CORE, PAL):
        login(client, email)
        client.get("/games")
    assert [e["role"] for e in events(tel, "page")] == ["admin", "core", "friend"]


def test_no_ip_address_or_user_agent_is_recorded():
    app, client, tel, _ = make()
    login(client, ADMIN)
    client.get("/games", headers={"User-Agent": "Mozilla/5.0 (Sekrit Browser 9)", "X-Forwarded-For": "198.51.100.7",
                                  "Accept-Language": "xx-YY"}, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    client.post("/games/start", data={"games": "valheim"}, headers={"User-Agent": "Sekrit Browser 9"},
                environ_base={"REMOTE_ADDR": "203.0.113.9"})
    dump = everything(tel)
    for leak in ("203.0.113.9", "198.51.100.7", "Sekrit", "Mozilla", "xx-YY"):
        assert leak not in dump
    assert {"ts", "user", "role", "sid", "kind", "name", "detail", "seq", "boot"} == set(events(tel)[0])


def test_one_sign_in_has_one_random_session_id_and_another_sign_in_another():
    app, client, tel, _ = make()
    login(client, ADMIN)
    client.get("/games")
    client.get("/games/valheim")
    other = app.test_client()
    login(other, ADMIN)
    other.get("/games")
    a, b, c = [e["sid"] for e in events(tel, "page")]
    assert a == b != c and re.fullmatch(r"[0-9a-f]{8}", a)


def test_actions_keep_field_names_and_only_the_allow_listed_values():
    app, client, tel, _ = make()
    login(client, ADMIN)
    client.post("/games/start", data={"games": ["valheim", "dragonwilds"], "idle_minutes": "45", "next": "/games/valheim",
                                      "free_text": "my address is 1 Nowhere Lane"})
    client.post("/admin/autoconfigure", data={"join_password": "hunter2-hunter2"})
    client.post("/admin/friends", data={"email": "new@example.com", "portals": ["valheim", "skippy"]})
    start, autoconf, friend = events(tel, "action")
    assert start["name"] == "dashboard.games_start" and start["detail"]["method"] == "POST"
    assert start["detail"]["fields"] == ["games", "idle_minutes", "next", "free_text"]
    assert start["detail"]["values"] == {"games": ["valheim", "dragonwilds"], "idle_minutes": ["45"]}
    assert autoconf["detail"]["fields"] == ["join_password"] and autoconf["detail"]["values"] == {}
    assert friend["detail"]["fields"] == ["email", "portals"] and friend["detail"]["values"] == {"portals": ["valheim", "skippy"]}
    dump = everything(tel)
    for typed in ("hunter2", "Nowhere", "new@example.com"):
        assert typed not in dump


def test_an_allow_listed_field_only_keeps_a_value_of_the_right_shape():
    app, client, tel, _ = make()
    login(client, ADMIN)
    client.post("/games/start", data={"games": "not a game id!", "idle_minutes": "lots"})
    (action,) = events(tel, "action")
    assert action["detail"]["fields"] == ["games", "idle_minutes"] and action["detail"]["values"] == {}


def test_a_failed_action_says_so():
    app, client, tel, _ = make()
    login(client, PAL)
    client.post("/games/stop")                                          # fine, or refused: either way it is recorded
    client.post("/admin/restart")                                       # a friend may not: 403
    ok, refused = events(tel, "action")
    assert refused["name"] == "admin.restart" and refused["detail"]["ok"] is False and refused["detail"]["status"] == 403
    assert isinstance(ok["detail"]["ok"], bool)


def test_the_polls_media_and_machine_endpoints_are_never_recorded():
    app, client, tel, vm = make()
    login(client, ADMIN)
    for path in ("/games/valheim/live", "/games/valheim/discord", "/chat/badges",
                 "/health", "/favicon.ico", "/static/favicon.svg", "/me/workshop/events?task=abcdef12&after=0",
                 "/chat/media/abc", "/experiments/radiance/app.js"):
        client.get(path)
    client.post("/me/workshop/send", json={"message": "private"}, headers={"X-Workshop": "1"})
    client.post("/probe")
    client.post("/telemetry/tap", json={"e": "tile:games", "p": "/"})
    assert [e for e in events(tel) if e["kind"] != "ui"] == []


def test_every_skipped_endpoint_is_a_real_endpoint():
    app, *_ = make()
    assert telemetry_web.SKIP_ENDPOINTS <= set(app.view_functions), \
        sorted(telemetry_web.SKIP_ENDPOINTS - set(app.view_functions))
    assert telemetry_web.CHAT_ENDPOINTS <= set(app.view_functions)


def test_a_signed_in_person_who_hits_an_error_page_is_recorded_as_an_error():
    app, client, tel, _ = make()
    login(client, PAL)
    client.get("/games/nope")
    client.get("/admin")                                                # 403 for a friend
    errors = events(tel, "error")
    assert [(e["name"], e["detail"]["path"]) for e in errors] == [("404", "/games/nope"), ("403", "/admin")]
    assert events(tel, "page") == []


def test_a_server_error_is_recorded_and_the_page_still_fails_as_it_would():
    app, client, tel, _ = make()
    app.add_url_rule("/boom", "boom", lambda: 1 / 0)
    login(client, ADMIN)
    assert client.get("/boom").status_code == 500
    assert client.post("/boom").status_code == 405                      # only GET was wired: not an error of ours
    (err,) = events(tel, "error")
    assert err["name"] == "500" and err["detail"]["endpoint"] == "boom"


def test_a_recorder_that_breaks_never_breaks_a_page(monkeypatch):
    app, client, tel, _ = make()
    login(client, ADMIN)

    def broken(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(tel, "record", broken)
    assert client.get("/games").status_code == 200 and client.post("/games/stop").status_code == 302


def test_switching_it_off_records_nothing_and_says_so():
    app, client, tel, _ = make(enabled=False)
    login(client, ADMIN)
    client.get("/games")
    client.post("/games/stop")
    client.post("/chat/send", json={"message": "hello"})
    assert client.post("/telemetry/tap", json={"e": "tile:games"}).status_code == 204
    assert tel.status()["waiting"] == 0 and not tel.enabled
    assert "switched off" in client.get("/privacy").data.decode()
    assert "sendBeacon" not in client.get("/").data.decode()


def test_telemetry_enabled_is_read_from_the_environment_and_defaults_on(monkeypatch):
    import config
    for name, value in {"FLASK_SECRET_KEY": "k", "GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s",
                        "ALLOWED_EMAILS": ADMIN, "GCP_PROJECT": "p"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("TELEMETRY_ENABLED", raising=False)
    assert config.load()["TELEMETRY_ENABLED"] is True
    for off in ("false", "FALSE", "0", "no", "off"):
        monkeypatch.setenv("TELEMETRY_ENABLED", off)
        assert config.load()["TELEMETRY_ENABLED"] is False, off
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    assert config.load()["TELEMETRY_ENABLED"] is True


# ── chat ─────────────────────────────────────────────────────────────────────────

def test_nothing_said_in_the_public_chat_is_recorded_unless_the_person_ticks_allow_saving():
    """The Captain's rule (2026-09-30): what people ask Skippy is kept only if they tick "Allow us to save your questions"."""
    class Tooling:
        def reply(self, system, messages, tools=None, run_tool=None, model=None, force_tool=None):
            run_tool("server_status", {})
            return "Valheim is asleep. The plan with the dragon is a good one."

    app, client, tel, _ = make(skippy=Tooling())
    login(client, ADMIN)
    for body in ({"message": "my secret plan involves a dragon"}, {"message": "dragon again", "save": False},
                 {"message": "dragon, truthy", "save": "yes"}):                          # only a real true counts
        assert client.post("/chat/send", json={**body, "history": []}).status_code == 200
    assert events(tel, "chat") == [] and events(tel, "action") == []
    assert "dragon" not in everything(tel) and "server_status" not in everything(tel)
    chat_page = client.get("/chat").data.decode()
    assert "Allow us to save your questions" in chat_page and 'class="save"' in chat_page and " checked" not in chat_page.split('class="save"')[1][:40]


def test_with_saving_allowed_a_chat_is_recorded_redacted_with_its_tools_and_arguments_but_not_their_results():
    class Tooling:
        def reply(self, system, messages, tools=None, run_tool=None, model=None, force_tool=None):
            run_tool("server_status", {})
            return "Valheim is asleep. Its join password is hunter22, obviously."

    app, client, tel, _ = make(skippy=Tooling())
    login(client, ADMIN)
    res = client.post("/chat/send", json={"message": "is valheim up? the valheim password is hunter22", "history": [], "save": True})
    assert res.status_code == 200
    (ev,) = events(tel, "chat")
    d = ev["detail"]
    assert ev["user"] == ADMIN and d["consented"] is True and d["model"] == "claude-haiku-4-5"
    assert d["message"] == "is valheim up? the valheim password is [redacted]"
    assert d["reply"] == "Valheim is asleep. Its join password is [redacted] obviously."
    assert d["tools"] == [{"name": "server_status", "args": {}}]
    assert "hunter22" not in everything(tel) and "join_password" not in everything(tel)


def test_an_invite_code_a_tool_returned_is_not_kept_even_if_skippy_repeats_it():
    app, client, tel, _ = make()
    with app.test_request_context("/chat/send", method="POST"):
        from flask import session
        session["user"] = {"email": ADMIN, "name": "A", "picture": None}
        telemetry_web.record_chat("what's the code?", "The invite code is QX7-4419.", "claude-haiku-4-5",
                                  [("server_status", {}, '{"games": {"dragonwilds": {"invite_code": "QX7-4419"}}}')])
    (ev,) = events(tel, "chat")
    assert ev["detail"]["reply"] == "The invite code is [redacted]." and "QX7-4419" not in everything(tel)


def test_a_chat_that_failed_is_an_action_that_failed_and_keeps_no_text():
    class Broken:
        def reply(self, *a, **k):
            raise chat.ChatError("the model is unhappy")

    app, client, tel, _ = make(skippy=Broken())
    login(client, ADMIN)
    assert client.post("/chat/send", json={"message": "secret plans"}).status_code == 502
    (act,) = events(tel, "action")
    assert act["name"] == "chat.send" and act["detail"]["ok"] is False and events(tel, "chat") == []
    assert "secret plans" not in everything(tel)


# ── the tap beacon ────────────────────────────────────────────────────────────────

def test_a_tap_is_recorded_for_a_signed_in_person_only():
    app, client, tel, _ = make()
    assert client.post("/telemetry/tap", json={"e": "tile:games", "p": "/"}).status_code == 401
    login(client, PAL)
    assert client.post("/telemetry/tap", json={"e": "tile:game:valheim", "p": "/games"}).status_code == 204
    (tap,) = events(tel, "ui")
    assert tap["name"] == "tile:game:valheim" and tap["user"] == PAL and tap["detail"] == {"page": "/games"}


def test_the_beacon_may_post_a_blob_of_any_content_type_but_nothing_odd():
    app, client, tel, _ = make()
    login(client, ADMIN)
    post = lambda body, **kw: client.post("/telemetry/tap", data=body, **kw).status_code       # noqa: E731
    assert post(json.dumps({"e": "demo:nbody", "p": "/experiments"}), content_type="text/plain") == 204
    assert post("not json") == 400 and post(json.dumps({"e": "<script>"})) == 400 and post(json.dumps({"p": "/"})) == 400
    assert post(json.dumps({"e": "x" * 200})) == 400
    assert post(json.dumps({"e": "tile:games", "pad": "x" * 5000})) == 413
    assert post(json.dumps({"e": "tile:games", "p": "javascript:alert(1)"})) == 204
    assert [e["detail"] for e in events(tel, "ui")] == [{"page": "/experiments"}, {"page": ""}]


def test_a_beacon_body_without_a_content_length_is_bounded_too():
    import io
    app, client, tel, _ = make()
    login(client, ADMIN)
    big = b'{"e": "tile:games", "pad": "' + b"x" * 50_000 + b'"}'
    res = client.post("/telemetry/tap", input_stream=io.BytesIO(big), content_type="application/json",
                      environ_base={"wsgi.input_terminated": True})            # a chunked upload: no Content-Length to check
    assert res.status_code == 413 and events(tel, "ui") == []


def test_a_flood_of_taps_is_rate_limited_per_person():
    app, client, tel, _ = make()
    login(client, ADMIN)
    codes = [client.post("/telemetry/tap", json={"e": "tile:games"}).status_code for _ in range(telemetry_web.TAPS_PER_MIN + 3)]
    assert codes.count(204) == telemetry_web.TAPS_PER_MIN and codes[-1] == 429


def test_the_pages_carry_the_beacon_only_for_signed_in_people():
    app, client, tel, _ = make()
    assert "sendBeacon" not in client.get("/").data.decode()
    login(client, ADMIN)
    page = client.get("/").data.decode()
    assert "sendBeacon" in page and '"/telemetry/tap"' in page and "data-tel" in page


def test_every_tile_id_the_templates_carry_is_one_the_dashboard_knows():
    known = set(telemetry_web.known_taps())
    import modules
    seen = set()
    for name in ("games.html", "play.html", "experiments.html"):
        seen |= set(re.findall(r'data-tel="([^"{]+)"', (TEMPLATES / name).read_text(encoding="utf-8")))
    # the launcher is built from the module registry: one tile per module, its id the tap id (templates/home.html)
    assert 'data-tel="tile:{{ t.id }}"' in (TEMPLATES / "home.html").read_text(encoding="utf-8")
    assert {m.id for m in modules.MODULES} == set(telemetry_web.LAUNCHER_TILES)
    seen |= {f"tile:{m.id}" for m in modules.MODULES}
    launcher = {t for t in seen if t.startswith("tile:") and t.count(":") == 1}
    assert launcher == {f"tile:{t}" for t in telemetry_web.LAUNCHER_TILES}
    assert seen <= known | {"tile:games:"}                                # the live badge is `tile:games:<id>`, not a tile
    for gid in GAMES:
        assert f"tile:game:{gid}" in known
    assert "demo:double-slit" in known and "play:star-control-zero" in known and all(_ok(t) for t in known)


def _ok(tap):
    return re.fullmatch(r"[a-z0-9:_.\-]{1,60}", tap) is not None


def test_the_allow_listed_form_fields_are_real_fields_and_never_free_text_or_settings():
    source = (Path(portal.__file__).read_text(encoding="utf-8")
              + "".join(p.read_text(encoding="utf-8") for p in TEMPLATES.glob("*.html")))
    setting_keys = {s.key for g in GAMES.values() for s in g.settings}
    for name in telemetry_web.SAFE_VALUES:
        assert f'"{name}"' in source, f"{name} isn't a field any form or route uses"
        assert name not in setting_keys
    assert not {"email", "note", "join_password", "message", "next"} & set(telemetry_web.SAFE_VALUES)


# ── the home reporter's pull ────────────────────────────────────────────────────────

def test_home_pulls_events_with_its_own_token_and_acknowledges_them():
    app, client, tel, vm = make()
    token = app.extensions["home"].pair()
    login(client, ADMIN)
    client.get("/games")
    client.get("/games/valheim")
    auth = {"Authorization": f"Bearer {token}"}
    other = app.test_client()                                            # the reporter isn't signed in to anything
    assert other.get("/home/telemetry").status_code == 401
    assert other.get("/home/telemetry", headers={"Authorization": "Bearer wrong"}).status_code == 401
    got = other.get("/home/telemetry", headers=auth).get_json()
    assert got["boot"] == tel.boot and [e["name"] for e in got["events"]] == ["/games", "/games/valheim"] and not got["more"]
    assert other.get("/home/telemetry", headers=auth).headers["Cache-Control"] == "no-store"
    assert tel.status()["waiting"] == 2                                  # asked twice, acknowledged nothing: nothing forgotten
    last = got["events"][-1]["seq"]
    again = other.get(f"/home/telemetry?boot={got['boot']}&after={last}", headers=auth).get_json()
    assert again["events"] == [] and tel.status()["waiting"] == 0 and tel.status()["last_collected_s"] == 0


def test_a_pull_can_be_limited_and_bad_parameters_are_ignored():
    app, client, tel, vm = make()
    token = app.extensions["home"].pair()
    for n in range(5):
        tel.record("page", f"/p{n}", user="a@x.com")
    auth = {"Authorization": f"Bearer {token}"}
    got = client.get("/home/telemetry?limit=2&after=-5&boot=../../etc", headers=auth).get_json()
    assert len(got["events"]) == 2 and got["more"] is True
    assert len(client.get("/home/telemetry?limit=abc&after=xyz", headers=auth).get_json()["events"]) == 5


def test_the_pull_carries_no_ip_and_the_endpoint_is_not_itself_recorded():
    app, client, tel, vm = make()
    token = app.extensions["home"].pair()
    client.get("/home/telemetry", headers={"Authorization": f"Bearer {token}"}, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert tel.status()["waiting"] == 0


# ── /privacy, the notices and the footer ──────────────────────────────────────────

def test_privacy_is_public_and_rendered_from_the_recorders_own_lists(monkeypatch):
    app, client, tel, _ = make()
    page = html.unescape(client.get("/privacy").data.decode())          # signed out
    for title, text in tel_mod.RECORDED:
        assert title in page and text.split(" (")[0][:40] in page
    for text in tel_mod.NOT_RECORDED + tel_mod.ALSO:
        assert text[:50] in page
    monkeypatch.setattr(tel_mod, "RECORDED", (("Sentinel heading", "sentinel body text"),))
    monkeypatch.setattr(tel_mod, "NOT_RECORDED", ("A sentinel promise",))
    changed = client.get("/privacy").data.decode()
    assert "Sentinel heading" in changed and "sentinel body text" in changed and "A sentinel promise" in changed
    assert "Pages you open" not in changed                              # the page can't say what the code doesn't


def test_privacy_says_who_sees_it_where_it_is_kept_for_how_long_and_how_to_have_it_deleted():
    app, client, tel, _ = make()
    page = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", client.get("/privacy").data.decode()))
    assert "Aaron only" in page and "isn't shared" in page
    assert "skippy-pc" in page and f"{tel_mod.KEEP_DAYS} days" in page and tel_mod.KEEP_DAYS == 180
    assert "Ask Aaron" in page and "delete everything recorded under your email" in page
    assert "IP address" in page and "Anthropic" in page and "Cloud Run" in page       # what else is true, said plainly


def test_privacy_is_reachable_signed_in_with_a_back_button():
    app, client, tel, _ = make()
    login(client, ADMIN)
    page = client.get("/privacy").data.decode()
    assert "&lsaquo; Home</a>" in page and '<h1 class="apptitle">Privacy</h1>' in page
    assert events(tel, "page")[0]["name"] == "/privacy"


def test_every_page_links_to_privacy_in_its_footer():
    app, client, tel, _ = make()
    assert 'href="/privacy"' in client.get("/").data.decode()              # sign-in page
    assert 'href="/privacy"' in client.get("/experiments").data.decode()   # a public page
    login(client, ADMIN)
    for path in ("/", "/games", "/games/valheim", "/me", "/admin", "/apps", "/music", "/experiments", "/privacy"):
        page = client.get(path).data.decode()
        assert re.search(r'<footer class="(?:sitefoot|foot)">\s*<a [^>]*href="/privacy"', page), path     # the bridge's strip or the old footer


def test_no_recording_warning_on_the_sign_in_page_or_in_the_chat():
    app, client, tel, _ = make()
    login_page = client.get("/").data.decode()
    assert "records which pages you open" not in login_page and "Nothing is recorded" not in login_page   # the Captain's call
    assert 'href="/privacy"' in login_page                                                                # the footer link stays
    login(client, ADMIN)
    chat_page = client.get("/chat").data.decode()
    assert "are recorded for" not in chat_page and "don't type any" not in chat_page    # no warning: nothing is kept by default


def test_the_notices_are_not_shown_when_recording_is_off():
    app, client, tel, _ = make(enabled=False)
    assert "records which pages you open" not in client.get("/").data.decode()
    login(client, ADMIN)
    assert "are recorded" not in client.get("/").data.decode()


# ── the Activity widget ───────────────────────────────────────────────────────────

def summary(now=None):
    """What flask-api's /telemetry/site_summary answers (its own test pins the shape), as of `now`."""
    now = time.time() if now is None else now
    return {"generated_at": int(now), "days": 30, "retention_days": 180, "total_events": 90, "window_events": 88,
            "people": 3, "oldest": now - 12 * 86400, "newest": now - 10,
            "top_pages": [{"name": "/games", "views": 40, "people": 3}, {"name": "/games/valheim", "views": 9, "people": 2}],
            "top_taps": [{"name": "tile:games", "taps": 20}],
            "taps": [{"name": "tile:games", "taps": 20, "last_days": 0}, {"name": "tile:skippy", "taps": 1, "last_days": 9},
                     {"name": "demo:nbody", "taps": 3, "last_days": 2}],
            "actions": [{"name": "dashboard.games_start", "count": 6, "failed": 2}],
            "errors": [{"name": "404", "count": 4}],
            "recent": [{"ts": now - 90, "user": "pal@example.com", "role": "friend", "kind": "page", "name": "/games", "detail": {}},
                       {"ts": now - 4000, "user": "pal@example.com", "role": "friend", "kind": "chat", "name": "send",
                        "detail": {"message": "how do I join?", "tools": ["server_status"]}}]}


def test_the_dashboard_shows_the_recorders_health_before_home_has_sent_anything():
    app, client, tel, _ = make()
    login(client, ADMIN)
    client.get("/games")
    page = client.get("/me").data.decode()
    assert "<h3>Activity</h3>" in page and "never collected" in page
    assert "No summary from home yet" in page and "/games" in page          # what the portal still holds is shown


def test_the_dashboard_shows_what_people_did_the_least_used_tiles_and_the_health_of_the_recorder(monkeypatch):
    monkeypatch.setattr(telemetry_web, "known_taps", lambda: ["tile:games", "tile:skippy", "tile:bilby"])
    app, client, tel, _ = make()
    home = app.extensions["home"]
    home.accept({"status": {}, "site_activity": summary()})
    tel.record("page", "/x", user="a@x.com")
    tel.since(tel.boot, 0)
    login(client, ADMIN)
    page = client.get("/me").data.decode()
    activity = page[page.index('id="activity"'):]
    activity = activity[:activity.index("Watcher")]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", activity))
    assert "88 events" in text and "3 people" in text and "last 30 days" in text and "Recording for 12 days" in text
    assert "/games 40 views · 3 people" in text and "tile:games 20 taps" in text
    assert "dashboard.games_start 6 · 2 failed" in text and "HTTP 404 4" in text
    assert "1 waiting · 0 dropped" in text and "last collected 0 s ago" in text
    assert "pal asked Skippy: how do I join? [server_status] 1 h ago" in text and "pal /games 1 min ago" in text
    least = text[text.index("Least-used tiles"):]
    assert least.index("tile:bilby") < least.index("tile:skippy") < least.index("tile:games")   # untouched first
    assert "tile:bilby 0 taps · never" in least and "tile:skippy 1 tap · 9d ago" in least and "tile:games 20 taps · today" in least


def test_the_widget_lists_the_untapped_tiles_first_and_the_longest_untapped_of_a_tie_first(monkeypatch):
    monkeypatch.setattr(telemetry_web, "known_taps", lambda: ["tile:games", "tile:skippy", "tile:bilby", "tile:admin", "demo:nbody"])
    view = telemetry_web.activity_view(tel_mod.Telemetry(), {"taps": [
        {"name": "tile:games", "taps": 5, "last_days": 0}, {"name": "tile:skippy", "taps": 1, "last_days": 3},
        {"name": "tile:bilby", "taps": 1, "last_days": 40}, {"name": "gone:tile", "taps": 0, "last_days": 1}]}, now=1000.0)
    assert [(r["name"], r["taps"]) for r in view["least_used"]] == [
        ("demo:nbody", 0), ("tile:admin", 0), ("tile:bilby", 1), ("tile:skippy", 1), ("tile:games", 5)]
    assert [r["last_days"] for r in view["least_used"]] == [None, None, 40, 3, 0]      # a tile that no longer exists isn't listed


def test_the_widget_lists_only_the_eight_least_used():
    view = telemetry_web.activity_view(tel_mod.Telemetry(), {"taps": []}, now=1000.0)
    assert len(view["least_used"]) == 8 and all(r["taps"] == 0 for r in view["least_used"])


def test_a_summary_from_outside_is_data_not_markup_and_a_broken_one_cannot_break_the_page():
    app, client, tel, _ = make()
    hostile = {"days": "x", "people": None, "oldest": "yesterday", "window_events": [1],
               "top_pages": [{"name": "<script>alert(1)</script>", "views": "many", "people": {}}, "junk", 7],
               "taps": [None, {"name": ["a"], "taps": "lots"}], "actions": "nope", "errors": [{"name": "<b>x</b>"}],
               "recent": [{"ts": "soon", "user": None, "kind": 9, "name": {"a": 1}, "detail": "str"}, 3]}
    app.extensions["home"].accept({"status": {}, "site_activity": hostile})
    login(client, ADMIN)
    res = client.get("/me")
    assert res.status_code == 200 and "<script>alert(1)</script>" not in res.data.decode()
    assert "&lt;script&gt;" in res.data.decode()


def test_the_activity_widget_is_for_the_captain_only():
    app, client, tel, _ = make()
    login(client, PAL)
    assert client.get("/me").status_code == 403
    login(client, CORE)
    assert client.get("/me").status_code == 403


def test_the_widget_says_when_recording_is_off_or_home_has_gone_quiet():
    app, client, tel, _ = make(enabled=False)
    login(client, ADMIN)
    assert "Recording is switched off" in client.get("/me").data.decode()
    now = [10_000.0]
    tel2 = tel_mod.Telemetry(clock=lambda: now[0])
    tel2.since(tel2.boot, 0)
    now[0] += 600
    view = telemetry_web.activity_view(tel2, None, now=now[0])
    assert view["state"] == "stale" and view["last_collected_s"] == 600


def test_home_keeps_the_summary_it_was_sent_and_nothing_else_of_the_wrong_type():
    app, *_ = make()
    home = app.extensions["home"]
    assert home.site_activity() is None
    home.accept({"status": {}, "site_activity": "nonsense"})
    assert home.site_activity() is None
    home.accept({"status": {}, "site_activity": {"people": 2}})
    assert home.site_activity() == {"people": 2}
