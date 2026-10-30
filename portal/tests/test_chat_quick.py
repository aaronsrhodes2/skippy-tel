"""The quick spoken line (POST /chat/quick): in Hands-off with the agent, Skippy Core's resident chat model says one line at once.

2026-10-29: of the 68 s the Captain waited for a spoken answer, 64 were the agent's own tool calls; the voice started in 0.94 s.
"""
from test_app import ADMIN, FRIEND, env, login  # noqa: F401  (env is a fixture)


def test_the_captain_gets_one_line_from_core_with_the_recent_turns(env):
    client, _, skippy = env
    login(client, ADMIN)
    r = client.post("/chat/quick", json={"message": "is the bridge up?", "context": [
        {"role": "assistant", "text": "hello"},                                 # a leading reply is dropped: the conversation starts with him
        {"role": "user", "text": "check the voice"}, {"role": "assistant", "text": "F5 is warm."},
        {"role": "user", "text": "(heard) something"}]})
    assert r.status_code == 200 and r.get_json()["say"] == "Obviously."
    system, messages = skippy.seen
    assert "Never say you did" in system and skippy.model
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]       # two of his in a row are one turn
    assert messages[-1]["content"].endswith("is the bridge up?") and skippy.tools is None


def test_long_replies_are_one_short_line(env):
    client, _, skippy = env
    skippy.reply = lambda *a, **k: "word " * 300
    login(client, ADMIN)
    assert len(client.post("/chat/quick", json={"message": "hi"}).get_json()["say"]) <= 400


def test_nobody_else_and_nothing_empty(env):
    client, _, _ = env
    assert client.post("/chat/quick", json={"message": "hi"}).status_code in (302, 401, 403)
    login(client, FRIEND)
    assert client.post("/chat/quick", json={"message": "hi"}).status_code == 403
    login(client, ADMIN)
    assert client.post("/chat/quick", json={"message": "  "}).status_code == 400
