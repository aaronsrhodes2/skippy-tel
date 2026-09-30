"""One event flask-api can never store must not stall all site telemetry (the reporter's half).

The defect: flask-api answered 500 to a batch holding one such event (a lone-surrogate string), and the reporter treated any 5xx as
'flask-api is down: retry, don't move on'. So the same batch was re-sent every cycle for ever: nothing behind it was stored, the
portal's buffer filled, and the widget said 'ok' because the pulls succeeded.

The rule now: a 500 makes the reporter halve the batch until the events flask-api refuses stand alone, file everything else, and
skip a lone event only when (a) flask-api has meanwhile STORED other events, so it is demonstrably up and writing, and (b) the event
still gets a 500 when sent alone again. Everything that looks like flask-api being down still retries and loses nothing: a
connection error, a 404 (an older flask-api without the route), a 502/503/504, and a 500 for every event (a broken database).
"""
import json

import pytest

import reporter
from test_music import Fake, _json
from test_reporter_telemetry import TOKEN, FakePortal, _recorder

BOTH_SIDES = "pal@example.com"


class PoisonApi:
    """flask-api's POST /telemetry/site_events. Answers 500 to any batch containing an event `poison(e)` says it can't store (as
    the real route did to a lone surrogate), or to everything while `broken`; stores each (boot, seq) once otherwise."""

    def __init__(self, poison=lambda e: False, broken=False, status=500, fail_posts=()):
        self.poison, self.broken, self.status, self.fail_posts = poison, broken, status, set(fail_posts)
        self.stored, self.posts = {}, []
        self.fake = Fake(self.handle)

    def handle(self, method, path, headers, body):
        events = json.loads(body)["events"]
        self.posts.append([e["seq"] for e in events])
        if self.broken or len(self.posts) in self.fail_posts or any(self.poison(e) for e in events):
            return _json({"error": "boom"}, self.status)
        for e in events:
            self.stored.setdefault((e["boot"], e["seq"]), e)
        return _json({"ok": True, "stored": len(events)})

    def seqs(self):
        return sorted(seq for _, seq in self.stored)


@pytest.fixture
def wired(monkeypatch):
    made = []

    def make(api, recorder=None):
        recorder = recorder or _recorder()
        portal = FakePortal(recorder)
        made.extend([api.fake, portal.fake])
        monkeypatch.setattr(reporter, "PORTAL", portal.fake.url)
        monkeypatch.setattr(reporter, "API", api.fake.url)
        return recorder, portal, reporter.SiteTelemetry(TOKEN)

    yield make
    for f in made:
        f.close()


def bad(event) -> bool:
    return "POISON" in event["name"]


def fill(rec, names):
    for name in names:
        rec.record("page", name, user=BOTH_SIDES, role="friend", sid="ab12cd34")


GOOD = [f"/p{i}" for i in range(6)]


@pytest.mark.parametrize("where", [0, 3, 6, "two at the head", "two in the middle", "all but one"])
def test_an_event_flask_api_will_not_store_is_skipped_and_everything_else_is_filed(wired, where):
    api = PoisonApi(poison=bad)
    rec, portal, site = wired(api)
    names = list(GOOD)
    if where == "two at the head":
        names = ["/POISON-a", "/POISON-b"] + names
    elif where == "two in the middle":
        names[2:2] = ["/POISON-a", "/POISON-b"]
    elif where == "all but one":
        names = ["/POISON-%d" % i for i in range(5)] + ["/only-good"]
    else:
        names.insert(where, "/POISON")
    fill(rec, names)
    good = [i + 1 for i, n in enumerate(names) if "POISON" not in n]
    site.collect()
    assert api.seqs() == good                                      # every good event, once; no poison
    assert site.after == len(names)                                # moved past the poison, so it is not pulled again
    assert len(api.posts) <= 4 * len(names) + 8                    # a bounded hunt: it ends, and it costs a few POSTs per bad event
    posts_before = len(api.posts)
    site.collect()                                                 # next cycle: the portal forgot everything up to `after`
    assert rec.status()["waiting"] == 0 and len(api.posts) == posts_before and site.poisoned == len(names) - len(good)


def test_a_big_batch_with_one_bad_event_costs_a_logarithmic_number_of_posts(wired):
    api = PoisonApi(poison=bad)
    rec, portal, site = wired(api)
    names = [f"/p{i}" for i in range(200)]
    names[137] = "/POISON"
    fill(rec, names)
    assert site.collect() == 199 and len(api.seqs()) == 199 and site.after == 200
    assert len(api.posts) <= 24, len(api.posts)


def test_a_flask_api_that_answers_500_to_everything_loses_nothing_and_skips_nothing(wired, capsys):
    api = PoisonApi(broken=True)                                    # a broken database: every write fails
    rec, portal, site = wired(api)
    fill(rec, GOOD)
    for cycle in range(4):
        assert site.collect() == 0 and site.after == 0 and site.poisoned == 0
        assert len(api.posts) <= (cycle + 1) * (reporter.TELEMETRY_HUNT + 1)      # each cycle's hunt is bounded
    assert rec.status()["waiting"] == 6
    api.broken = False                                              # it comes back: all six, once
    assert site.collect() == 6 and api.seqs() == [1, 2, 3, 4, 5, 6] and site.poisoned == 0
    assert capsys.readouterr().out.count("HTTP 500 storing telemetry") == 1        # said once per outage, not every 15 s


def test_an_outage_answered_500_that_ends_mid_hunt_does_not_skip_the_events_it_failed_on(wired):
    api = PoisonApi(fail_posts={1, 2, 3})                           # the first three POSTs fail (a database that recovers), then all is well
    rec, portal, site = wired(api)
    fill(rec, GOOD)
    site.collect()
    assert api.seqs() == [1, 2, 3, 4, 5, 6] and site.poisoned == 0 and site.after == 6


@pytest.mark.parametrize("status", [502, 503, 504, 404])
def test_a_flask_api_that_is_down_is_retried_as_a_whole_batch_never_bisected_never_skipped(wired, status):
    api = PoisonApi(broken=True, status=status)
    rec, portal, site = wired(api)
    fill(rec, GOOD)
    for _ in range(3):
        assert site.collect() == 0 and site.after == 0
    assert api.posts == [[1, 2, 3, 4, 5, 6]] * 3                    # one POST a cycle: the same batch
    api.broken = False
    assert site.collect() == 6 and api.seqs() == [1, 2, 3, 4, 5, 6] and site.poisoned == 0


def test_a_lone_bad_event_waits_for_proof_flask_api_is_writing_then_is_skipped_when_company_arrives(wired):
    api = PoisonApi(poison=bad)
    rec, portal, site = wired(api)
    fill(rec, ["/POISON"])
    for _ in range(3):
        assert site.collect() == 0 and site.after == 0 and site.poisoned == 0      # no proof yet that the fault is the event
    assert len(api.posts) == 3                                      # one POST a cycle: cheap, not a storm
    fill(rec, ["/late-a", "/late-b"])                               # traffic arrives: now flask-api shows it can write
    assert site.collect() == 2 and api.seqs() == [2, 3] and site.after == 3 and site.poisoned == 1
    site.collect()
    assert rec.status()["waiting"] == 0


def test_a_skipped_event_is_said_out_loud_with_which_it_was(wired, capsys):
    api = PoisonApi(poison=bad)
    rec, portal, site = wired(api)
    fill(rec, GOOD[:2] + ["/POISON"] + GOOD[2:])
    site.collect()
    out = capsys.readouterr().out
    assert "skipping a telemetry event flask-api will not store" in out and f"{rec.boot} seq 3" in out


def test_the_status_of_the_last_answer_is_kept_for_the_hunt(wired):
    api = PoisonApi()
    rec, portal, site = wired(api)
    fill(rec, GOOD)
    assert site.collect() == 6 and site.after == 6 and site.last_status == 200
    assert site._store([{"seq": 1, "boot": rec.boot}]) is True and site.last_status == 200
    api.broken = True
    assert site._store([{"seq": 2, "boot": rec.boot}]) is False and site.last_status == 500
