"""The media hand-off store: audio only, capped, short-lived, replaceable."""
import pytest

import media


def test_a_clip_round_trips_with_its_owner_and_title():
    m = media.Media()
    assert m.put("job1", b"ID3....", "audio/mpeg", "aaron@example.com", "lofi beat") == {"bytes": 7}
    item = m.get("job1")
    assert item["data"] == b"ID3...." and item["type"] == "audio/mpeg" and item["owner"] == "aaron@example.com"
    assert m.get("nope") is None


@pytest.mark.parametrize("ctype", ["text/html", "application/octet-stream", "image/svg+xml", "image/gif", ""])
def test_only_audio_and_png_jpeg_pictures_are_held(ctype):
    with pytest.raises(media.MediaError) as err:
        media.Media().put("j", b"data", ctype, "a")
    assert err.value.status == 415


def test_empty_and_oversized_uploads_are_refused(monkeypatch):
    m = media.Media()
    with pytest.raises(media.MediaError) as err:
        m.put("j", b"", "audio/mpeg", "a")
    assert err.value.status == 400
    monkeypatch.setattr(media, "MAX_ONE", 10)
    with pytest.raises(media.MediaError) as err:
        m.put("j", b"x" * 11, "audio/mpeg", "a")
    assert err.value.status == 413


def test_the_whole_store_is_capped_but_replacing_a_clip_doesnt_double_count(monkeypatch):
    monkeypatch.setattr(media, "MAX_ALL", 20)
    m = media.Media()
    m.put("a", b"x" * 12, "audio/mpeg", "o")
    with pytest.raises(media.MediaError) as err:
        m.put("b", b"y" * 12, "audio/mpeg", "o")
    assert err.value.status == 507
    m.put("a", b"z" * 18, "audio/mpeg", "o")                     # same key: replaces, still fits
    assert m.get("a")["data"] == b"z" * 18


def test_clips_expire():
    now = [1000.0]
    m = media.Media(clock=lambda: now[0])
    m.put("a", b"x", "audio/mpeg", "o")
    now[0] += media.TTL - 1
    assert m.get("a") is not None
    now[0] += 2
    assert m.get("a") is None


def test_titles_are_trimmed():
    m = media.Media()
    m.put("a", b"x", "audio/ogg", "o", "t" * 500)
    assert len(m.get("a")["title"]) == 100
