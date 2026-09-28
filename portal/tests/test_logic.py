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
    assert out == "# keep me\nA=1\nB=22\nZ=new\n"
    assert envfile.parse(out) == {"A": "1", "B": "22", "Z": "new"}


def test_merge_into_empty():
    assert envfile.merge("", {"A": "1"}) == "A=1\n"


def test_merge_does_not_touch_commented_keys():
    assert envfile.merge("# A=old\n", {"A": "new"}) == "# A=old\nA=new\n"


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
    assert discord_admin.links({}, "valheim") == {"text": None, "voice": None}
    env = {"DISCORD_GUILD_ID": "g1", "DISCORD_CHANNEL_VALHEIM": "c1", "DISCORD_VOICE_VALHEIM": "v1"}
    assert discord_admin.links(env, "valheim") == {
        "text": "https://discord.com/channels/g1/c1",
        "voice": "https://discord.com/channels/g1/v1",
    }
    assert discord_admin.links(env, "factorio") == {"text": None, "voice": None}


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


def _chat(responses):
    import chat
    bot = chat.SkippyChat("claude-opus-5")
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
