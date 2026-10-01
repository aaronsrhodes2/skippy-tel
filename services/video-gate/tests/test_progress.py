"""Watch progress: the player reports where the viewer is; the gate validates it strictly, throttles it, and writes it to Jellyfin as the
configured user, so "Continue Watching" in Jellyfin's own apps agrees with the browser."""
import gzip
import json
import time

import pytest

import fake_jellyfin as fj
import gate
from helpers import NOW

ALPHA, BRAVO, HOTEL, FOXTROT = fj.fid(1), fj.fid(2), fj.fid(8), fj.fid(6)       # runtimes: 5400 s, 6000 s, 7200 s (played), unknown
TICKS = 10_000_000


def post(stack, body, tok=None, **kw):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return stack.web.post(f"/t/{tok or stack.tok}/progress", data=data, **kw)


def writes(stack):
    return stack.fake.userdata


def report(item=ALPHA, pos=600, state="progress", **extra):
    return {"id": item, "pos": pos, "state": state, **extra}


# ── what is written to Jellyfin ─────────────────────────────────────────────────────────────────────────────────────

def test_a_report_is_written_as_the_configured_user_and_shows_in_the_next_catalog(stack):
    r = post(stack, report(pos=1234.9))
    assert r.status_code == 200 and r.get_json() == {"ok": True, "resume": 1234, "played": False}
    assert writes(stack) == [{"item": ALPHA, "user": fj.USER_ID, "body": {
        "PlaybackPositionTicks": 1234 * TICKS, "LastPlayedDate": time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime(NOW))}}]
    call = stack.fake.calls("/UserItems/", "POST")[0]
    assert call["authed"] and call["path"] == f"/UserItems/{ALPHA}/UserData" and call["q"] == {"userId": fj.USER_ID}
    item = next(i for i in json.loads(gzip.decompress(stack.get("/catalog.json").data))["items"] if i["id"] == ALPHA)
    assert item["resume"] == 1234 and item["played"] is False


def test_what_was_written_is_what_jellyfin_reports_back_after_a_refresh(stack):
    post(stack, report(pos=777))
    assert stack.library.refresh()                                                # Jellyfin's own data replaces ours: the two cannot drift
    assert stack.library.get(ALPHA)["resume"] == 777
    stack.clock.advance(10)
    post(stack, report(pos=5000, state="stop"))
    assert stack.library.refresh() and stack.library.get(ALPHA)["played"] is True and stack.library.get(ALPHA)["resume"] == 0


@pytest.mark.parametrize("pos,resume", [(0, 0), (5, 0), (29.9, 0), (30, 30), (31, 31), (5399, 5399)])
def test_a_quick_peek_is_not_worth_resuming(stack, pos, resume):
    r = post(stack, report(pos=pos))
    assert r.status_code == 200 and r.get_json()["resume"] == resume
    assert writes(stack)[-1]["body"]["PlaybackPositionTicks"] == resume * TICKS


def test_finishing_the_film_marks_it_watched_and_clears_the_resume_point(stack):
    r = post(stack, report(pos=5000, state="stop"))                               # 92.6 % of 5400 s
    assert r.get_json() == {"ok": True, "resume": 0, "played": True}
    body = writes(stack)[-1]["body"]
    assert body["Played"] is True and body["PlayCount"] == 1 and body["PlaybackPositionTicks"] == 0
    assert stack.library.get(ALPHA)["played"] is True and stack.library.get(ALPHA)["resume"] == 0


def test_stopping_early_keeps_the_place(stack):
    r = post(stack, report(pos=2000, state="stop"))
    assert r.get_json() == {"ok": True, "resume": 2000, "played": False}
    assert "Played" not in writes(stack)[-1]["body"]                              # an early stop doesn't change watched-ness


def test_the_ninety_percent_rule_only_applies_to_a_stop(stack):
    r = post(stack, report(pos=5300, state="progress"))                           # 98 %, but still playing
    assert r.get_json() == {"ok": True, "resume": 5300, "played": False}


def test_an_explicit_played_flag_wins(stack):
    assert post(stack, report(pos=100, played=True)).get_json() == {"ok": True, "resume": 0, "played": True}
    stack.clock.advance(10)
    assert post(stack, report(pos=100, played=False)).get_json() == {"ok": True, "resume": 100, "played": False}
    assert writes(stack)[-1]["body"]["Played"] is False


def test_the_play_count_is_only_bumped_the_first_time(stack):
    post(stack, report(item=HOTEL, pos=7000, state="stop"))                       # Hotel was already played
    assert "PlayCount" not in writes(stack)[-1]["body"] and writes(stack)[-1]["body"]["Played"] is True


def test_a_film_whose_length_is_unknown_is_still_reportable(stack):
    r = post(stack, report(item=FOXTROT, pos=4000, state="stop"))                 # not probed yet: no runtime, so no 90 % rule
    assert r.status_code == 200 and r.get_json() == {"ok": True, "resume": 4000, "played": False}
    assert post(stack, report(item=FOXTROT, pos=172801)).status_code == 400       # but no more than two days in


def test_only_the_fields_the_gate_computes_ever_go_to_jellyfin(stack):
    post(stack, report(pos=300, state="stop", played=True))
    assert set(writes(stack)[-1]["body"]) <= {"PlaybackPositionTicks", "Played", "PlayCount", "LastPlayedDate"}


def test_start_writes_nothing_but_checks_the_film(stack):
    r = post(stack, {"id": ALPHA, "state": "start"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "relayed": False}
    assert post(stack, {"id": ALPHA, "state": "start", "pos": 600}).get_json() == {"ok": True, "relayed": False}
    assert writes(stack) == []
    assert post(stack, {"id": fj.fid(99), "state": "start"}).status_code == 404


def test_a_text_plain_beacon_works_too(stack):
    # navigator.sendBeacon(url, JSON string) when a tab closes sends text/plain: no preflight, and it must still be understood
    r = post(stack, report(pos=900, state="stop"), content_type="text/plain;charset=UTF-8")
    assert r.status_code == 200 and writes(stack)


# ── throttling ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_reports_for_one_film_are_at_least_five_seconds_apart(stack):
    assert post(stack, report(pos=100)).get_json()["ok"] and len(writes(stack)) == 1
    stack.clock.advance(4)
    assert post(stack, report(pos=104)).get_json() == {"ok": True, "throttled": True} and len(writes(stack)) == 1
    stack.clock.advance(1.1)
    assert post(stack, report(pos=110)).get_json()["resume"] == 110 and len(writes(stack)) == 2


def test_the_throttle_is_per_film_and_a_stop_is_never_held_back_by_a_progress(stack):
    post(stack, report(item=ALPHA, pos=100))
    assert post(stack, report(item=BRAVO, pos=100)).get_json().get("throttled") is None       # another film
    assert post(stack, report(item=ALPHA, pos=101, state="stop")).get_json().get("throttled") is None   # the last word is always heard
    assert post(stack, report(item=ALPHA, pos=102, state="stop")).get_json()["throttled"] is True       # but not twice in a second
    assert len(writes(stack)) == 3


def test_throttled_reports_do_not_reach_jellyfin_at_all(stack):
    post(stack, report(pos=100))
    n = len(stack.fake.requests)
    for _ in range(20):
        post(stack, report(pos=101))
    assert len(stack.fake.requests) == n


def test_the_throttle_remembers_a_bounded_number_of_films():
    t = [0.0]
    th = gate.Throttle(5.0, lambda: t[0], keep=3)
    assert [th.allow(k) for k in "abc"] == [True] * 3
    assert th.allow("a") is False
    assert th.allow("d") is True                                                  # over the bound: the least recently used ("b"...) is forgotten
    assert len(th._seen) == 3
    t[0] = 6.0
    assert th.allow("a") is True


# ── validation ──────────────────────────────────────────────────────────────────────────────────────────────────────

BAD_BODIES = [
    b"", b"not json", b"[]", b'"x"', b"null", b"5", b"true", b"{}", b'{"id": 5, "state": "progress", "pos": 1}',
    b'{"state": "progress", "pos": 1}', b'{"id": "%s", "pos": 1}' % ALPHA.encode(),
    b'{"id": "%s", "state": "pause", "pos": 1}' % ALPHA.encode(), b'{"id": "%s", "state": "PROGRESS", "pos": 1}' % ALPHA.encode(),
    b'{"id": "%s", "state": ["progress"], "pos": 1}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress"}' % ALPHA.encode(), b'{"id": "%s", "state": "stop"}' % ALPHA.encode(),     # pos is required but for a start
    b'{"id": "%s", "state": "progress", "pos": null}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress", "pos": "100"}' % ALPHA.encode(), b'{"id": "%s", "state": "progress", "pos": true}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress", "pos": [1]}' % ALPHA.encode(), b'{"id": "%s", "state": "progress", "pos": {"a": 1}}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress", "pos": -1}' % ALPHA.encode(), b'{"id": "%s", "state": "progress", "pos": -0.5}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress", "pos": NaN}' % ALPHA.encode(), b'{"id": "%s", "state": "progress", "pos": Infinity}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress", "pos": -Infinity}' % ALPHA.encode(), b'{"id": "%s", "state": "progress", "pos": 1e999}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress", "pos": %s}' % (ALPHA.encode(), b"9" * 400),                              # an integer too big for a float
    b'{"id": "%s", "state": "progress", "pos": 5941}' % ALPHA.encode(),                                          # past 105 % of 5400 s + 60 s
    b'{"id": "%s", "state": "progress", "pos": 1, "played": "yes"}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress", "pos": 1, "played": 1}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress", "pos": 1, "extra": 1}' % ALPHA.encode(),                                # unknown keys: refused, not ignored
    b'{"id": "%s", "state": "progress", "pos": 1, "userId": "x"}' % ALPHA.encode(),
    b'{"id": "%s", "state": "progress", "pos": 1, "id": 7}' % ALPHA.encode(),                                    # (a duplicate key: the last one wins: 7)
    b"[" * 1000, b'{"a":' * 200, b"\xff\xfe\x00bad", b"\x00" * 10, b'{"id": "%s", "state": "progress", "pos": 1}\x00' % ALPHA.encode(),
]


@pytest.mark.parametrize("body", BAD_BODIES, ids=[f"{n:02d}" for n in range(len(BAD_BODIES))])
def test_a_report_that_is_not_exactly_what_the_player_sends_is_a_400_and_costs_nothing(stack, body):
    before = len(stack.fake.requests)
    r = post(stack, body)
    assert r.status_code in (400, 404), body                                      # (404: an id that isn't a film)
    if r.status_code == 400:
        assert r.get_json() == {"error": "bad request"}
    assert len(stack.fake.requests) == before and writes(stack) == []


def test_an_enormous_integer_is_a_400_not_a_crash(stack):
    r = post(stack, b'{"id": "%s", "state": "progress", "pos": %s}' % (ALPHA.encode(), b"9" * 400))
    assert r.status_code == 400 and r.get_json() == {"error": "bad request"}      # (math.isfinite() on it would raise OverflowError)


def test_a_deeply_nested_body_is_a_400_not_a_crash(stack):
    r = post(stack, b"[" * 1000)
    assert r.status_code == 400 and r.get_json() == {"error": "bad request"}
    assert post(stack, b'{"id": ' + b"[" * 450 + b"1" + b"]" * 450 + b"}").status_code == 400      # deep but under the size limit: a bad id, not a crash


def test_a_body_over_a_kilobyte_is_refused_unread(stack):
    r = post(stack, b'{"id": "%s", "state": "progress", "pos": 1, "pad": "%s"}' % (ALPHA.encode(), b"x" * 2000))
    assert r.status_code == 413 and r.get_json() == {"error": "too large"}
    assert writes(stack) == []


@pytest.mark.parametrize("ident", [fj.fid(99), fj.fid(10), fj.fid(20), "0" * 32, "../etc/passwd", fj.fid(11).upper(), ALPHA + "\n", ALPHA[:-1], "",
                                   "http://evil.example/", ALPHA + "/x"])        # (fid(11) is a real episode: its UPPER-case spelling is not an id)
def test_the_film_must_be_one_in_the_catalog(stack, ident):
    r = post(stack, report(item=ident))
    assert r.status_code == 404 and r.get_json() == {"error": "not found"}
    assert writes(stack) == [] and stack.fake.calls("/UserItems/") == []


def test_only_post_is_accepted(stack):
    for method in ("GET", "PUT", "DELETE", "PATCH"):
        assert stack.web.open(f"{stack.base}/progress", method=method).status_code == 405


# ── Jellyfin misbehaving ────────────────────────────────────────────────────────────────────────────────────────────

def test_jellyfin_down_is_a_503_and_the_catalog_is_not_changed(fake, make_stack):
    s = make_stack(fake)
    fake.go_down()
    r = post(s, report(pos=900))
    assert r.status_code == 503 and r.get_json() == {"error": "Jellyfin is not answering"}
    assert s.library.get(ALPHA)["resume"] == 1200                                 # what we did not manage to write is not shown as written


def test_a_film_gone_from_jellyfin_is_a_404(fake, make_stack):
    s = make_stack(fake)
    fake.items[:] = [i for i in fake.items if i["Id"] != ALPHA]                   # in our catalog, deleted from Jellyfin since
    r = post(s, report(pos=900))
    assert r.status_code == 404 and r.get_json() == {"error": "not found"}


@pytest.mark.parametrize("status,code,error", [(500, 503, "Jellyfin is not answering"), (401, 502, "Jellyfin sent something unusable"),
                                               (403, 502, "Jellyfin sent something unusable"), (418, 502, "Jellyfin sent something unusable")])
def test_what_jellyfin_refuses_becomes_a_plain_answer_with_no_upstream_text(fake, make_stack, status, code, error):
    s = make_stack(fake)
    fake.userdata_status = status
    r = post(s, report(pos=900))
    assert r.status_code == code and r.get_json() == {"error": error}
    assert fj.KEY not in r.get_data(as_text=True) and s.library.get(ALPHA)["resume"] == 1200


def test_the_user_is_resolved_once_and_the_right_one(fake, make_stack):
    s = make_stack(fake, user="viewer")
    post(s, report(pos=900))
    assert writes(s)[-1]["user"] == fj.OTHER_ID
    assert len(fake.calls("/Users")) == 1                                         # asked at the catalog build, never again
