"""Posters and backdrops: the four widths, one fetch from Jellyfin per picture, a bounded disk cache, and the burst guard that
keeps a poster wall from taking every thread (the same rules as the music gate's cover thumbnails)."""
import os
import threading
import time

import pytest

import fake_jellyfin as fj
import images
from images import ImageCache, pick_width
from jellyfin import JellyfinError

ALPHA, GOLF = fj.fid(1), fj.fid(7)           # Alpha has a poster, a backdrop, a logo and a thumb; Golf has no art at all
JPEG = ("image/jpeg", b"\xff\xd8\xff")


def fetches(stack, item=ALPHA):
    return stack.fake.calls(f"/Items/{item}/Images/")


# ── ?w= ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asked,got", [("160", 160), ("320", 320), ("640", 640), ("1280", 1280), ("1", 160), ("200", 160), ("240", 320),
                                       ("480", 640), ("500", 640), ("960", 1280), ("4096", 1280), (" 320 ", 320)])
def test_width_snaps_to_the_nearest_allowed_one(asked, got):
    assert pick_width(asked) == got                        # (a tie, 240 between 160 and 320, goes to the bigger)


@pytest.mark.parametrize("asked", ["", "0", "-5", "abc", "320.5", "1e3", "4097", "99999999999999999999", "٣٢٠", "320px", "0x140", "None", "٣"])
def test_a_width_that_is_not_a_sensible_whole_number_is_refused(asked):
    assert pick_width(asked) is None


# ── the route ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_poster_is_the_picture_scaled_by_jellyfin_with_the_caching_headers(stack):
    r = stack.get(f"/img/{ALPHA}/poster?w=300")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data.startswith(b"\xff\xd8\xff")
    assert r.data == b"\xff\xd8\xff" + f"{ALPHA}:Primary:320".encode() * 4                      # 300 snapped to 320, asked of Jellyfin as maxWidth
    assert r.headers["Cache-Control"] == "private, max-age=604800" and r.headers["ETag"]
    assert r.headers["Access-Control-Allow-Origin"] == "*" and "Set-Cookie" not in r.headers
    up = fetches(stack)[-1]
    assert up["q"] == {"maxWidth": "320", "quality": "80"} and up["authed"]                      # exactly this, the key in the header only


@pytest.mark.parametrize("kind,jf", [("poster", "Primary"), ("backdrop", "Backdrop"), ("logo", "Logo"), ("thumb", "Thumb")])
def test_every_kind_maps_to_its_jellyfin_image_type(stack, kind, jf):
    r = stack.get(f"/img/{ALPHA}/{kind}")
    assert r.status_code == 200
    assert fetches(stack)[-1]["path"] == f"/Items/{ALPHA}/Images/{jf}"
    assert fetches(stack)[-1]["q"]["maxWidth"] == "640"                                          # no ?w=: the default


def test_a_png_stays_a_png(stack):
    r = stack.get(f"/img/{ALPHA}/logo?w=160")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data.startswith(b"\x89PNG")
    assert stack.get(f"/img/{ALPHA}/logo?w=160").mimetype == "image/png"                        # also from the disk cache


@pytest.mark.parametrize("query", ["?w=", "?w=abc", "?w=0", "?w=-1", "?w=1e3", "?w=4097", "?w=%D9%A3%D9%A2%D9%A0", "?w=320;x"])
def test_a_width_that_is_not_a_number_is_a_400_and_asks_nothing_of_jellyfin(stack, query):
    r = stack.get(f"/img/{ALPHA}/poster{query}")
    assert r.status_code == 400 and r.get_json()["error"] == "bad request"
    assert fetches(stack) == []


def test_unknown_items_and_missing_art_are_404s_decided_from_the_catalog(stack):
    for path in (f"/img/{fj.fid(99)}/poster", f"/img/{GOLF}/poster", f"/img/{GOLF}/backdrop", f"/img/{fj.fid(21)}/poster",   # no such item, no art
                 f"/img/{ALPHA}/banner", f"/img/{ALPHA}/Primary", f"/img/{ALPHA}/POSTER"):                                  # not one of the four kinds
        r = stack.get(path)
        assert r.status_code == 404 and r.get_json() == {"error": "not found"}, path
    assert stack.fake.calls("/Items/") == []                                                                                # Jellyfin never asked


def test_a_collections_poster_works_too(stack):
    r = stack.get(f"/img/{fj.fid(20)}/poster?w=160")
    assert r.status_code == 200 and r.data == b"\xff\xd8\xff" + f"{fj.fid(20)}:Primary:160".encode() * 4


def test_the_etag_revalidates_and_a_hit_does_not_change_the_file(stack):
    first = stack.get(f"/img/{ALPHA}/poster?w=320")
    etag = first.headers["ETag"]
    path = next((stack.images.root).rglob("*.jpg"))
    stamp = path.stat().st_mtime_ns
    again = stack.get(f"/img/{ALPHA}/poster?w=320", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.data == b"" and again.headers["ETag"] == etag
    assert stack.get(f"/img/{ALPHA}/poster?w=320").headers["ETag"] == etag                      # the same picture, the same tag, every time
    assert path.stat().st_mtime_ns == stamp                                                      # (a touch on every hit would be a write per poster)
    assert stack.get(f"/img/{ALPHA}/poster?w=320", headers={"If-None-Match": '"other"'}).status_code == 200
    assert stack.get(f"/img/{ALPHA}/poster?w=640").headers["ETag"] != etag                       # another width is another picture
    assert len(fetches(stack)) == 2


def test_a_poster_is_fetched_from_jellyfin_once(stack):
    for _ in range(5):
        assert stack.get(f"/img/{ALPHA}/poster?w=320").status_code == 200
    assert len(fetches(stack)) == 1
    stack.get(f"/img/{ALPHA}/poster?w=250")                         # snaps to 320: the same entry
    assert len(fetches(stack)) == 1


def test_new_art_in_jellyfin_is_a_new_picture(fake, make_stack):
    s = make_stack(fake)
    s.get(f"/img/{ALPHA}/poster?w=320")
    fake.item(1)["ImageTags"]["Primary"] = "brandnewtag"             # the Captain replaced the poster in Jellyfin
    assert s.library.refresh()
    s.get(f"/img/{ALPHA}/poster?w=320")
    assert len(fetches(s)) == 2


def test_the_cache_survives_a_restart(fake, make_stack, tmp_path):
    s = make_stack(fake)
    s.get(f"/img/{ALPHA}/poster?w=320")
    again = make_stack(fake)                                         # a new process on the same /state
    assert again.get(f"/img/{ALPHA}/poster?w=320").status_code == 200
    assert len(fetches(again)) == 1


def test_cached_pictures_are_served_while_jellyfin_is_down_and_new_ones_say_so(fake, make_stack):
    s = make_stack(fake)
    s.get(f"/img/{ALPHA}/poster?w=320")
    fake.go_down()
    assert s.get(f"/img/{ALPHA}/poster?w=320").status_code == 200
    r = s.get(f"/img/{ALPHA}/backdrop?w=320")
    assert r.status_code == 503 and r.get_json() == {"error": "Jellyfin is not answering"}
    fake.come_back()
    assert s.get(f"/img/{ALPHA}/backdrop?w=320").status_code == 200                              # a failure was not remembered


def test_a_picture_jellyfin_no_longer_has_is_a_404_and_is_not_asked_for_again(fake, make_stack):
    s = make_stack(fake)
    fake.item(1)["ImageTags"].pop("Primary")                         # in the catalog we built, gone from Jellyfin since
    for _ in range(4):
        r = s.get(f"/img/{ALPHA}/poster?w=320")
        assert r.status_code == 404 and r.get_json() == {"error": "not found"}
    assert len(fetches(s)) == 1


def test_a_jellyfin_that_sends_a_non_image_is_a_502_never_cached(fake, make_stack, monkeypatch):
    s = make_stack(fake)
    monkeypatch.setattr(type(s.client), "image", lambda self, *a, **k: (_ for _ in ()).throw(JellyfinError("bad")))
    r = s.get(f"/img/{ALPHA}/poster?w=320")
    assert r.status_code == 502 and r.get_json() == {"error": "Jellyfin sent something unusable"}
    assert not list(s.images.root.rglob("*.*"))


def test_no_secret_or_jellyfin_address_in_what_comes_back(stack):
    for path in (f"/img/{ALPHA}/poster", f"/img/{ALPHA}/poster?w=bad", f"/img/{fj.fid(99)}/poster"):
        r = stack.get(path)
        text = str(r.headers) + (r.get_data(as_text=True) if r.mimetype == "application/json" else "")
        assert fj.KEY not in text and "127.0.0.1" not in text and "Location" not in r.headers, path


# ── the cache itself ─────────────────────────────────────────────────────────────────────────────────────────────────

def ask(cache, n, load, kind="poster", width=160):
    return cache.get(fj.fid(n), kind, width, "tag", load)


def test_simultaneous_requests_for_one_picture_fetch_it_once(tmp_path):
    cache = ImageCache(tmp_path / "c")
    calls, results, go = [], [], threading.Event()

    def load():
        calls.append(1)
        time.sleep(0.1)
        return b"\xff\xd8\xffX", "image/jpeg"

    def worker():
        go.wait()
        results.append(ask(cache, 1, load))
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    go.set()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len(results) == 10 and len({r[0] for r in results}) == 1 and all(r[2] == b"\xff\xd8\xffX" for r in results)


def test_a_burst_of_uncached_pictures_cannot_take_every_thread(tmp_path):
    """The gate has 48 threads and a film that streams holds one: past a handful of posters being fetched, the rest are told to
    come back (Busy) instead of waiting on a thread."""
    cache = ImageCache(tmp_path / "c", workers=1, backlog=1)         # one fetching, one waiting: a third is turned away
    release, inside, outcomes = threading.Event(), threading.Event(), []

    def slow():
        inside.set()
        release.wait(5)
        return b"\xff\xd8\xffX", "image/jpeg"

    def go(n):
        try:
            outcomes.append((n, ask(cache, n, slow) is not None))
        except images.Busy:
            outcomes.append((n, "busy"))
    first = threading.Thread(target=go, args=(1,))
    first.start()
    inside.wait(5)
    second = threading.Thread(target=go, args=(2,))
    second.start()
    time.sleep(0.2)                                                  # the second is now waiting its turn
    go(3)
    assert outcomes == [(3, "busy")]
    release.set()
    first.join(5)
    second.join(5)
    assert sorted(o for o in outcomes if o[0] != 3) == [(1, True), (2, True)]
    assert ask(cache, 3, lambda: (b"\xff\xd8\xffX", "image/jpeg")) is not None                    # a slot is free again


def test_cached_pictures_never_wait_or_get_turned_away(tmp_path):
    cache = ImageCache(tmp_path / "c", workers=1, backlog=0)
    ask(cache, 1, lambda: (b"\xff\xd8\xffX", "image/jpeg"))
    for _ in range(50):
        assert ask(cache, 1, lambda: pytest.fail("not cached")) is not None


def test_the_route_turns_a_burst_away_with_503_and_retry_after(stack, monkeypatch):
    def busy(*a, **k):
        raise images.Busy
    monkeypatch.setattr(stack.images, "get", busy)
    r = stack.get(f"/img/{ALPHA}/poster?w=320")
    assert r.status_code == 503 and r.headers["Retry-After"] == "1" and r.get_json() == {"error": "busy; try again in a moment", "busy": True}
    assert stack.get("/catalog.json").status_code == 200                                         # everything else carries on


def test_a_real_burst_through_the_route_is_bounded(fake, make_stack, tmp_path):
    """End to end: Jellyfin sits on every picture for a moment; a dozen different posters arrive at once. Only a few are fetched
    (workers + backlog) and the rest get a 503 at once, instead of a dozen threads waiting on Jellyfin."""
    fake.items[:] = [fj.movie(n, f"Film {n}") for n in range(1, 13)]
    fake.boxes[:] = []
    fake.children.clear()
    s = make_stack(fake)
    s.images._pending = threading.BoundedSemaphore(3)                # 2 workers + 1 waiting
    s.images._busy = threading.BoundedSemaphore(2)
    fake.delay["/Items/"] = 0.4
    codes, lock = [], threading.Lock()

    def go(n):
        r = s.get(f"/img/{fj.fid(n)}/poster?w=160")
        with lock:
            codes.append((r.status_code, r.headers.get("Retry-After")))
    threads = [threading.Thread(target=go, args=(n,)) for n in range(1, 13)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    served = [c for c in codes if c[0] == 200]
    turned_away = [c for c in codes if c[0] == 503]
    assert len(codes) == 12 and len(served) + len(turned_away) == 12
    assert len(served) <= 6 and len(turned_away) >= 6 and all(c[1] == "1" for c in turned_away)
    assert len(fake.calls("/Items/")) == len(served)                 # only what was served was ever fetched


def test_a_missing_picture_is_remembered_but_only_up_to_a_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_BAD", 5)
    cache = ImageCache(tmp_path / "c")
    calls = []
    for n in range(1, 9):
        assert ask(cache, n, lambda: calls.append(1)) is None        # load() returns None: Jellyfin has no such picture
    assert len(calls) == 8 and len(cache._bad) == 5                  # bounded: the oldest are forgotten
    assert ask(cache, 8, lambda: calls.append(1)) is None and len(calls) == 8                    # a remembered one isn't asked again
    assert ask(cache, 1, lambda: calls.append(1)) is None and len(calls) == 9                    # a forgotten one is


def test_an_empty_answer_counts_as_missing(tmp_path):
    cache = ImageCache(tmp_path / "c")
    assert ask(cache, 1, lambda: (b"", "image/jpeg")) is None
    assert not list((tmp_path / "c").rglob("*.jpg"))


def test_an_error_from_load_reaches_the_caller_and_frees_the_slot(tmp_path):
    cache = ImageCache(tmp_path / "c", workers=1, backlog=0)
    for _ in range(3):                                               # three failures in a row would leak a slot each if they weren't returned
        with pytest.raises(JellyfinError):
            ask(cache, 1, lambda: (_ for _ in ()).throw(JellyfinError("down")))
    assert ask(cache, 1, lambda: (b"\xff\xd8\xffX", "image/jpeg")) is not None


def test_the_cache_is_bounded_and_the_oldest_go_first(tmp_path):
    clock = [1000.0]
    cache = ImageCache(tmp_path / "c", limit=500, clock=lambda: clock[0])
    keys = []
    for n in range(1, 13):
        clock[0] += 100
        etag, _mime, _data = ask(cache, n, lambda: (b"\xff\xd8\xff" + b"x" * 97, "image/jpeg"))      # 100 bytes each
        os.utime(tmp_path / "c" / etag[:2] / f"{etag}.jpg", (clock[0], clock[0]))                  # the file's age follows the fake clock
        keys.append(etag)
    on_disk = {p.stem for p in (tmp_path / "c").rglob("*.jpg")}
    total = sum(p.stat().st_size for p in (tmp_path / "c").rglob("*.jpg"))
    assert total <= 500 and keys[-1] in on_disk and keys[0] not in on_disk
    assert on_disk <= set(keys) and len(on_disk) >= 3                # eviction frees room, it doesn't empty the cache


def test_a_cache_left_by_an_earlier_run_counts_against_the_limit(tmp_path):
    big = ImageCache(tmp_path / "c", limit=10_000_000)
    for n in range(1, 7):
        ask(big, n, lambda: (b"\xff\xd8\xff" + b"x" * 997, "image/jpeg"))
    before = sum(p.stat().st_size for p in (tmp_path / "c").rglob("*.jpg"))
    small = ImageCache(tmp_path / "c", limit=before // 2)            # restarted with a lower limit
    ask(small, 99, lambda: (b"\xff\xd8\xff" + b"x" * 997, "image/jpeg"))
    after = sum(p.stat().st_size for p in (tmp_path / "c").rglob("*.jpg"))
    assert after <= before // 2


def test_a_temp_file_left_by_a_crash_is_cleared_when_it_is_old(tmp_path):
    clock = [10_000.0]
    cache = ImageCache(tmp_path / "c", clock=lambda: clock[0])
    stale, fresh = tmp_path / "c" / "ab" / "abcdef.jpg.1234.tmp", tmp_path / "c" / "ab" / "abcdef.jpg.5678.tmp"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"half a poster")
    fresh.write_bytes(b"a poster being written right now")
    os.utime(stale, (1000, 1000))
    os.utime(fresh, (9_900, 9_900))
    cache.evict()
    assert not stale.exists() and fresh.exists()


def test_an_unwritable_cache_costs_the_cache_not_the_picture(tmp_path):
    blocker = tmp_path / "c"
    blocker.write_bytes(b"a FILE where the directory should be")
    got = ask(ImageCache(blocker), 1, lambda: (b"\xff\xd8\xffX", "image/jpeg"))
    assert got is not None and got[2] == b"\xff\xd8\xffX"


def test_an_unknown_type_is_served_but_not_kept(tmp_path):
    cache = ImageCache(tmp_path / "c")
    got = ask(cache, 1, lambda: (b"GIF89a....", "image/gif"))
    assert got is not None and got[1] == "image/gif" and not list((tmp_path / "c").rglob("*.*"))


def test_a_read_only_touches_an_image_that_has_gone_stale(tmp_path):
    clock = [100.0]
    cache = ImageCache(tmp_path / "c", clock=lambda: clock[0])
    etag = ask(cache, 1, lambda: (b"\xff\xd8\xffX", "image/jpeg"))[0]
    path = tmp_path / "c" / etag[:2] / f"{etag}.jpg"
    os.utime(path, (50, 50))
    clock[0] = 60.0                                                  # 10 s old: left alone
    ask(cache, 1, lambda: pytest.fail("cached"))
    assert path.stat().st_mtime == 50
    clock[0] = 50 + 13 * 3600                                        # 13 h old: touched (to the real now), so eviction keeps what is looked at
    ask(cache, 1, lambda: pytest.fail("cached"))
    assert path.stat().st_mtime > 1_000_000_000
