"""purple Skippy's vision_flash and vision_moment (portal/vision.py): a flash is asked of the portal with the home token and says what is on its way;
a moment's pictures are saved into .purple-out/ where the agent can Read them, and anything odd is an error in words."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import house_portal_tools as tools  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 30
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 30
MOMENT = {"id": "20261102-210000", "at": 1793653200, "by": "key", "hold": True, "kept": False, "said": "a desk with a laptop", "pending": [],
          "seen": [{"text": "Salida", "lang": "es", "en": "Exit"}],
          "files": [{"name": "hud-page.jpg", "type": "image/jpeg"}, {"name": "pod-theater.png", "type": "image/png"},
                    {"name": "glasses-rgb-7s.zip", "type": "application/zip"}]}


def test_a_flash_asks_the_portal_for_a_moment_and_says_what_is_coming():
    sent = []

    def request(method, url, body=None, **kw):
        sent.append((method, url, body))
        return 200, {"id": "20261102-210000", "pending": ["pods", "pc"]}
    out = tools.vision_flash({}, request=request)
    assert sent == [("POST", tools.PORTAL_URL + "/home/vision/flash", {})]
    assert "20261102-210000" in out and "arriving" in out and "vision_moment" in out


def test_a_flash_with_skippy_pc_silent_says_nothing_was_fetched_and_a_missing_bucket_is_an_error():
    assert "nothing was fetched" in tools.vision_flash({}, request=lambda *a, **k: (200, {"id": "20261102-210001", "pending": []}))
    with pytest.raises(ValueError, match="no storage"):
        tools.vision_flash({}, request=lambda *a, **k: (503, {"error": "no bucket"}))
    with pytest.raises(ValueError, match="answered 500"):
        tools.vision_flash({}, request=lambda *a, **k: (500, {}))


def test_without_an_id_it_lists_the_latest_moments_one_line_each():
    seen = []
    out = tools.vision_moment({"limit": 3}, request=lambda method, url, **k: seen.append(url) or (200, {"moments": [MOMENT, dict(MOMENT, id="20261102-205900", hold=False, said="")]}))
    assert seen == [tools.PORTAL_URL + "/home/vision/moments?limit=3"]
    lines = out.splitlines()
    assert len(lines) == 2 and lines[0].startswith("20261102-210000 · 2026-11-02 ")
    assert "clip" in lines[0] and "said: a desk with a laptop" in lines[0] and "read: Exit" in lines[0]
    assert "clip" not in lines[1] and "said:" not in lines[1]
    assert tools.vision_moment({}, request=lambda *a, **k: (200, {"moments": []})) == "No moments yet."


def test_with_an_id_the_pictures_are_saved_where_the_agent_can_read_them(tmp_path):
    asked = []

    def request(method, url, body=None, raw=False, **kw):
        asked.append(url)
        if "/home/vision/moments" in url:
            return 200, {"moments": [MOMENT]}
        return (200, JPEG if url.endswith("hud-page.jpg") else PNG) if raw else (200, {})
    out = tools.vision_moment({"id": "20261102-210000"}, request=request, cwd=tmp_path)
    folder = tmp_path / ".purple-out" / "moment-20261102-210000"
    assert (folder / "hud-page.jpg").read_bytes() == JPEG and (folder / "pod-theater.png").read_bytes() == PNG
    assert not (folder / "glasses-rgb-7s.zip").exists()                                  # the clip is not a picture to Read
    assert asked[0].endswith("/home/vision/moments?limit=30")
    assert "Read a picture to look at it" in out and "clip is in the moment too" in out and str(folder / "hud-page.jpg") in out


def test_a_bad_id_or_an_unknown_moment_or_a_failing_portal_is_an_error_in_words(tmp_path):
    with pytest.raises(ValueError, match="id looks like"):
        tools.vision_moment({"id": "../../etc/passwd"}, request=lambda *a, **k: pytest.fail("a bad id must not reach the portal"))
    with pytest.raises(ValueError, match="no moment 20261102-210009"):
        tools.vision_moment({"id": "20261102-210009"}, request=lambda *a, **k: (200, {"moments": [MOMENT]}), cwd=tmp_path)
    with pytest.raises(ValueError, match="answered 401"):
        tools.vision_moment({}, request=lambda *a, **k: (401, {}))
    text, err = tools.call("vision_moment", {"id": "nope"})
    assert err and "id looks like" in text


def test_a_picture_the_portal_cannot_give_is_left_out_and_said(tmp_path):
    def request(method, url, body=None, raw=False, **kw):
        return (200, {"moments": [MOMENT]}) if "/home/vision/moments" in url else (404, b"")
    out = tools.vision_moment({"id": "20261102-210000"}, request=request, cwd=tmp_path)
    assert "No pictures could be fetched" in out


def test_both_are_offered_to_the_agent():
    assert "vision_flash" in tools.TOOLS and "vision_moment" in tools.TOOLS
    assert tools.TOOLS["vision_moment"]["input_schema"]["properties"]["id"]["type"] == "string"
