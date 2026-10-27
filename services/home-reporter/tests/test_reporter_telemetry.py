"""The reporter's site-telemetry collector, against the portal's REAL recorder (portal/telemetry.py, loaded by path: standard
library only, so its pull/acknowledge/boot rules are the real ones) behind a fake portal, and a fake flask-api that stores each
(boot, seq) once, as the real UNIQUE constraint does. Every failure mode has to lose nothing and file nothing twice: flask-api
down, a batch it will never take, a restarted portal, a restarted reporter, more events than one pull, and the 128 KB body limit
flask-api enforces. Also: the site summary rides in the report."""
import importlib.util
import json
import time
import urllib.parse
from pathlib import Path

import pytest

import reporter
from test_music import Fake, _json

TOKEN = "home-token"
REPO = Path(__file__).resolve().parents[3]


def _recorder(**kw):
    spec = importlib.util.spec_from_file_location("portal_telemetry_under_test", REPO / "portal" / "telemetry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Telemetry(**kw)


class FakePortal:
    """GET /home/telemetry?boot=&after=&limit= answered by the real Telemetry.since()."""

    def __init__(self, recorder, token=TOKEN, status=200):
        self.rec, self.token, self.status, self.pulls = recorder, token, status, []
        self.fake = Fake(self.handle)

    def handle(self, method, path, headers, body):
        url = urllib.parse.urlparse(path)
        assert method == "GET" and url.path == "/home/telemetry"
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query, keep_blank_values=True).items()}
        self.pulls.append({"q": q, "auth": headers.get("Authorization")})
        if self.status != 200:
            return _json({"error": "nope"}, self.status)
        if headers.get("Authorization") != f"Bearer {self.token}":
            return _json({"error": "unauthorized"}, 401)
        return _json(self.rec.since(q["boot"], int(q["after"]), int(q["limit"])))


class FakeApi:
    """POST /telemetry/site_events: stores each (boot, seq) once, like flask-api's UNIQUE (boot, seq); refuses over 128 KB."""

    def __init__(self, fail_first=0, refuse=None, fail_posts=()):
        self.stored, self.posts, self.fail_first, self.refuse, self.fail_posts = {}, [], fail_first, refuse, set(fail_posts)
        self.fake = Fake(self.handle)

    def handle(self, method, path, headers, body):
        assert method == "POST" and path == "/telemetry/site_events"
        events = json.loads(body)["events"]
        self.posts.append({"size": len(body), "seqs": [e["seq"] for e in events]})
        if len(body) > 128 * 1024:
            return _json({"error": "too large"}, 413)
        if self.fail_first > 0 or len(self.posts) in self.fail_posts:
            self.fail_first = max(0, self.fail_first - 1)
            return _json({"error": "down"}, 503)
        if self.refuse and self.refuse(len(self.posts), events):
            return _json({"error": "bad"}, 400)
        new = 0
        for e in events:
            new += (e["boot"], e["seq"]) not in self.stored
            self.stored.setdefault((e["boot"], e["seq"]), e)
        return _json({"ok": True, "stored": new})


@pytest.fixture
def wired(monkeypatch):
    made = []

    def make(recorder=None, api=None, portal=None):
        recorder = recorder or _recorder()
        api, portal = api or FakeApi(), portal or FakePortal(recorder)
        made.extend([api.fake, portal.fake])
        monkeypatch.setattr(reporter, "PORTAL", portal.fake.url)
        monkeypatch.setattr(reporter, "API", api.fake.url)
        return recorder, api, portal, reporter.SiteTelemetry(TOKEN)

    yield make
    for f in made:
        f.close()


def fill(rec, n, kind="page", **detail):
    for i in range(n):
        rec.record(kind, f"/p{i}", user="pal@example.com", role="friend", sid="ab12cd34", **detail)


def test_events_are_pulled_filed_in_flask_api_and_acknowledged_on_the_next_pull(wired):
    rec, api, portal, site = wired()
    fill(rec, 5)
    assert site.collect() == 5
    assert sorted(seq for _, seq in api.stored) == [1, 2, 3, 4, 5]
    assert rec.status()["waiting"] == 5                                    # filed, but the portal forgets on the NEXT pull
    assert site.collect() == 0 and rec.status()["waiting"] == 0 and len(api.stored) == 5
    assert all(p["auth"] == f"Bearer {TOKEN}" for p in portal.pulls)
    assert portal.pulls[0]["q"] == {"boot": "", "after": "0", "limit": str(reporter.TELEMETRY_PULL)}
    assert portal.pulls[1]["q"]["boot"] == rec.boot and portal.pulls[1]["q"]["after"] == "5"


def test_what_is_filed_is_exactly_what_the_portal_recorded(wired):
    rec, api, portal, site = wired()
    rec.record("chat", "send", user="pal@example.com", role="friend", sid="ab12cd34", message="hi", reply="hello", tools=[])
    site.collect()
    (stored,) = api.stored.values()
    assert stored == rec.recent(1)[0] and stored["detail"]["reply"] == "hello"


def test_nothing_to_report_costs_one_pull_and_no_post(wired):
    rec, api, portal, site = wired()
    assert site.collect() == 0 and len(portal.pulls) == 1 and api.posts == []


def test_a_flask_api_that_is_down_loses_nothing_and_files_each_event_once_when_it_returns(wired):
    rec, api, portal, site = wired(api=FakeApi(fail_first=2))
    fill(rec, 4)
    assert site.collect() == 0 and site.after == 0 and api.stored == {}      # 503: progress didn't move
    assert site.collect() == 0 and api.stored == {}
    assert site.collect() == 4 and sorted(seq for _, seq in api.stored) == [1, 2, 3, 4]
    site.collect()
    assert rec.status()["waiting"] == 0 and len(api.stored) == 4


def test_a_cycle_that_half_worked_keeps_what_was_filed_and_resumes_where_it_stopped(wired, monkeypatch):
    monkeypatch.setattr(reporter, "TELEMETRY_BATCH", 700)                     # a few events per POST
    rec, api, portal, site = wired(api=FakeApi(fail_posts={3}))               # the third POST of the cycle fails
    fill(rec, 9, note="x" * 200)
    filed = site.collect()
    first_two = api.posts[0]["seqs"] + api.posts[1]["seqs"]
    assert filed == len(first_two) and 0 < filed < 9
    assert site.after == max(first_two) and sorted(s for _, s in api.stored) == sorted(first_two)
    site.collect()                                                              # next cycle: acknowledges, then files the rest
    assert sorted(seq for _, seq in api.stored) == list(range(1, 10))
    assert sum(len(p["seqs"]) for p in api.posts if p is not api.posts[2]) == 9   # the failed POST was the only repeat


def test_a_batch_flask_api_will_never_take_is_skipped_so_it_cannot_block_the_rest(wired, monkeypatch, capsys):
    monkeypatch.setattr(reporter, "TELEMETRY_BATCH", 700)
    rec, api, portal, site = wired(api=FakeApi(refuse=lambda n, events: n == 1))
    fill(rec, 8, note="x" * 200)
    site.collect()
    first = api.posts[0]["seqs"]
    assert not any(seq in {s for _, s in api.stored} for seq in first)           # the refused batch was dropped
    assert {s for _, s in api.stored} == set(range(1, 9)) - set(first)
    assert site.after == 8 and "refused a telemetry batch (HTTP 400)" in capsys.readouterr().out


def test_a_flask_api_that_does_not_have_the_route_yet_only_delays_and_never_skips(wired, monkeypatch):
    """A rollout in the wrong order: the reporter is new, flask-api is still the old image (404). Nothing may be lost."""
    rec, api, portal, site = wired()
    old = Fake(lambda m, p, h, b: _json({"error": "not found"}, 404))
    try:
        monkeypatch.setattr(reporter, "API", old.url)
        fill(rec, 3)
        assert site.collect() == 0 and site.after == 0 and rec.status()["waiting"] == 3
    finally:
        old.close()
    monkeypatch.setattr(reporter, "API", api.fake.url)                            # flask-api is rebuilt
    assert site.collect() == 3 and sorted(seq for _, seq in api.stored) == [1, 2, 3]


def test_every_post_stays_under_flask_apis_128_kb_limit_even_for_long_chats(wired):
    rec, api, portal, site = wired()
    for i in range(60):
        rec.record("chat", "send", user="pal@example.com", role="friend", sid="ab12cd34",
                   message=("word " * 1000)[:4000], reply=("word " * 2000)[:8000], model="claude-haiku-4-5", tools=[])
    assert site.collect() == 60 and len(api.stored) == 60
    assert len(api.posts) > 1 and max(p["size"] for p in api.posts) <= 128 * 1024
    assert reporter.TELEMETRY_BATCH < 128 * 1024


def test_more_events_than_one_pull_are_drained_in_one_cycle(wired, monkeypatch):
    monkeypatch.setattr(reporter, "TELEMETRY_PULL", 50)
    rec, api, portal, site = wired()
    fill(rec, 130)
    assert site.collect() == 130 and len(portal.pulls) == 3 and len(api.stored) == 130


def test_a_portal_that_always_has_more_cannot_hold_a_cycle_forever(wired, monkeypatch):
    monkeypatch.setattr(reporter, "TELEMETRY_CYCLE_S", 0.3)
    monkeypatch.setattr(reporter, "TELEMETRY_PULL", 1)

    class Endless:
        n = 0

        def since(self, boot, after, limit):
            Endless.n += 1
            return {"boot": "ab12cd34", "more": True, "events": [
                {"boot": "ab12cd34", "seq": Endless.n, "ts": time.time(), "user": "a@x.com", "role": "admin", "sid": "",
                 "kind": "page", "name": "/", "detail": {}}]}
    rec, api, portal, site = wired(recorder=Endless())
    started = time.time()
    assert site.collect() > 0 and time.time() - started < 5


def test_a_restarted_portal_is_followed_and_its_restarted_numbering_does_not_collide(wired):
    rec, api, portal, site = wired()
    fill(rec, 3)
    site.collect()
    assert site.boot == rec.boot
    rec2 = _recorder()                                                          # the portal redeployed: seq starts at 1 again
    fill(rec2, 2)
    portal.rec = rec2
    assert site.collect() == 2 and site.boot == rec2.boot
    assert sorted(api.stored) == sorted([(rec.boot, 1), (rec.boot, 2), (rec.boot, 3), (rec2.boot, 1), (rec2.boot, 2)])


def test_a_restarted_reporter_starts_over_and_flask_api_files_nothing_twice(wired):
    rec, api, portal, site = wired()
    fill(rec, 4)
    site.collect()                                                              # filed, but not yet acknowledged
    assert rec.status()["waiting"] == 4
    again = reporter.SiteTelemetry(TOKEN)                                        # the reporter restarted: no memory of progress
    assert again.collect() == 4                                                  # it pulls them all again and posts them again ...
    assert len(api.posts) == 2 and len(api.stored) == 4                          # ... and flask-api ignores what it already has
    assert sorted(seq for _, seq in api.stored) == [1, 2, 3, 4]
    again.collect()
    assert rec.status()["waiting"] == 0


def test_a_wrong_token_or_a_down_portal_is_survived_and_said_once(wired, capsys):
    rec, api, portal, site = wired(portal=None)
    fill(rec, 2)
    portal.token = "someone-else"
    assert site.collect() == 0 and site.collect() == 0 and api.posts == []
    out = capsys.readouterr().out
    assert out.count("HTTP 401") == 1 and "SKIPPY_HOME_TOKEN doesn't match" in out
    portal.token = TOKEN
    assert site.collect() == 2


def test_an_unreachable_portal_or_flask_api_never_raises(monkeypatch, capsys):
    def refused(*a, **k):
        raise ConnectionRefusedError("nobody home")
    monkeypatch.setattr(reporter.urllib.request, "urlopen", refused)
    site = reporter.SiteTelemetry(TOKEN)
    assert site.collect() == 0 and site.collect() == 0
    assert capsys.readouterr().out.count("can't pull telemetry") == 1              # said once per outage, not every 15 s
    assert site._store([{"seq": 1}]) is False                                       # flask-api unreachable: try again later, don't skip


def test_junk_from_the_portal_is_ignored(wired):
    rec, api, portal, site = wired(recorder=type("Junk", (), {"since": lambda self, b, a, l: {
        "boot": "ab12cd34", "more": False, "events": ["x", None, {"seq": "3"}, {"seq": 1, "boot": "ab12cd34", "kind": "page"}]}})())
    assert site.collect() == 1 and list(api.stored) == [("ab12cd34", 1)]


def test_chunks_split_by_bytes_and_keep_every_event_in_order(monkeypatch):
    monkeypatch.setattr(reporter, "TELEMETRY_BATCH", 500)
    events = [{"seq": i, "pad": "x" * 100} for i in range(1, 11)]
    parts = reporter.SiteTelemetry.chunks(events)
    assert len(parts) > 1 and [e for p in parts for e in p] == events
    assert all(sum(len(json.dumps(e)) for e in p) <= 500 or len(p) == 1 for p in parts)
    assert reporter.SiteTelemetry.chunks([]) == []


def test_the_report_carries_flask_apis_site_summary(monkeypatch):
    summary = {"days": 30, "people": 2, "top_pages": [{"name": "/games", "views": 3, "people": 2}]}
    asked = []

    def fake_fetch(url, method="GET", data=None, timeout=4.0, raw=False):
        asked.append(url)
        return summary if url.endswith("/telemetry/site_summary") else None

    monkeypatch.setattr(reporter, "fetch", fake_fetch)
    monkeypatch.setattr(reporter, "BRIDGE", "")
    monkeypatch.setattr(reporter, "MUSIC", "")
    monkeypatch.setattr(reporter, "MAC", "")
    assert reporter.gather()["site_activity"] == summary
    assert f"{reporter.API}/telemetry/site_summary" in asked
    monkeypatch.setattr(reporter, "fetch", lambda *a, **k: None)                  # an older flask-api has no such route
    assert reporter.gather()["site_activity"] is None
