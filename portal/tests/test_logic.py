"""Pure-logic tests: stdlib only, run anywhere."""
import envfile
import discord_admin
from catalog import GAMES, missing_settings
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
