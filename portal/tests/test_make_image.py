"""make_image: skippy-pc's image studio draws it (Gemini is gone, 2026-10-23), for everyone, guests 10 a day; the picture
comes back as an image card; a picture asked for but never drawn is drawn by the portal itself; and the persona's phone-era
tags ([IMAGE: ...], [SKIPPY_LEARN: ...]) never reach a reply."""
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import chat  # noqa: E402
import chat_tools  # noqa: E402
import media as media_mod  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


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


def tools(home=None, admin=True, allow=None):
    return chat_tools.ChatTools(None, is_admin=admin, who="captain@example.com", home_image=home, image_allow=allow)


def test_the_pcs_studio_draws_it_and_the_card_shows_it():
    home = Home({"state": "done", "result": {"title": "a dog"}})
    t = tools(home)
    out = json.loads(t.run("make_image", {"prompt": "a dog", "orientation": "portrait"}))
    assert home.started == [("make_image", {"prompt": "a dog", "width": 832, "height": 1216})]
    assert out["status"] == "done" and t.media[0]["kind"] == "image" and t.media[0]["url"] == "/chat/media/job1"


def test_no_studio_no_tool():
    assert "make_image" not in tools(None).available()


def test_guests_get_pictures_until_the_days_limit():
    home = Home({"state": "done", "result": {}})
    left = [1]

    def allow():
        left[0] -= 1
        return left[0] >= 0
    t = tools(home, admin=False, allow=allow)
    assert "make_image" in t.available()
    json.loads(t.run("make_image", {"prompt": "a dog"}))
    with pytest.raises(chat_tools.ToolError, match="10 a day"):
        t.run("make_image", {"prompt": "a cat"})
    assert len(home.started) == 1


@pytest.mark.parametrize("ask, wanted", [
    ("draw me a dragon on a skateboard", True),
    ("Can you make a picture of a red barn at dusk?", True),
    ("generate an image of a cyberpunk city", True),
    ("paint a sunset", True),
    ("what's the speed of light?", False),
    ("make me a sandwich", False),
    ("how do I draw conclusions from this data", False),
])
def test_a_picture_ask_is_recognised(ask, wanted):
    assert chat.wants_picture(ask) is wanted


def test_the_image_tag_gives_the_prompt():
    assert chat.image_tag("Fine.\n[IMAGE: a dog with a leash]") == "a dog with a leash"
    assert chat.image_tag("no tag here") == ""


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
