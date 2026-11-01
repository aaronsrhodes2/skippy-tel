"""What he said in a room is kept until it has a real answer (the Captain, 2026-11-01): Skippy Core going away mid-answer (a dropped
connection, a 5xx, an empty reply) is waited out and the same call made again; a tool that already ran never runs twice."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from types import SimpleNamespace as NS

import pytest

import brain as brain_mod
from brain import Brain
from timers import Timers


class Model:
    """Skippy Core's Messages API, killable: while `down` it cuts each connection (or answers 503, or nothing)."""

    def __init__(self, rounds):
        self.down, self.calls, self.rounds, self.down_for = "drop", 0, list(rounds), 10 ** 9
        model = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                model.calls += 1
                if model.down and model.calls > model.down_for:
                    model.down = None                          # it is back up
                if model.down == "drop":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                if model.down == "503":
                    return self._send(503, {"type": "error", "error": {"type": "api_error", "message": "swapping"}})
                content = [] if model.down == "empty" else model.rounds.pop(0)
                self._send(200, {"id": "m", "type": "message", "role": "assistant", "model": "chat", "stop_reason": "end_turn",
                                 "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}, "content": content})

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


def make(model):
    anthropic = pytest.importorskip("anthropic")
    client = anthropic.Anthropic(api_key="k", base_url=model.url, max_retries=0, timeout=5)
    clock = [1000.0]
    timers = Timers(clock=lambda: clock[0])
    return Brain(client, "chat", timers, persona="P", clock=lambda: clock[0], now=lambda: "Sunday", sleep=lambda s: time.sleep(0.05)), timers


@pytest.mark.parametrize("how", ["drop", "503", "empty"])
def test_core_going_down_mid_answer_is_asked_again_and_he_gets_the_real_answer(how):
    model = Model([[{"type": "text", "text": "Still here, monkey."}]])
    model.down, model.down_for = how, 2                    # down for two calls, then back
    b, _ = make(model)
    try:
        assert b.reply("are you there") == "Still here, monkey."
        assert model.calls == 3                            # it really was down, and was asked again
    finally:
        model.server.shutdown()


def test_a_tool_that_ran_before_the_model_went_away_never_runs_twice():
    model = Model([[{"type": "tool_use", "id": "t1", "name": "set_timer", "input": {"seconds": 60, "label": "tea"}}],
                   [{"type": "text", "text": "Tea in a minute."}]])
    model.down = None
    b, timers = make(model)
    real = b.client.messages.create
    state = {"n": 0}

    def flaky(**kw):                                      # the second call (after the timer was set) hits a dead model once
        state["n"] += 1
        if state["n"] == 2:
            model.down = "drop"
            threading.Timer(0.3, lambda: setattr(model, "down", None)).start()
        return real(**kw)
    b.client = NS(messages=NS(create=flaky))
    try:
        assert b.reply("tea timer, one minute") == "Tea in a minute."
        assert timers.remaining() == [("tea", 60)] and b.retries >= 1
    finally:
        model.server.shutdown()


def test_when_it_stays_away_the_room_hears_so_instead_of_waiting_forever(monkeypatch):
    monkeypatch.setattr(brain_mod, "RETRY_FOR_S", 0.3)
    model = Model([])
    b, _ = make(model)
    try:
        with pytest.raises(Exception) as caught:
            b.reply("hello")
        assert brain_mod.transient(caught.value)          # the hub says "My brain is not answering"
    finally:
        model.server.shutdown()


def test_a_bug_is_not_retried():
    calls = []

    def create(**kw):
        calls.append(kw)
        raise ValueError("a bug")
    b = Brain(NS(messages=NS(create=create)), "m", Timers(), persona="P", now=lambda: "x", sleep=lambda s: None)
    with pytest.raises(ValueError):
        b.reply("hi")
    assert len(calls) == 1
