"""make_image: Gemini first (the Captain's free key), skippy-pc's SDXL when Gemini refuses or fails, the Captain only, the
picture back as an image card; and the persona's phone-era tags ([IMAGE: ...], [SKIPPY_LEARN: ...]) never reach a reply."""
import base64
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import chat  # noqa: E402
import chat_tools  # noqa: E402
import image_gen  # noqa: E402
import media as media_mod  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class Http:
    def __init__(self, reply):
        self.reply, self.sent = reply, None

    def post(self, url, json=None, timeout=None, headers=None):
        self.sent = (url, json, headers)
        return self.reply


def picture(mime="image/png"):
    return {"candidates": [{"content": {"parts": [{"text": "here"}, {"inlineData": {"mimeType": mime,
                                                                                  "data": base64.b64encode(PNG).decode()}}]},
                            "finishReason": "STOP"}]}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("GEMINI_FREE_API_KEY", "g-free")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_gemini_returns_the_picture_and_is_asked_for_an_image():
    http = Http(Reply(200, picture()))
    data, mime = image_gen.generate("a dog", 1216, 832, http=http)
    url, body, headers = http.sent
    assert data == PNG and mime == "image/png" and headers["x-goog-api-key"] == "g-free"
    assert url.endswith(f"/models/{image_gen.model()}:generateContent") and "IMAGE" in body["generationConfig"]["responseModalities"]
    assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "3:2"


@pytest.mark.parametrize("reply, says", [
    (Reply(200, {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}), "blocked"),
    (Reply(200, {"candidates": [{"finishReason": "IMAGE_SAFETY", "content": {"parts": []}}]}), "IMAGE_SAFETY"),
    (Reply(200, {"candidates": [{"content": {"parts": [{"text": "I can't draw that."}]}, "finishReason": "STOP"}]}), "didn't draw"),
    (Reply(200, {"candidates": []}), "no picture"),
    (Reply(429, {}), "quota"),
    (Reply(500, {}), "500"),
])
def test_every_way_gemini_can_say_no_is_a_refusal(reply, says):
    with pytest.raises(image_gen.Refused, match=says):
        image_gen.generate("a dog", http=Http(reply))


def test_without_a_key_gemini_is_not_ready(monkeypatch):
    monkeypatch.delenv("GEMINI_FREE_API_KEY")
    assert not image_gen.ready()
    with pytest.raises(image_gen.Refused):
        image_gen.generate("a dog", http=Http(Reply(200, picture())))


class Home:
    def __init__(self, outcome):
        self.outcome, self.started = outcome, []

    def start(self, kind, owner="", **payload):
        self.started.append((kind, payload))
        return "job1"

    def owner_of(self, job):
        return "captain@example.com"

    def wait_result(self, job, wait):
        return self.outcome


def tools(home=None, gemini=None, admin=True):
    return chat_tools.ChatTools(None, is_admin=admin, who="captain@example.com", home_image=home, gemini_image=gemini)


def test_gemini_first_and_the_card_says_who_drew_it():
    home = Home({"state": "done", "result": {"title": "x"}})
    t = tools(home, gemini=lambda p, w, h: {"kind": "image", "job_id": "g1", "url": "/chat/media/g1", "title": p, "by": "Gemini"})
    out = json.loads(t.run("make_image", {"prompt": "a dog"}))
    assert out["by"] == "Gemini" and home.started == [] and t.media[0]["by"] == "Gemini"


def test_a_gemini_refusal_falls_back_to_the_pcs_sdxl_and_says_so():
    def refuse(p, w, h):
        raise image_gen.Refused("Gemini didn't draw it (IMAGE_SAFETY)")
    home = Home({"state": "done", "result": {"title": "a dog"}})
    t = tools(home, gemini=refuse)
    out = json.loads(t.run("make_image", {"prompt": "a dog", "orientation": "portrait"}))
    assert home.started == [("make_image", {"prompt": "a dog", "width": 832, "height": 1216})]
    assert out["status"] == "done" and "IMAGE_SAFETY" in out["gemini"] and t.media[0]["kind"] == "image"


def test_no_gemini_and_no_home_is_said_plainly():
    def refuse(p, w, h):
        raise image_gen.Refused("Gemini's free quota is used up for now")
    with pytest.raises(chat_tools.ToolError, match="quota.*image studio isn't reachable"):
        tools(None, gemini=refuse).run("make_image", {"prompt": "a dog"})


def test_only_the_captain_gets_make_image():
    home = Home({"state": "done", "result": {}})
    assert "make_image" in tools(home).available()
    assert "make_image" not in tools(home, gemini=lambda *a: {}, admin=False).available()


def test_the_portal_holds_pictures_for_the_chat():
    m = media_mod.Media()
    assert m.put("k", PNG, "image/png", "a@b")["bytes"] == len(PNG)
    with pytest.raises(media_mod.MediaError):
        m.put("k2", b"x", "image/svg+xml", "a@b")              # never an svg (it could carry script)


@pytest.mark.parametrize("raw, clean", [
    ('snorts The only model here is me.\n\n[SKIPPY_LEARN: {"phrase": "model", "note": "a \'model\' now."}]', "snorts The only model here is me."),
    ("But fine...\n[IMAGE: a dog with a leash]", "But fine..."),
    ('[SKIPPY_LEARN: {"a": [1, 2]}]\nreal answer', "real answer"),
    ("Keep [this], [a link](https://x) and `code[0]`.", "Keep [this], [a link](https://x) and `code[0]`."),
])
def test_phone_era_tags_never_reach_a_reply(raw, clean):
    assert chat.strip_sentinels(raw) == clean
