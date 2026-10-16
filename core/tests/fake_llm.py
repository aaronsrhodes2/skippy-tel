"""A fake llama-swap for the shim's tests: OpenAI chat completions (stream and not), /running, unload, and a /v1
endpoint that echoes. It records every request it gets."""
import json
import threading

from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server


class FakeLLM:
    def __init__(self):
        self.calls, self.running, self.reply, self.tool_calls = [], [], "Hello, Captain.", None
        app = Flask("fake_llm")

        @app.post("/v1/chat/completions")
        def completions():
            body = request.get_json()
            self.calls.append(("chat", body))
            if body["model"] == "broken":
                return jsonify(error="boom"), 500
            if not body.get("stream"):
                msg = {"role": "assistant", "content": self.reply, "reasoning_content": "hmm"}
                if self.tool_calls:
                    msg["tool_calls"] = self.tool_calls
                return jsonify(choices=[{"message": msg, "finish_reason": "stop"}],
                               usage={"prompt_tokens": 7, "completion_tokens": 3})

            def sse():
                for word in self.reply.split(" "):
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": word + " "}}]}) + "\n\n"
                if self.tool_calls:
                    yield "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
                        {"index": 0, "function": {"name": "look", "arguments": '{"a": '}}]}}]}) + "\n\n"
                    yield "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
                        {"index": 0, "function": {"arguments": "1}"}}]}}]}) + "\n\n"
                yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
                yield "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}) + "\n\n"
                yield "data: [DONE]\n\n"
            return Response(sse(), mimetype="text/event-stream")

        @app.post("/v1/messages")
        def messages():
            self.calls.append(("messages", request.get_json(), dict(request.headers)))
            return jsonify(type="message", content=[{"type": "text", "text": "anthropic ok"}])

        @app.get("/running")
        def running():
            return jsonify(running=self.running)

        @app.post("/api/models/unload/<slot>")
        def unload(slot):
            self.calls.append(("unload", slot))
            return "OK"

        @app.get("/upstream/<slot>/health")
        def health(slot):
            self.calls.append(("load", slot))
            return jsonify(status="ok")

        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
