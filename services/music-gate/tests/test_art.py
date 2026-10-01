"""Cover thumbnails: the sizes that exist, a real resize, a cover that can't be read is a 404 (never a 500), the disk cache is
keyed by (path, mtime, width) and bounded."""
import io
import os
import threading
import time

import pytest

import art
import gate
from art import ThumbCache, pick_size, render
from helpers import image_bytes, make_mp3
from library import Library

NOW = 1_800_000_000
KEY = gate.gate_key("home-token")


def open_image(data):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


# ── ?w= ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asked,got", [("96", 96), ("192", 192), ("384", 384), ("768", 768), ("100", 96), ("150", 192), ("144", 192),
                                       ("1", 96), ("300", 384), ("600", 768), ("1000", 768), ("4096", 768), (" 192 ", 192)])
def test_width_snaps_to_the_nearest_allowed_one(asked, got):
    assert pick_size(asked) == got                      # (a tie, 144 between 96 and 192, goes to the bigger)


@pytest.mark.parametrize("asked", ["", "0", "-5", "abc", "96.5", "1e2", "4097", "99999999999999999999", "٩٦", "96px", "0x60", "None"])
def test_a_width_that_is_not_a_sensible_whole_number_is_refused(asked):
    assert pick_size(asked) is None


# ── the resize ──────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("w", art.SIZES)
def test_a_big_cover_becomes_a_jpeg_that_fits_the_width(w):
    out = open_image(render(image_bytes((1400, 1000)), w))
    assert out.format == "JPEG" and max(out.size) == w and out.size == (w, round(w * 1000 / 1400))   # the aspect ratio stays


def test_a_small_cover_is_not_enlarged():
    assert open_image(render(image_bytes((120, 120)), 768)).size == (120, 120)


def test_a_png_with_transparency_is_laid_on_the_matte_not_black_or_garbage():
    from PIL import Image
    src = io.BytesIO()
    Image.new("RGBA", (300, 300), (255, 0, 0, 0)).save(src, "PNG")        # fully transparent red
    px = open_image(render(src.getvalue(), 96)).getpixel((10, 10))
    assert all(abs(a - b) <= 3 for a, b in zip(px, art.MATTE)), px


@pytest.mark.parametrize("fmt,mode", [("PNG", "P"), ("PNG", "L"), ("PNG", "LA"), ("PNG", "1"), ("GIF", "P"), ("WEBP", "RGB"),
                                      ("TIFF", "CMYK"), ("JPEG", "L"), ("JPEG", "CMYK"), ("BMP", "RGB")])
def test_other_formats_and_modes_come_out_as_rgb_jpeg(fmt, mode):
    color = {"RGB": (9, 9, 9), "CMYK": (0, 0, 0, 0), "1": 1, "L": 100, "LA": (100, 255), "P": 3}[mode]
    out = open_image(render(image_bytes((500, 500), fmt, mode, color), 192))
    assert out.format == "JPEG" and out.mode == "RGB" and out.size == (192, 192)


@pytest.mark.parametrize("junk", [b"", b"not an image at all", b"\xff\xd8\xff\xe0 truncated", b"GIF89a", b"\x89PNG\r\n\x1a\n" + b"\0" * 20,
                                  image_bytes((800, 800))[:200]])
def test_a_cover_that_cannot_be_read_is_none_not_an_exception(junk):
    assert render(junk, 96) is None


def test_a_cover_with_absurd_dimensions_is_refused_before_it_is_decoded():
    from PIL import Image
    huge = io.BytesIO()
    Image.new("1", (9000, 9000)).save(huge, "PNG")                         # 81 MP: a few KB of file, gigabytes if it were decoded as RGB
    assert render(huge.getvalue(), 96) is None


# ── the cache ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_thumbnail_is_made_once_and_keyed_by_path_mtime_and_width(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    cache = ThumbCache(tmp_path / "cache")
    calls = []

    def load():
        calls.append(1)
        return image_bytes((600, 600)), "image/jpeg"
    one = cache.get("A/B/a.mp3", f, 96, load)
    assert cache.get("A/B/a.mp3", f, 96, load) == one and len(calls) == 1             # the second came off the disk
    assert cache.get("A/B/a.mp3", f, 192, load)[0] != one[0] and len(calls) == 2      # another width: its own file
    assert cache.get("Other/B/a.mp3", f, 96, load)[0] != one[0] and len(calls) == 3   # another path: its own file
    os.utime(f, (NOW, NOW))                                                           # retagged: the old thumbnail is not the answer
    assert cache.get("A/B/a.mp3", f, 96, load)[0] != one[0] and len(calls) == 4
    assert len(list((tmp_path / "cache").rglob("*.jpg"))) == 4


def test_it_survives_a_restart(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    ThumbCache(tmp_path / "cache").get("A/a.mp3", f, 96, lambda: (image_bytes(), "image/jpeg"))
    again = ThumbCache(tmp_path / "cache")
    assert again.get("A/a.mp3", f, 96, lambda: pytest.fail("read the file again")) is not None


def test_a_file_that_is_gone_or_has_no_cover_is_none(tmp_path):
    cache = ThumbCache(tmp_path / "cache")
    assert cache.get("A/a.mp3", tmp_path / "missing.mp3", 96, lambda: pytest.fail("no file")) is None
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    assert cache.get("A/a.mp3", f, 96, lambda: None) is None


def test_a_cover_known_to_be_unreadable_is_not_opened_again(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    cache = ThumbCache(tmp_path / "cache")
    calls = []
    for _ in range(3):
        assert cache.get("A/a.mp3", f, 96, lambda: calls.append(1) or (b"garbage", "image/jpeg")) is None
    assert len(calls) == 1 and not list((tmp_path / "cache").rglob("*.jpg"))


def test_simultaneous_requests_for_one_thumbnail_make_it_once(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    cache = ThumbCache(tmp_path / "cache")
    calls, results = [], []
    gate_open = threading.Event()

    def load():
        calls.append(1)
        return image_bytes((800, 800)), "image/jpeg"

    def worker():
        gate_open.wait()
        results.append(cache.get("A/a.mp3", f, 192, load))
    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    gate_open.set()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len(results) == 12 and len({r[0] for r in results}) == 1 and all(r[1] == results[0][1] for r in results)


def test_a_burst_of_uncached_covers_cannot_take_every_thread(tmp_path):
    """The gate has 32 threads and a streaming track holds one: past a handful of thumbnails being made, the rest are told
    to come back (Busy) instead of waiting on a thread."""
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    cache = ThumbCache(tmp_path / "cache", workers=1, backlog=1)          # one making, one waiting: a third is turned away
    release, inside, outcomes = threading.Event(), threading.Event(), []

    def slow():
        inside.set()
        release.wait(5)
        return image_bytes((300, 300)), "image/jpeg"

    def ask(name):
        try:
            outcomes.append((name, cache.get(f"A/{name}.mp3", f, 96, slow) is not None))
        except art.Busy:
            outcomes.append((name, "busy"))
    first = threading.Thread(target=ask, args=("one",))
    first.start()
    inside.wait(5)
    second = threading.Thread(target=ask, args=("two",))
    second.start()
    time.sleep(0.2)                                                        # `two` is now waiting its turn
    ask("three")
    assert ("three", "busy") in outcomes and len(outcomes) == 1
    release.set()
    first.join(5)
    second.join(5)
    assert sorted(o for o in outcomes if o[0] != "three") == [("one", True), ("two", True)]
    assert cache.get("A/three.mp3", f, 96, lambda: (image_bytes((300, 300)), "image/jpeg")) is not None   # a slot is free again


def test_cached_thumbnails_never_wait_or_get_turned_away(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    cache = ThumbCache(tmp_path / "cache", workers=1, backlog=0)
    cache.get("A/a.mp3", f, 96, lambda: (image_bytes(), "image/jpeg"))
    for _ in range(50):
        assert cache.get("A/a.mp3", f, 96, lambda: pytest.fail("not cached")) is not None


def test_the_cache_is_bounded_and_the_oldest_go_first(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    clock = [1000.0]
    one = len(render(image_bytes((500, 500), color=(10, 20, 30)), 96))
    cache = ThumbCache(tmp_path / "cache", max_bytes=one * 5, clock=lambda: clock[0])
    keys = []
    for i in range(12):
        clock[0] += 100
        got = cache.get(f"A/{i}.mp3", f, 96, lambda: (image_bytes((500, 500), color=(10, 20, 30)), "image/jpeg"))
        # make the file's own age follow the fake clock, as time passing would
        os.utime(tmp_path / "cache" / got[0][:2] / f"{got[0]}.jpg", (clock[0], clock[0]))
        keys.append(got[0])
    on_disk = {p.stem for p in (tmp_path / "cache").rglob("*.jpg")}
    total = sum(p.stat().st_size for p in (tmp_path / "cache").rglob("*.jpg"))
    assert total <= one * 5 and keys[-1] in on_disk and keys[0] not in on_disk       # the newest stays, the first is long gone
    assert on_disk <= set(keys) and len(on_disk) >= 3                                # eviction frees room, it doesn't empty the cache


def test_a_cache_left_by_an_earlier_run_counts_against_the_limit(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    big = ThumbCache(tmp_path / "cache", max_bytes=10_000_000)
    for i in range(6):
        big.get(f"A/{i}.mp3", f, 96, lambda: (image_bytes((500, 500)), "image/jpeg"))
    before = sum(p.stat().st_size for p in (tmp_path / "cache").rglob("*.jpg"))
    small = ThumbCache(tmp_path / "cache", max_bytes=before // 2)                    # restarted with a lower limit
    small.get("B/new.mp3", f, 96, lambda: (image_bytes((500, 500)), "image/jpeg"))
    after = sum(p.stat().st_size for p in (tmp_path / "cache").rglob("*.jpg"))
    assert after <= before // 2


def test_a_temp_file_left_by_a_crash_is_cleared_when_it_is_old(tmp_path):
    clock = [10_000.0]
    cache = ThumbCache(tmp_path / "cache", clock=lambda: clock[0])
    stale = tmp_path / "cache" / "ab" / "abcdef.jpg.1234.tmp"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"half a thumbnail")
    os.utime(stale, (1000, 1000))
    cache.evict()
    assert not stale.exists()


def test_an_unwritable_cache_costs_the_cache_not_the_cover(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    blocker = tmp_path / "cache"
    blocker.write_bytes(b"a FILE where the directory should be")
    got = ThumbCache(blocker).get("A/a.mp3", f, 96, lambda: (image_bytes(), "image/jpeg"))
    assert got is not None and open_image(got[1]).format == "JPEG"


# ── the route ───────────────────────────────────────────────────────────────────────────────────────────────────────

def fake_tags(path):
    return {"t": path.stem, "a": "x", "al": "y", "n": 1, "d": 1, "g": "", "s": 100, "art": True, "y": 0}


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "music"
    (root / "Artist" / "Album (2016)").mkdir(parents=True)
    make_mp3(root / "Artist" / "Album (2016)" / "Cover.mp3", cover=image_bytes((1200, 900)), TIT2="Cover")
    make_mp3(root / "Artist" / "Album (2016)" / "NoCover.mp3", TIT2="NoCover")
    make_mp3(root / "Artist" / "Album (2016)" / "Corrupt.mp3", cover=b"this is not an image", TIT2="Corrupt")
    make_mp3(root / "Artist" / "Album (2016)" / "Png.mp3", cover=image_bytes((500, 500), "PNG"), cover_mime="image/png", TIT2="Png")
    library = Library(root, tmp_path / "state", reader=fake_tags, clock=lambda: NOW)
    library.scan()
    return library


@pytest.fixture
def client(lib):
    return gate.create_app(lib, KEY, clock=lambda: NOW).test_client()


TOK = gate.sign(KEY, NOW + 3600)
ART = "/art/Artist/Album%20(2016)/"


def test_route_serves_a_resized_jpeg_with_the_caching_headers(client):
    r = client.get(f"/t/{TOK}{ART}Cover.mp3?w=192")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert r.headers["Cache-Control"] == "private, max-age=604800" and r.headers["Access-Control-Allow-Origin"] == "*"
    im = open_image(r.data)
    assert im.format == "JPEG" and im.size == (192, 144)
    assert len(r.data) < len(image_bytes((1200, 900)))


def test_route_snaps_the_width_and_refuses_nonsense(client):
    a = client.get(f"/t/{TOK}{ART}Cover.mp3?w=100")
    b = client.get(f"/t/{TOK}{ART}Cover.mp3?w=96")
    assert a.status_code == b.status_code == 200 and a.data == b.data and a.headers["ETag"] == b.headers["ETag"]
    assert open_image(client.get(f"/t/{TOK}{ART}Cover.mp3?w=4096").data).size == (768, 576)       # the most it snaps to
    for bad in ("abc", "", "0", "-1", "96.5", "4097", "5000"):
        r = client.get(f"/t/{TOK}{ART}Cover.mp3?w={bad}")
        assert r.status_code == 400, bad


def test_route_revalidates_with_the_etag(client):
    r = client.get(f"/t/{TOK}{ART}Cover.mp3?w=96")
    again = client.get(f"/t/{TOK}{ART}Cover.mp3?w=96", headers={"If-None-Match": r.headers["ETag"]})
    assert again.status_code == 304 and not again.data
    assert client.get(f"/t/{TOK}{ART}Cover.mp3?w=192").headers["ETag"] != r.headers["ETag"]


def test_route_without_a_width_is_the_cover_as_it_is(client):
    r = client.get(f"/t/{TOK}{ART}Cover.mp3")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and open_image(r.data).size == (1200, 900)
    assert r.headers["Cache-Control"] == "private, max-age=604800"
    png = client.get(f"/t/{TOK}{ART}Png.mp3")
    assert png.mimetype == "image/png"
    assert client.get(f"/t/{TOK}{ART}Png.mp3?w=96").mimetype == "image/jpeg"            # a thumbnail is always a JPEG


@pytest.mark.parametrize("name", ["NoCover.mp3", "Corrupt.mp3", "Missing.mp3"])
def test_no_cover_or_an_unreadable_one_is_a_404_never_a_500(client, name):
    for query in ("?w=96", "?w=96", "?w=768", ""):                                      # twice: the second skips the work
        r = client.get(f"/t/{TOK}{ART}{name}{query}")
        if name == "Corrupt.mp3" and query == "":
            assert r.status_code == 200          # served as it is: the browser decides (an <img> shows its broken-image icon)
        else:
            assert r.status_code == 404, (name, query)


def test_route_turns_a_burst_away_with_503_and_retry_after(lib, tmp_path, monkeypatch):
    def busy(*a, **kw):
        raise art.Busy
    thumbs = ThumbCache(tmp_path / "cache")
    monkeypatch.setattr(thumbs, "get", busy)
    client = gate.create_app(lib, KEY, clock=lambda: NOW, thumbs=thumbs).test_client()
    r = client.get(f"/t/{TOK}{ART}Cover.mp3?w=96")
    assert r.status_code == 503 and r.headers["Retry-After"] == "1"
    assert client.get(f"/t/{TOK}{ART}Cover.mp3").status_code == 200       # the original cover and the audio carry on


def test_thumbnails_land_in_the_state_directory(client, lib):
    client.get(f"/t/{TOK}{ART}Cover.mp3?w=96")
    assert len(list((lib.state / "art-cache").rglob("*.jpg"))) == 1


def test_the_audio_stays_untouched_by_all_this(client):
    r = client.get(f"/t/{TOK}/a/Artist/Album%20(2016)/Cover.mp3", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.data) == 10
