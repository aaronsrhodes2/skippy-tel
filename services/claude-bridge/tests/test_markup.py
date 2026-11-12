"""The Captain's marks on a screenshot (markup.py): found by Core's vision slot, each looked at close up, said in words for the coder."""
import json

from PIL import Image

import markup


def reply(text):
    return {"content": [{"type": "text", "text": text}]}


def picture(tmp_path):
    p = tmp_path / "shot.png"
    Image.new("RGB", (1000, 500), (0, 0, 0)).save(p)
    return str(p)


def test_each_mark_comes_back_in_the_pictures_pixels_with_what_is_under_it(tmp_path):
    asked = []

    def post(req):
        body = json.loads(req.data)
        asked.append(body["messages"][0]["content"][1]["text"])
        if len(asked) == 1:
            return reply('```json\n[{"color": "red", "shape": "circle", "bbox_2d": [100, 100, 300, 200], "tip": null},'
                         ' {"color": "red", "shape": "arrow", "bbox_2d": [500, 400, 700, 800], "tip": [700, 400]}]\n```')
        return reply("The Save button, text 'Save', cut off at the right.")

    out = markup.read(picture(tmp_path), "http://core", "k", post=post)
    assert "Mark 1: red circle from (100,50) to (300,100). On it: The Save button" in out
    assert "Mark 2: red arrow" in out and "pointing at (700,200)" in out
    assert len(asked) == 3 and "pointing at" in asked[2]


def test_no_marks_is_nothing_and_garbage_is_ignored(tmp_path):
    assert markup.read(picture(tmp_path), "http://u", "k", post=lambda r: reply("[]")) == ""
    assert markup.read(picture(tmp_path), "http://u", "k", post=lambda r: reply("I see a cat")) == ""
    assert markup._parse('[{"bbox_2d": "x"}, {"color": "red", "bbox_2d": [1, 1, 900, 950]}]')[0]["box"] == (1, 1, 900, 950)


def test_an_arrows_close_up_is_around_its_tip_and_a_circles_has_a_margin():
    img = Image.new("RGB", (1000, 1000))
    left, top, right, bottom = markup._crop(img, {"shape": "arrow", "tip": [500, 500], "box": (0, 0, 1000, 1000)}, 1000, 1000)
    assert left < 500 < right and top < 500 < bottom and right - left < 400
    c = markup._crop(img, {"shape": "circle", "tip": None, "box": (400, 400, 600, 600)}, 1000, 1000)
    assert c[0] < 400 and c[2] > 600


def test_a_core_run_with_a_marked_picture_gets_the_marks_ahead_of_the_description(shop, monkeypatch, tmp_path):
    monkeypatch.setattr(shop, "describe_picture", lambda path: "a settings page")
    monkeypatch.setattr(shop, "read_marks", lambda path: "Mark 1: red circle from (1,1) to (9,9). On it: the Save button.")
    msg = shop._describe_pictures("fix this", [picture(tmp_path)])
    assert msg.index("Mark 1: red circle") < msg.index("The whole picture:\na settings page")
    monkeypatch.setattr(shop, "read_marks", lambda path: (_ for _ in ()).throw(OSError("down")))
    assert "a settings page" in shop._describe_pictures("fix this", [picture(tmp_path)])
