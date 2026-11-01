"""Every chat prompt is kept until it has a real answer (prompts.py, the Captain 2026-11-01): the model going away mid-answer (a dropped
connection, a 5xx, an empty reply) is retried with backoff, never shown as the reply, and the same prompt id sent again (a reload) joins
the turn or gets the answer already recorded."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")
pytest.importorskip("anthropic")

from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

import app as portal  # noqa: E402
import chat  # noqa: E402
import prompts as prompts_mod  # noqa: E402


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(prompts_mod, "BACKOFF_S", (0.2,))
    monkeypatch.setattr(prompts_mod, "RETRY_FOR_S", 20.0)


class Model:
    """A stand-in for Skippy Core's llama-swap (Anthropic's Messages API) that can be taken down: while down it cuts every connection
    without a reply (a model killed mid-answer), or answers 503 / an empty reply, as told."""

    def __init__(self):
        self.down, self.calls, self.answer = "drop", 0, "Back from the dead, monkey."
        model = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                model.calls += 1
                if model.down == "drop":
                    self.connection.shutdown(socket.SHUT_RDWR)        # mid-answer: the socket just goes
                    return
                if model.down == "503":
                    return self._send(503, {"type": "error", "error": {"type": "api_error", "message": "loading"}})
                text = "" if model.down == "empty" else model.answer
                self._send(200, {"id": "m1", "type": "message", "role": "assistant", "model": "chat", "stop_reason": "end_turn",
                                 "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
                                 "content": [{"type": "text", "text": text}] if text else []})

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

    def up_after(self, seconds):
        threading.Timer(seconds, lambda: setattr(self, "down", None)).start()


@pytest.fixture
def model():
    m = Model()
    yield m
    m.server.shutdown()


def _client(model):
    bot = chat.SkippyChat("chat", api_key="k", base_url=model.url, label="Skippy Core")
    bot._client_obj = __import__("anthropic").Anthropic(api_key="k", base_url=model.url, max_retries=0, timeout=5)
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=bot)
    client = app.test_client()
    login(client, FRIEND)
    return app, client


@pytest.mark.parametrize("how", ["drop", "503", "empty"])
def test_the_model_going_down_mid_answer_is_retried_and_the_same_prompt_is_answered_when_it_is_back(model, how):
    model.down = how
    model.up_after(1.0)
    app, client = _client(model)
    r = client.post("/chat/send", json={"message": "are you there?", "prompt_id": "p-test-0001"})
    d = r.get_json()
    assert r.status_code == 200, d
    assert d["reply"] == "Back from the dead, monkey."              # the real answer, never the failure
    assert d["attempts"] >= 1 and model.calls >= 2
    assert app.extensions["chat_prompts"].get("p-test-0001", FRIEND).state == "answered"


def test_a_resend_of_an_answered_prompt_gets_the_recorded_answer_and_runs_nothing_again(model):
    model.down = None
    _, client = _client(model)
    first = client.post("/chat/send", json={"message": "hello", "prompt_id": "p-test-0002"}).get_json()
    calls = model.calls
    again = client.post("/chat/send", json={"message": "hello", "prompt_id": "p-test-0002"}).get_json()   # a reload
    assert again["reply"] == first["reply"] and model.calls == calls


def test_a_reload_while_it_is_retrying_joins_the_same_turn_and_sees_it_waiting(model):
    model.down = "drop"
    app, client = _client(model)
    out = {}
    t = threading.Thread(target=lambda: out.update(first=client.post("/chat/send", json={"message": "hi", "prompt_id": "p-test-0003"}).get_json()))
    t.start()
    for _ in range(100):
        p = app.extensions["chat_prompts"].get("p-test-0003", FRIEND)
        if p is not None and p.attempts:
            break
        time.sleep(0.05)
    other = app.test_client()
    login(other, FRIEND)
    view = other.get("/chat/pending/p-test-0003").get_json()
    assert view["state"] == "pending" and view["attempts"] >= 1 and "reach" in view["why"]   # the card says: waiting, retrying
    joined = {}
    j = threading.Thread(target=lambda: joined.update(d=other.post("/chat/send", json={"message": "hi", "prompt_id": "p-test-0003"}).get_json()))
    j.start()
    time.sleep(0.3)
    model.down = None
    t.join(15)
    j.join(15)
    assert out["first"]["reply"] == joined["d"]["reply"] == "Back from the dead, monkey."
    stranger = app.test_client()
    login(stranger, ADMIN)
    assert stranger.get("/chat/pending/p-test-0003").get_json()["state"] == "unknown"     # never someone else's prompt


def test_when_the_window_runs_out_it_says_still_pending_not_an_answer(model, monkeypatch):
    monkeypatch.setattr(prompts_mod, "RETRY_FOR_S", 0.5)
    model.down = "503"
    _, client = _client(model)
    r = client.post("/chat/send", json={"message": "hi", "prompt_id": "p-test-0004"})
    d = r.get_json()
    assert r.status_code == 202 and d["pending"] is True and "reply" not in d
    model.down = None                                                 # it comes back; the page sends the same id again
    d = client.post("/chat/send", json={"message": "hi", "prompt_id": "p-test-0004"}).get_json()
    assert d["reply"] == "Back from the dead, monkey."


def test_a_retry_never_runs_a_tool_twice():
    vm, bot, tries, outs = FakeVm(), FakeSkippy(), {"n": 0}, []
    counted = {"n": 0}
    status = vm.status
    vm.status = lambda: counted.__setitem__("n", counted["n"] + 1) or status()

    def reply(system, messages, tools=None, run_tool=None, model=None, force_tool=None):
        outs.append(run_tool("start_game", {"games": ["valheim"]}))     # a side effect: it must happen once
        tries["n"] += 1
        if tries["n"] < 3:
            raise chat.Dropped("couldn't reach Skippy Core")
        return "Started, monkey."
    bot.reply = reply
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=bot)
    client = app.test_client()
    login(client, ADMIN)
    d = client.post("/chat/send", json={"message": "start valheim", "prompt_id": "p-test-0005"}).get_json()
    assert d["reply"] == "Started, monkey." and tries["n"] == 3
    assert len([c for c in vm.calls if c[0] == "start"]) == 1 and len(set(outs)) == 1


def test_a_final_refusal_is_not_retried():
    bot = FakeSkippy()
    n = {"c": 0}

    def reply(*a, **k):
        n["c"] += 1
        raise chat.ChatError("The portal's key for Skippy Core is missing or invalid.")
    bot.reply = reply
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=bot)
    client = app.test_client()
    login(client, ADMIN)
    r = client.post("/chat/send", json={"message": "hi", "prompt_id": "p-test-0006"})
    assert r.status_code == 502 and n["c"] == 1


def test_the_agents_retry_event_reaches_the_chat_and_keeps_the_count_in_step():
    import workshop
    assert workshop._clean({"t": "retry", "attempt": 2, "why": "API Error: Connection error.", "secret": "x"}) == \
        {"t": "retry", "attempt": 2, "why": "API Error: Connection error."}           # never dropped: the relay counts events
    page = (portal.Path(portal.__file__).parent / "templates" / "_workshop.html").read_text(encoding="utf-8") \
        if hasattr(portal, "Path") else open(portal.__file__.replace("app.py", "templates/_workshop.html"), encoding="utf-8").read()
    assert 'p.t === "retry"' in page and "Waiting for Skippy, retrying" in page


def test_the_chat_page_keeps_the_prompt_and_resumes_it_after_a_reload():
    page = open(portal.__file__.replace("app.py", "templates/_chat.html"), encoding="utf-8").read()
    assert "prompt_id: pid" in page and "keepPending(" in page and "setTimeout(resumePending, 0)" in page
    assert "Waiting for Skippy, retrying" in page and "ask(rec.text, {pid: rec.pid})" in page
