"""Pure-logic tests: stdlib only, run anywhere."""
import envfile
import discord_admin
from catalog import GAMES, clamp_idle, missing_settings
from chat import MAX_MESSAGES, build_messages


# ── envfile ──────────────────────────────────────────────────────────────────

def test_parse_ignores_comments_and_inline_comments():
    text = "# header\nA=1\nB=two words   # note\n\nC=\n  D = x \n"
    assert envfile.parse(text) == {"A": "1", "B": "two words", "C": "", "D": "x"}


def test_merge_replaces_in_place_and_appends_new():
    text = "# keep me\nA=1\nB=2\n"
    out = envfile.merge(text, {"B": "22", "Z": "new"})
    assert out == "# keep me\nA=1\nB='22'\nZ='new'\n"
    assert envfile.parse(out) == {"A": "1", "B": "22", "Z": "new"}


def test_merge_into_empty():
    assert envfile.merge("", {"A": "1"}) == "A='1'\n"


def test_merge_does_not_touch_commented_keys():
    assert envfile.merge("# A=old\n", {"A": "new"}) == "# A=old\nA='new'\n"


# ── catalog ──────────────────────────────────────────────────────────────────

def test_valheim_needs_a_real_password():
    assert missing_settings("valheim", {})
    assert missing_settings("valheim", {"VALHEIM_PASSWORD": "change-me"})
    assert missing_settings("valheim", {"VALHEIM_PASSWORD": "abcd"})
    assert missing_settings("valheim", {"VALHEIM_PASSWORD": "SkippyRocks", "VALHEIM_SERVER_NAME": "Skippy"})
    assert missing_settings("valheim", {"VALHEIM_PASSWORD": "hunter22", "VALHEIM_SERVER_NAME": "Skippy"}) == []


def test_dragonwilds_needs_owner_and_admin_password():
    assert len(missing_settings("dragonwilds", {})) == 2
    ok = {"DRAGONWILDS_OWNER_ID": "abc", "DRAGONWILDS_ADMIN_PASSWORD": "pw123"}
    assert missing_settings("dragonwilds", ok) == []


def test_every_setting_key_is_unique():
    keys = [s.key for g in GAMES.values() for s in g.settings]
    assert len(keys) == len(set(keys))


# ── discord links ────────────────────────────────────────────────────────────

def test_links_need_guild_and_channel():
    none = {"text": None, "voice": None, "invite": None}
    assert discord_admin.links({}, "valheim") == none
    env = {"DISCORD_GUILD_ID": "g1", "DISCORD_CHANNEL_VALHEIM": "c1", "DISCORD_VOICE_VALHEIM": "v1",
           "DISCORD_INVITE_VALHEIM": "abc"}
    assert discord_admin.links(env, "valheim") == {
        "text": "https://discord.com/channels/g1/c1",
        "voice": "https://discord.com/channels/g1/v1",
        "invite": "https://discord.gg/abc",
    }
    assert discord_admin.links(env, "factorio") == none


class FakeDiscord:
    """Just enough of Discord's REST API for setup(): channels, invites,
    webhooks, pins, messages."""
    def __init__(self, token=None):
        self.channels = [{"id": "old-voice", "name": "Dragonwilds", "type": 2}]   # pre-rename voice
        self.invites, self.calls, self.n = {}, [], 0

    def __call__(self, method, path, payload=None, auth=True):
        self.calls.append((method, path))
        self.n += 1
        if path.endswith("/channels") and method == "GET":
            return list(self.channels)
        if path.endswith("/channels") and method == "POST":
            ch = {"id": f"ch{self.n}", **payload}
            self.channels.append(ch)
            return ch
        if method == "PATCH" and path.startswith("/channels/"):
            return {"name": payload["name"]}
        if path.endswith("/invites") and method == "POST":
            code = f"inv{self.n}"
            self.invites[code] = path
            return {"code": code}
        if path.startswith("/invites/"):
            if path.rsplit("/", 1)[-1] in self.invites:
                return {"guild": {"id": "g1"}}
            import urllib.error
            raise urllib.error.HTTPError(path, 404, "gone", {}, None)
        if path.endswith("/webhooks") and method == "GET":
            return []
        if path.endswith("/webhooks"):
            return {"id": f"h{self.n}", "token": "t"}
        if "?wait=true" in path:
            return {"id": f"m{self.n}"}
        return {}


def test_setup_makes_like_named_text_and_voice_channels_with_invites(monkeypatch):
    fake = FakeDiscord()
    monkeypatch.setattr(discord_admin, "Discord", lambda token: fake)
    env = {"DISCORD_GUILD_ID": "g1", "DISCORD_VOICE_DRAGONWILDS": "old-voice"}
    out = discord_admin.setup("tok", "gPTVS7Hmj", ["dragonwilds", "valheim"], env)
    for game in ("dragonwilds", "valheim"):
        key = game.upper()
        text = next(c for c in fake.channels if c["id"] == out[f"DISCORD_CHANNEL_{key}"])
        assert text["name"] == game and text["type"] == 0
        assert out[f"DISCORD_INVITE_{key}"] in fake.invites
        assert fake.invites[out[f"DISCORD_INVITE_{key}"]] == f"/channels/{text['id']}/invites"
    assert out["DISCORD_VOICE_DRAGONWILDS"] == "old-voice"            # kept, not duplicated...
    assert ("PATCH", "/channels/old-voice") in fake.calls              # ...renamed to "dragonwilds"
    valheim_voice = next(c for c in fake.channels if c["id"] == out["DISCORD_VOICE_VALHEIM"])
    assert valheim_voice["name"] == "valheim" and valheim_voice["type"] == 2

    # Re-running keeps the same invites instead of minting new ones.
    again = discord_admin.setup("tok", "gPTVS7Hmj", ["dragonwilds"], {**env, **out})
    assert again["DISCORD_INVITE_DRAGONWILDS"] == out["DISCORD_INVITE_DRAGONWILDS"]


def test_recent_messages_readable_and_cached(monkeypatch):
    raw = [  # Discord returns newest first
        {"id": "3", "content": "", "author": {"username": "Skippy", "bot": True},
         "embeds": [{"title": "🟢 RuneScape: Dragonwilds is up"}], "timestamp": "t3"},
        {"id": "2", "content": "hey <@42>, it's up", "author": {"username": "groggy", "global_name": "Captain"},
         "mentions": [{"id": "42", "username": "pal"}], "timestamp": "t2"},
        {"id": "1", "content": "", "author": {"username": "x"}, "timestamp": "t1"},   # empty: dropped
    ]
    calls = []

    class Api:
        def __init__(self, token):
            pass

        def __call__(self, method, path, payload=None, auth=True):
            calls.append(path)
            return raw
    monkeypatch.setattr(discord_admin, "Discord", Api)
    discord_admin._CACHE.clear()
    msgs = discord_admin.recent_messages("tok", "c9")
    assert [m["id"] for m in msgs] == ["2", "3"]                      # oldest first
    assert msgs[0]["author"] == "Captain" and msgs[0]["text"] == "hey @pal, it's up"
    assert msgs[1]["bot"] and msgs[1]["extra"] == ["🟢 RuneScape: Dragonwilds is up"]
    discord_admin.recent_messages("tok", "c9")
    assert len(calls) == 1                                             # served from cache


def test_portal_and_vm_describe_the_same_games():
    assert set(discord_admin.notify.GAMES) == set(GAMES)


# ── chat history ─────────────────────────────────────────────────────────────

def test_build_messages_appends_new_user_turn():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert build_messages(history, "again") == history + [{"role": "user", "content": "again"}]


def test_build_messages_drops_junk_and_fixes_alternation():
    history = [
        {"role": "assistant", "content": "leading assistant turn"},   # can't start with assistant
        {"role": "system", "content": "sneaky"},                       # not a chat role
        "not a dict",
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},                         # consecutive user: keep latest
        {"role": "assistant", "content": ""},                          # empty
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "unanswered"},                     # replaced by the new message
    ]
    assert build_messages(history, "new") == [
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "new"},
    ]


def test_build_messages_caps_length_and_starts_with_user():
    history = []
    for i in range(100):
        history += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    out = build_messages(history, "last")
    assert len(out) <= MAX_MESSAGES
    assert out[0]["role"] == "user"
    assert out[-1] == {"role": "user", "content": "last"}


def test_build_messages_tolerates_garbage_history():
    assert build_messages("nope", "hi") == [{"role": "user", "content": "hi"}]
    assert build_messages(None, "hi") == [{"role": "user", "content": "hi"}]


# ── player guardrails ────────────────────────────────────────────────────────

def test_players_cannot_disable_auto_sleep():
    assert clamp_idle("0", is_admin=False) == 15
    assert clamp_idle("9999", is_admin=False) == 120
    assert clamp_idle("45", is_admin=False) == 45
    assert clamp_idle("junk", is_admin=False) == 30
    assert clamp_idle("0", is_admin=True) == 0
    assert clamp_idle("9999", is_admin=True) == 9999


def test_minutes_up():
    from datetime import datetime, timedelta, timezone
    from gcp import VmStatus
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    s = VmStatus("RUNNING", "e2", "1.2.3.4", started_at=now - timedelta(minutes=3, seconds=30))
    assert s.minutes_up(now) == 3
    s.status = "TERMINATED"
    assert s.minutes_up(now) is None


# ── SkippyChat tool loop (fake Claude client) ─────────────────────────────────

class _Block:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, stop_reason, *content):
        self.stop_reason, self.content = stop_reason, list(content)


class _FakeClient:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.beta = self
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)


def _chat(responses, model="claude-opus-5"):
    import chat
    bot = chat.SkippyChat(model)
    bot._client_obj = _FakeClient(responses)
    return bot


def test_chat_tool_loop_runs_tools_and_returns_final_text():
    bot = _chat([
        _Resp("tool_use", _Block("text", text="Checking."),
              _Block("tool_use", id="t1", name="server_status", input={})),
        _Resp("end_turn", _Block("text", text="It's asleep, Captain.")),
    ])
    ran = []
    reply = bot.reply("sys", [{"role": "user", "content": "up?"}], tools=[{"name": "server_status"}],
                      run_tool=lambda name, args: ran.append(name) or '{"state": "asleep"}')
    assert reply == "It's asleep, Captain."
    assert ran == ["server_status"]
    second = bot._client.requests[1]["messages"]
    assert second[-1]["content"][0] == {"type": "tool_result", "tool_use_id": "t1",
                                        "content": '{"state": "asleep"}'}


def test_chat_tool_errors_go_back_to_the_model():
    bot = _chat([
        _Resp("tool_use", _Block("tool_use", id="t1", name="start_game", input={"games": ["x"]})),
        _Resp("end_turn", _Block("text", text="Can't, sorry.")),
    ])

    def fail(name, args):
        raise ValueError("needs the owner's Player ID")
    assert bot.reply("sys", [{"role": "user", "content": "go"}], tools=[{}], run_tool=fail) == "Can't, sorry."
    result = bot._client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "Player ID" in result["content"]


def test_chat_tool_loop_is_capped():
    import chat
    loop = _Resp("tool_use", _Block("tool_use", id="t", name="server_status", input={}))
    bot = _chat([loop] * chat.MAX_TOOL_ROUNDS)
    reply = bot.reply("sys", [{"role": "user", "content": "?"}], tools=[{}], run_tool=lambda n, a: "{}")
    assert "round in circles" in reply
    assert len(bot._client.requests) == chat.MAX_TOOL_ROUNDS


def test_values_with_shell_and_compose_specials_round_trip():
    tricky = {"PW": "p$ss #1 x", "EMPTY": "", "DQ": 'say "hi"'}
    assert envfile.parse(envfile.merge("", tricky)) == tricky


def test_parse_reads_quoted_and_legacy_unquoted_values():
    text = "A='lit $x #y'\nB=\"dq\"\nC=plain  # note\nD=\n"
    assert envfile.parse(text) == {"A": "lit $x #y", "B": "dq", "C": "plain", "D": ""}


def test_merge_refuses_values_compose_cant_carry():
    import pytest
    for bad in ("it's", "two\nlines"):
        with pytest.raises(ValueError):
            envfile.merge("", {"PW": bad})


# ── diagnostics ───────────────────────────────────────────────────────────────

def test_latest_snapshot_and_startup_tail():
    import diagnostics
    port2 = "=== skippy-diag a ===\nold\n=== end ===\njunk\n=== skippy-diag b ===\nnew\n"
    assert diagnostics.latest_snapshot(port2) == "=== skippy-diag b ===\nnew\n"   # still being written
    assert diagnostics.latest_snapshot("nothing") == ""
    port1 = ("kernel stuff\nstartup-script: === skippy-games boot 1 ===\nstartup-script: first\n"
             "startup-script: === skippy-games boot 2 ===\nstartup-script: pull failed\n")
    tail = diagnostics.startup_tail(port1)
    assert "pull failed" in tail and "first" not in tail and "kernel" not in tail


def test_redact_masks_only_secret_settings():
    import diagnostics
    env = {"DRAGONWILDS_WORLD_PASSWORD": "s3cret!", "DRAGONWILDS_SERVER_NAME": "SkippyScape"}
    out = diagnostics.redact("name=SkippyScape pw=s3cret!", env)
    assert out == "name=SkippyScape pw=••••"


def test_join_codes_from_diag_line_or_log_fallback():
    import diagnostics
    assert diagnostics.join_codes("JOIN_CODE dragonwilds=AB12-CD34\n") == {"dragonwilds": "AB12-CD34"}
    log = ('[..]LogNetSessionSettings: Setting ["JoinCode"] written with key[xz] value[YTKM-EKL1]\n')
    assert diagnostics.join_codes(log) == {"dragonwilds": "YTKM-EKL1"}
    assert diagnostics.join_codes("nothing here") == {}


def test_discord_errors_say_what_went_wrong():
    import io
    import urllib.error
    err = urllib.error.HTTPError("https://x", 403, "Forbidden", {},
                                 io.BytesIO(b'{"message": "Missing Access", "code": 50001}'))
    e = discord_admin.DiscordError.from_http(err, "GET", "/guilds/1/channels")
    assert e.code == 403 and isinstance(e, urllib.error.HTTPError)   # callers still branch on .code
    assert "isn't in this Discord server" in str(e)
    bare = urllib.error.HTTPError("https://discord.com/api/webhooks/1/secret", 500, "Oops", {}, io.BytesIO(b""))
    assert "secret" not in str(discord_admin.DiscordError.from_http(bare, "POST", bare.url))   # no webhook token in messages


def test_join_code_read_from_real_serial_output_with_crlf():
    import diagnostics
    port2 = ("=== skippy-diag 2026-09-29T02:43 ===\r\n--- dragonwilds: last 40 log lines\r\n"
             "[..]HeartbeatSession ok\r\nJOIN_CODE dragonwilds=RKXL-B01K\r\n\r\n=== end ===\r\n")
    assert diagnostics.join_codes(diagnostics.latest_snapshot(port2)) == {"dragonwilds": "RKXL-B01K"}


def test_fallback_params_only_for_models_that_support_them():
    done = _Resp("end_turn", _Block("text", text="Aye."))
    haiku = _chat([done], model="claude-haiku-4-5")
    assert haiku.reply("sys", [{"role": "user", "content": "hi"}]) == "Aye."
    sent = haiku._client.requests[0]
    assert sent["model"] == "claude-haiku-4-5" and "betas" not in sent and "fallbacks" not in sent
    opus = _chat([_Resp("end_turn", _Block("text", text="Aye."))])
    opus.reply("sys", [{"role": "user", "content": "hi"}])
    assert opus._client.requests[0]["fallbacks"] == "default"


# ── chat privacy: friends vs. the Captain ─────────────────────────────────────

def test_friends_get_the_privacy_rules_and_only_their_games():
    import chat
    friend = chat.system_prompt("Pal", is_admin=False, page="/games/dragonwilds",
                                games=["RuneScape: Dragonwilds"])
    assert "NOT the Captain" in friend and "calendar" in friend
    assert "/games/dragonwilds" in friend and "Valheim" not in friend
    captain = chat.system_prompt("Aaron", is_admin=True)
    assert "NOT the Captain" not in captain


def test_mcp_tools_default_to_private():
    import tool_policy as tp
    assert tp.allowed("physics", is_admin=False)
    for private in ("gmail", "calendar", "memory", "some-new-server"):
        assert not tp.allowed(private, is_admin=False)
        assert tp.allowed(private, is_admin=True)
    assert tp.allowed_servers(["physics", "gmail", "brand-new"], is_admin=False) == ["physics"]


def test_portal_skippy_is_loud_but_privacy_rules_come_after():
    import chat
    friend = chat.system_prompt("Pal")
    assert "full volume" in friend and "filthy monkeys" in friend
    assert friend.index("full volume") < friend.index("NOT the Captain")   # later rules win


# ── Discord: channels the Captain made by hand ────────────────────────────────

def test_a_games_discord_channels_can_be_named_something_other_than_its_id():
    assert discord_admin.channel_name("everquest") == "foreverquest"
    assert discord_admin.channel_name("valheim") == "valheim"


def test_setup_adopts_the_captains_foreverquest_channels_instead_of_making_new_ones(monkeypatch):
    class FakeDiscord:
        created: list = []

        def __init__(self, token):
            pass

        def __call__(self, method, path, body=None, auth=True):
            if method == "GET" and path.endswith("/channels"):
                return [{"id": "10", "name": "Game Servers", "type": 4},
                        {"id": "111", "name": "foreverquest", "type": 0, "parent_id": "10"},
                        {"id": "222", "name": "foreverquest", "type": 2, "parent_id": "10"}]
            if method == "POST" and path.endswith("/channels"):
                ch = {"id": f"new-{body['name']}-{body['type']}", **body}
                FakeDiscord.created.append(body["name"])
                return ch
            raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(discord_admin, "Discord", FakeDiscord)
    monkeypatch.setattr(discord_admin, "_ensure_webhook", lambda api, cid: f"hook-{cid}")
    monkeypatch.setattr(discord_admin, "_ensure_invite", lambda api, cid, code: f"invite-{cid}")
    monkeypatch.setattr(discord_admin, "_ensure_board", lambda api, hook, cid, game, bid, env: f"board-{cid}")
    out = discord_admin.setup("token", "invite", ["everquest", "valheim"], {"DISCORD_GUILD_ID": "G"})
    assert out["DISCORD_CHANNEL_EVERQUEST"] == "111" and out["DISCORD_VOICE_EVERQUEST"] == "222"   # adopted
    assert out["DISCORD_INVITE_EVERQUEST"] == "invite-111" and out["DISCORD_BOARD_EVERQUEST"] == "board-111"
    assert "everquest" not in FakeDiscord.created and "foreverquest" not in FakeDiscord.created  # nothing duplicated
    assert FakeDiscord.created == ["valheim", "valheim"]                                            # others are unchanged


# ── automatic setup of the game servers ───────────────────────────────────────

def _configured_env():
    return {"VALHEIM_PASSWORD": "hunter22", "DRAGONWILDS_OWNER_ID": "owner1", "DRAGONWILDS_ADMIN_PASSWORD": "adm1n-real",
            "DRAGONWILDS_WORLD_PASSWORD": "dragonpw", "DRAGONWILDS_SERVER_NAME": "SkippyScape",
            "DRAGONWILDS_WORLD_NAME": "SkippyScape"}


def test_autoconfig_makes_up_a_missing_join_password_and_leaves_what_is_set_alone():
    import autoconfig
    from catalog import GAMES, missing_settings
    env = _configured_env()
    del env["VALHEIM_PASSWORD"]
    assert missing_settings("valheim", env)                                      # the one thing that blocked Valheim
    p = autoconfig.plan(env, GAMES)
    assert "VALHEIM_PASSWORD" in p.generated and len(p.set["VALHEIM_PASSWORD"]) >= 5
    assert not missing_settings("valheim", {**env, **p.set})                     # and now it can start
    for key in ("DRAGONWILDS_OWNER_ID", "DRAGONWILDS_ADMIN_PASSWORD", "DRAGONWILDS_WORLD_PASSWORD",
                "DRAGONWILDS_SERVER_NAME", "DRAGONWILDS_WORLD_NAME"):
        assert key not in p.set, key                                             # nothing that was filled in is touched


def test_autoconfig_fills_the_defaults_the_vm_would_use_anyway_only_where_blank():
    import autoconfig
    from catalog import GAMES
    env = {**_configured_env(), "VALHEIM_SERVER_NAME": "", "FACTORIO_SAVE_NAME": "", "SATISFACTORY_MAX_PLAYERS": "8"}
    p = autoconfig.plan(env, GAMES)
    assert p.set["VALHEIM_SERVER_NAME"] == "Skippy Valheim" and p.set["FACTORIO_SAVE_NAME"] == "skippy"
    assert "SATISFACTORY_MAX_PLAYERS" not in p.set                               # someone chose 8: kept


def test_a_join_password_the_captain_names_replaces_every_games_join_password():
    import autoconfig
    from catalog import GAMES
    p = autoconfig.plan(_configured_env(), GAMES, join_password="groggy")
    assert p.set["VALHEIM_PASSWORD"] == "groggy" and p.set["DRAGONWILDS_WORLD_PASSWORD"] == "groggy"
    assert sorted(p.chosen) == ["DRAGONWILDS_WORLD_PASSWORD", "VALHEIM_PASSWORD"]
    assert "DRAGONWILDS_ADMIN_PASSWORD" not in p.set                             # the admin password isn't a join password
    again = autoconfig.plan({**_configured_env(), **p.set}, GAMES, join_password="groggy")
    assert again.set == {} and again.chosen == []                                # idempotent


def test_an_unusable_join_password_is_refused_without_echoing_it():
    import autoconfig, pytest
    from catalog import GAMES
    with pytest.raises(autoconfig.PlanError) as short:
        autoconfig.plan(_configured_env(), GAMES, join_password="abc")
    assert "at least 5" in str(short.value) and "abc" not in str(short.value)
    env = {**_configured_env(), "VALHEIM_SERVER_NAME": "Groggy Land"}
    with pytest.raises(autoconfig.PlanError) as named:
        autoconfig.plan(env, GAMES, join_password="groggy")
    assert "server name" in str(named.value)


def test_what_only_the_captain_can_supply_is_reported_not_invented():
    import autoconfig
    from catalog import GAMES
    env = _configured_env()
    del env["DRAGONWILDS_OWNER_ID"]                                              # a Player ID from inside the game
    p = autoconfig.plan(env, GAMES)
    assert "DRAGONWILDS_OWNER_ID" not in p.set
    assert [n["game"] for n in p.needs_you] == ["dragonwilds"] and "Player ID" in p.needs_you[0]["problem"]
    assert "Still needs you" in p.summary()


def test_the_summary_names_settings_but_never_shows_a_value():
    import autoconfig
    from catalog import GAMES
    env = _configured_env()
    del env["VALHEIM_PASSWORD"]
    p = autoconfig.plan(env, GAMES)
    text = p.summary()
    assert "valheim password" in text
    assert all(value not in text for value in p.set.values())


# ── mentat_client ────────────────────────────────────────────────────────────

def test_mentat_errors_say_whether_mentat_refused_or_was_offline(monkeypatch):
    import io
    import urllib.error
    import mentat_client

    def answer(status, body=b'{"ok": false, "error": "environment must be one of: vacuum"}'):
        def urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(body))
        return urlopen

    m = mentat_client.Mentat("http://mentat.test")
    for status, offline in ((400, False), (429, False), (404, True), (503, True), (504, True)):
        monkeypatch.setattr(mentat_client.urllib.request, "urlopen", answer(status))
        try:
            m.wva_solve({"environment": "mars"})
        except mentat_client.MentatError as exc:
            assert (exc.status, exc.offline) == (status, offline)
            assert str(exc) == "environment must be one of: vacuum"             # Mentat's own words
        else:
            raise AssertionError("no error")

    def unreachable(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(mentat_client.urllib.request, "urlopen", unreachable)
    try:
        m.wva_catalog()
    except mentat_client.MentatError as exc:
        assert exc.status is None and exc.offline and "unreachable" in str(exc)
    else:
        raise AssertionError("no error")
