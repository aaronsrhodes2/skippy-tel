"""The portal's copy of Skippy Core's eyes (eyes_client.py): the request it sends to the vision slot, and every way it can fail as one RuntimeError."""
import base64
import json

import pytest

import eyes_client as eyes

PIC = b"\xff\xd8\xff\xe0" + b"p" * 20


def test_it_sends_the_picture_and_the_question_to_the_vision_slot_with_thinking_off():
    sent = {}

    def post(req):
        sent.update(url=req.full_url, headers=dict(req.header_items()), body=json.loads(req.data))
        return {"content": [{"type": "text", "text": "[ ]"}, {"type": "text", "text": "ok"}]}
    assert eyes.ask("https://core.example/", "k-9", PIC, "image/jpeg", "what is this?", post=post) == "[ ] ok"
    assert sent["url"] == "https://core.example/v1/messages" and sent["headers"]["X-api-key"] == "k-9"
    body = sent["body"]
    assert body["model"] == "vision" and body["chat_template_kwargs"] == {"enable_thinking": False} and body["max_tokens"] == 600
    image, text = body["messages"][0]["content"]
    assert image["source"] == {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(PIC).decode()} and text["text"] == "what is this?"


def test_every_failure_is_one_runtime_error_with_a_reason():
    with pytest.raises(RuntimeError, match="isn't configured"):
        eyes.ask("", "", PIC, "image/jpeg", "q")
    with pytest.raises(RuntimeError, match="said nothing"):
        eyes.ask("https://c", "k", PIC, "image/jpeg", "q", post=lambda req: {"content": []})

    def boom(req):
        raise TimeoutError("slow")
    with pytest.raises(RuntimeError, match="TimeoutError"):
        eyes.ask("https://c", "k", PIC, "image/jpeg", "q", post=boom)
