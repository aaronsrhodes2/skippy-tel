"""Skippy's chat asks skippy-core's Mentat straight over its Funnel (mentat_client.Mentat.ask_mentat): the trace id and caller
travel with the question, a keywords-only or down Mentat is not offered, and a failure is an error, never a made-up answer."""
import json
import urllib.error
import urllib.request

import pytest

import mentat_client


class Reply:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self, *_):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def wire(monkeypatch):
    sent, answers = [], {}

    def urlopen(req, timeout=None):
        sent.append({"url": req.full_url, "timeout": timeout, "body": json.loads(req.data) if req.data else None,
                     "headers": {k.lower(): v for k, v in req.header_items()}})
        got = answers[req.full_url.rsplit("/", 1)[1]]
        if isinstance(got, Exception):
            raise got
        return Reply(got)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent, answers


def test_a_question_goes_straight_to_mentat_with_its_trace(wire):
    sent, answers = wire
    answers["ask"] = {"ok": True, "result": {"value": 203.8, "units": "m"}}
    m = mentat_client.Mentat("https://skippy-core.example:8443/")
    assert m.ask_mentat("how far?", given={"speed_m_s": 30}, trace="chat-abc") == {"value": 203.8, "units": "m"}
    req, = sent
    assert req["url"] == "https://skippy-core.example:8443/ask" and req["timeout"] == mentat_client.ASK_TIMEOUT
    assert req["body"] == {"question": "how far?", "given": {"speed_m_s": 30}}
    assert req["headers"]["x-skippy-trace"] == "chat-abc" and req["headers"]["x-skippy-caller"] == "portal-chat"


def test_a_refusal_or_an_outage_is_an_error(wire):
    _, answers = wire
    m = mentat_client.Mentat("http://m")
    answers["ask"] = {"ok": False, "error": "Qwen is unreachable"}
    with pytest.raises(RuntimeError, match="Qwen"):
        m.ask_mentat("q")
    answers["ask"] = urllib.error.URLError("timed out")
    with pytest.raises(TimeoutError):
        m.ask_mentat("q")


def test_only_a_ready_mentat_thinking_with_qwen_is_offered(wire):
    _, answers = wire
    for health, ok in (({"ready": True, "mode": "llm"}, True), ({"ready": True, "mode": "keywords"}, False),
                       ({"ready": False, "mode": "llm"}, False), (urllib.error.URLError("refused"), False)):
        answers["health"] = health
        assert mentat_client.Mentat("http://m").ready() is ok
