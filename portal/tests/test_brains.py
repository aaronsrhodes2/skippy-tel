"""brains.py: who may use which model (guests: Skippy Core only; paid models: the Captain only), when a brain counts as
available, and the OpenAI-style adapter (Gemini) running the same tool loop as Claude's."""
import json

import pytest

import brains


def test_a_guest_has_exactly_skippy_core_and_cannot_ask_for_another():
    assert [b.id for b in brains.choices(False)] == ["core"]
    assert brains.pick(None, False).id == "core" and brains.pick("core", False).id == "core"
    for asked in ("haiku", "sonnet", "opus", "gemini", "gemini-free", "anything"):
        with pytest.raises(PermissionError):
            brains.pick(asked, False)


def test_the_captain_picks_any_brain_and_defaults_to_skippy_core():
    assert [b.id for b in brains.choices(True)] == ["core", "haiku", "sonnet", "opus", "gemini-free", "gemini"]
    assert brains.pick("", True).id == "core" and brains.pick("opus", True).model == "claude-opus-5-5"
    with pytest.raises(PermissionError):
        brains.pick("gpt-9", True)


def test_every_paid_brain_is_admin_only_and_barney_is_skippy_core():
    assert all(b.admin_only for b in brains.BRAINS.values() if b.paid)
    assert brains.BARNEY == brains.DEFAULT == "core" and not brains.BRAINS["core"].paid


def test_a_brain_is_ready_only_with_its_key_and_base(monkeypatch):
    for v in ("SKIPPY_CORE_URL", "SKIPPY_CORE_API_KEY", "GEMINI_FREE_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    core = brains.BRAINS["core"]
    assert not core.ready()
    monkeypatch.setenv("SKIPPY_CORE_API_KEY", "sk-core-SECRET")
    assert not core.ready()                                         # a key with nowhere to send it
    monkeypatch.setenv("SKIPPY_CORE_URL", "https://core.example/")
    assert core.ready() and core.base_url() == "https://core.example"
    monkeypatch.setenv("GEMINI_FREE_API_KEY", "unset")              # bootstrap's placeholder for a skipped secret
    assert not brains.BRAINS["gemini-free"].ready()
    assert "SECRET" not in json.dumps(core.view())                       # the picker never sees a key


class FakeHttp:
    """Gemini's /chat/completions: first asks for ask_mentat, then answers with what the tool said."""

    def __init__(self, status=200):
        self.status, self.bodies = status, []

    def post(self, url, json=None, timeout=None, headers=None):
        self.bodies.append((url, json, headers))
        status = self.status

        class R:
            status_code = status

            @staticmethod
            def json():
                last = json["messages"][-1]
                if last["role"] == "tool":
                    return {"choices": [{"message": {"content": f"Mentat says {last['content']}"}}]}
                return {"choices": [{"message": {"content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "ask_mentat", "arguments": '{"question": "c?"}'}}]}}]}
        return R()


def test_the_gemini_adapter_runs_the_tool_loop(monkeypatch):
    monkeypatch.setenv("GEMINI_FREE_API_KEY", "g-key")
    http = FakeHttp()
    bot = brains.OpenAIChat(brains.BRAINS["gemini-free"], session=http)
    tools = [{"name": "ask_mentat", "description": "physics", "input_schema": {"type": "object", "properties": {}}}]
    ran = []
    text = bot.reply("sys", [{"role": "user", "content": "speed of light?"}], tools=tools,
                     run_tool=lambda n, a: ran.append((n, a)) or "299792458 m/s")
    assert text == "Mentat says 299792458 m/s" and ran == [("ask_mentat", {"question": "c?"})]
    url, body, headers = http.bodies[0]
    assert url == brains.GEMINI_BASE + "/chat/completions" and headers["Authorization"] == "Bearer g-key"
    assert body["messages"][0] == {"role": "system", "content": "sys"} and body["tools"][0]["function"]["name"] == "ask_mentat"


def test_the_gemini_adapter_turns_http_errors_into_chat_errors(monkeypatch):
    import chat
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    for status, words in ((429, "rate-limited"), (401, "missing or invalid"), (500, r"error \(500\)")):
        with pytest.raises(chat.ChatError, match=words):
            brains.OpenAIChat(brains.BRAINS["gemini"], session=FakeHttp(status)).reply("s", [{"role": "user", "content": "x"}])
