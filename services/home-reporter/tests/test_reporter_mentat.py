"""A physics job on the reporter's side: the question goes to Mentat at home, with the values the person said yes to
(`given`, from Mentat's own suggestion) and the chat turn's trace id, which Mentat keeps in its research record."""
import json
import urllib.request

import pytest

import reporter


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
def mentat(monkeypatch):
    sent = []

    def urlopen(req, timeout=None):
        sent.append({"url": req.full_url, "body": json.loads(req.data), "headers": {k.lower(): v for k, v in req.header_items()}})
        return Reply({"ok": True, "result": {"value": 203.8, "units": "m"}})
    monkeypatch.setattr(reporter, "MENTAT", "http://mentat-home:8080")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent


def test_the_question_goes_with_the_trace_id(mentat):
    out = reporter.run_job({"id": "j1", "kind": "ask_mentat", "question": "how far?", "trace": "chat-abc"})
    req, = mentat
    assert out == {"result": {"value": 203.8, "units": "m"}}
    assert req["url"] == "http://mentat-home:8080/ask" and req["body"] == {"question": "how far?"}
    assert req["headers"]["x-skippy-trace"] == "chat-abc" and req["headers"]["x-skippy-caller"] == "portal-chat"


def test_agreed_values_go_as_given_and_a_job_without_a_trace_uses_its_id(mentat):
    reporter.run_job({"id": "j2", "kind": "ask_mentat", "question": "how far?", "given": {"speed_m_s": 30.0}})
    req, = mentat
    assert req["body"] == {"question": "how far?", "given": {"speed_m_s": 30.0}}
    assert req["headers"]["x-skippy-trace"] == "j2"
