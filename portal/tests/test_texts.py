"""Texts (texts.py): Google Voice's forwarded texts, read from Gmail with the read-only grant Connect Google for Skippy holds. Parsing a mail
(who sent it, Voice's footer, the code in it), the cache, the five-minute 'fresh' code, every way Google or Gmail can fail without the page
ever seeing an error, and the three doors (the page, its JSON, purple's); the Captain's alone."""
import base64

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import texts as T  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, _pair, login  # noqa: E402

T0 = 1_800_000_000.0                                           # "now" in the tests that need a clock
FOOTER = "\n\n-- \nSent using SMS-to-email. Reply to this email to text the sender back and \nsave on SMS fees.\nhttps://voice.google.com"


# ── the code in a text ──
@pytest.mark.parametrize("body, code", [
    ("Your Google verification code is 482913", "482913"),
    ("G-482913 is your Google verification code.", "482913"),
    ("482913 is your Uber code. Never share it.", "482913"),
    ("Use 7788 to log in. Code expires in 10 minutes", "7788"),
    ("Your PIN is 2024", "2024"),                                  # beside a word like PIN even a year is a code
    ("Your code: 123 456", "123456"),
    ("Your OTP is 004213", "004213"),                              # leading zeros stay
    ("Code 12345678", "12345678"),
    ("Your code is 482913 or call 555 123 4567", "482913"),
    ("Your code is 482913 or call 5551234567", "482913"),
    ("482913", "482913"),                                          # a lone number in a short text
    ("Meet in room 2214 at 8pm", "2214"),
])
def test_the_code_in_a_text(body, code):
    assert T.code_of(body) == code


@pytest.mark.parametrize("body", [
    "Call me at 555-123-4567",
    "My number is +1 (555) 123-4567 call me",
    "See you in 2026!",                                            # a lone year
    "Happy new year 2025, love you",
    "Dinner at 7:30 on 11/02/2026",
    "Order total $1234.56",
    "pi is 3.14159",
    "ids 1234 and 5678",                                           # two numbers and no word: not a code
    "Meet at 2030",
    "Be there at 1830 hrs",                                        # a number with a unit after it
    "Code 123456789",                                              # nine digits are not a code
    "I'm at 1234 Main Street, it's a long day and you should come by when you can and bring snacks",     # long, no word
    "",
])
def test_what_is_not_a_code(body):
    assert T.code_of(body) == ""


def test_the_nearest_number_to_the_word_wins_and_a_far_one_does_not():
    assert T.code_of("Order 111111 shipped. Your verification code is 222222.") == "222222"
    far = "Your verification is pending. " + "x" * 120 + " 998877"
    assert T.code_of(far) == ""


# ── reading one mail ──
@pytest.mark.parametrize("subject, from_header, name, number", [
    ("New text message from Alice Smith (+1 555-123-4567)", "", "Alice Smith", "+1 555-123-4567"),
    ("New text message from (555) 123-4567", "", "", "(555) 123-4567"),
    ("New text message from 22000", "", "", "22000"),
    ("New text message from Bob", "", "Bob", ""),
    ("New group message from Carol", "", "Carol", ""),
    ("Fwd: New text message from +15551234567", "", "", "+15551234567"),
    ("hello", '"Dana (+1 555 111 2222)" <x@txt.voice.google.com>', "Dana", "+1 555 111 2222"),
])
def test_who_sent_it_from_the_subject(subject, from_header, name, number):
    got = T.sender_of(subject, from_header)
    assert (got["name"], got["number"]) == (name, number) and got["sender"] == (name or number)
    assert T.sender_of("", "")["sender"] == "unknown"


def test_voices_footer_is_cut_and_a_dash_line_in_the_text_is_not():
    assert T.cut_footer("hey are you coming?" + FOOTER) == "hey are you coming?"
    assert T.cut_footer("a\n--\nb\n\nSent using Google Voice\nTo respond to this text message, reply to this email or visit Google Voice.") == "a\n--\nb"
    assert T.cut_footer("no footer here\r\nsecond line") == "no footer here\nsecond line"


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_the_text_of_a_mail_is_its_plain_part_else_its_html_with_the_tags_off():
    multipart = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/html", "body": {"data": b64("<p>html version</p>")}},
        {"mimeType": "multipart/related", "parts": [{"mimeType": "text/plain", "body": {"data": b64("plain version, 100% — café")}}]}]}
    assert T.body_of(multipart) == "plain version, 100% — café"
    only_html = {"mimeType": "text/html", "body": {"data": b64("<div>Your code is<br>482913</div><style>p{}</style>&amp; more</div>")}}
    assert T.body_of(only_html).split() == ["Your", "code", "is", "482913", "&", "more"]
    assert T.body_of({}) == "" and T.body_of({"mimeType": "text/plain", "body": {"data": "!!!"}}) == ""            # nothing, or nothing readable


# ── Gmail, faked ──
class Gmail:
    """The two Gmail calls texts.py makes, over a dict of mails; every call is recorded."""

    def __init__(self, mails=None):
        self.mails = dict(mails or {})
        self.calls = []
        self.fail = None                                       # a status code, or an exception to raise

    def __call__(self, url, token, params=None, timeout=0):
        self.calls.append((url, token, dict(params or {})))
        if isinstance(self.fail, Exception):
            raise self.fail
        if self.fail:
            return self.fail, {}
        if url.endswith("/messages"):
            ids = sorted(self.mails, key=lambda i: self.mails[i]["internalDate"], reverse=True)
            return 200, ({"messages": [{"id": i, "threadId": i} for i in ids]} if ids else {"resultSizeEstimate": 0})
        mid = url.rsplit("/", 1)[1]
        return (200, self.mails[mid]) if mid in self.mails else (404, {})

    def listed(self):
        return [c for c in self.calls if c[0].endswith("/messages")]

    def fetched(self):
        return [c[0].rsplit("/", 1)[1] for c in self.calls if not c[0].endswith("/messages")]


def mail(mid, subject, body, at, footer=True):
    text = body + (FOOTER if footer else "")
    return {"id": mid, "internalDate": str(int(at * 1000)),
            "payload": {"mimeType": "multipart/alternative",
                        "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": "x <1.2.abc@txt.voice.google.com>"}],
                        "parts": [{"mimeType": "text/plain", "body": {"data": b64(text)}}, {"mimeType": "text/html", "body": {"data": b64("<p>" + text + "</p>")}}]}}


class Link:
    def __init__(self, **got):
        self.got = {"connected": True, "access_token": "tok-1", "scopes": ["https://www.googleapis.com/auth/gmail.readonly"], **got}
        self.asked = 0

    def access(self):
        self.asked += 1
        if isinstance(self.got, Exception):
            raise self.got
        return self.got


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def texts_with(mails, link=None, clock=None):
    gmail = Gmail(mails)
    clock = clock or Clock()
    return T.Texts(link or Link(), http=gmail, clock=clock), gmail, clock


MAILS = {
    "18c0ffee01": mail("18c0ffee01", "New text message from Alice Smith (+1 555-123-4567)", "running late, save me a seat", T0 - 3600),
    "18c0ffee02": mail("18c0ffee02", "New text message from 22000", "Your Google verification code is 482913", T0 - 120),
    "18c0ffee03": mail("18c0ffee03", "New text message from Bob", "G-554433 is your Google verification code", T0 - 7200),
}


def test_the_texts_come_newest_first_with_who_what_when_and_the_code_and_voices_footer_gone():
    t, gmail, _ = texts_with(MAILS)
    got = t.state()
    assert got["connected"] is True and got["why"] == ""
    assert [x["id"] for x in got["texts"]] == ["18c0ffee02", "18c0ffee01", "18c0ffee03"]
    newest = got["texts"][0]
    assert (newest["from"], newest["number"], newest["body"], newest["code"], newest["at"]) == ("22000", "22000", "Your Google verification code is 482913", "482913", int(T0 - 120))
    assert got["texts"][1]["from"] == "Alice Smith" and got["texts"][1]["number"] == "+1 555-123-4567" and got["texts"][1]["code"] == ""
    assert "Sent using" not in " ".join(x["body"] for x in got["texts"])
    url, token, params = gmail.listed()[0]
    assert url == T.GMAIL + "/messages" and token == "tok-1" and params["q"] == "from:txt.voice.google.com newer_than:7d"
    assert {c[2]["format"] for c in gmail.calls if c not in gmail.listed()} == {"full"}


def test_a_code_under_five_minutes_old_is_fresh():
    t, gmail, clock = texts_with(MAILS)
    assert t.state()["fresh"] == {"code": "482913", "sender": "22000", "at": int(T0 - 120), "age_s": 120}


def test_a_code_stops_being_fresh_on_time_even_while_gmail_is_still_cached():
    t, gmail, clock = texts_with({"18c0ffee07": mail("18c0ffee07", "New text message from 22000", "Your code is 998877", T0 - 285)})
    assert t.state()["fresh"]["age_s"] == 285
    clock.t = T0 + 14                                            # 299 s old; the look is 14 s old, so Gmail is not asked again
    assert t.state()["fresh"]["age_s"] == 299
    clock.t = T0 + 16                                            # 301 s old
    assert t.state()["fresh"] is None and t.latest_code()["code"] is None and len(gmail.listed()) == 1


def test_purple_gets_the_code_the_sender_and_how_old_it_is():
    t, _, clock = texts_with(MAILS)
    got = t.latest_code()
    assert got == {"connected": True, "why": "", "code": "482913", "sender": "22000", "at": int(T0 - 120), "age_s": 120}


def test_gmail_is_asked_again_only_after_thirty_seconds_and_a_mail_already_read_is_not_fetched_again():
    t, gmail, clock = texts_with(MAILS)
    t.state()
    t.state()
    clock.t += T.CACHE_S - 1
    t.state()
    assert len(gmail.listed()) == 1 and sorted(gmail.fetched()) == sorted(MAILS)
    gmail.mails["18c0ffee04"] = mail("18c0ffee04", "New text message from Cy", "hi", clock.t - 5)
    clock.t += 2
    got = t.state()
    assert len(gmail.listed()) == 2 and gmail.fetched().count("18c0ffee04") == 1 and len(gmail.fetched()) == 4       # only the new one
    assert got["texts"][0]["id"] == "18c0ffee04"
    del gmail.mails["18c0ffee03"]                                # it left the seven days
    clock.t += T.CACHE_S + 1
    assert "18c0ffee03" not in [x["id"] for x in t.state()["texts"]]


def test_a_state_is_a_copy_the_caller_may_change():
    t, *_ = texts_with(MAILS)
    t.state()["texts"][0]["body"] = "changed"
    assert t.state()["texts"][0]["body"] == "Your Google verification code is 482913"


# ── every way it can fail, none of them an error for the page ──
def test_google_not_connected_or_not_set_up_or_not_answering_is_a_plain_why():
    t, gmail, _ = texts_with(MAILS, link=Link(connected=False, why="not connected: press Connect Google for Skippy in Admin"))
    got = t.state()
    assert got == {"connected": False, "why": "not connected: press Connect Google for Skippy in Admin", "texts": [], "fresh": None, "at": int(T0)}
    assert gmail.calls == []                                     # Gmail is never asked without a token
    assert T.Texts(None, http=Gmail(), clock=Clock()).state()["why"] == "Google isn't set up on this portal"
    broken = Link()
    broken.got = RuntimeError("token endpoint answered 500")
    assert texts_with(MAILS, link=broken)[0].state()["why"] == "Google didn't answer"
    assert texts_with(MAILS, link=Link(connected=False))[0].state()["why"].startswith("Google isn't connected")


def test_a_grant_without_gmail_says_so_in_words():
    t, gmail, _ = texts_with(MAILS, link=Link(scopes=["https://www.googleapis.com/auth/calendar.readonly"]))
    got = t.state()
    assert got["connected"] is False and "Gmail box ticked" in got["why"] and gmail.calls == []


def test_the_link_may_be_a_function_the_portal_builds_it_before_the_link_exists():
    link = Link()
    t = T.Texts(lambda: link, http=Gmail(MAILS), clock=Clock())
    assert t.state()["connected"] is True and link.asked == 1


@pytest.mark.parametrize("fail, why", [(403, "Gmail refused the mail"), (401, "Google refused the token")])
def test_gmail_refusing_is_a_why_not_texts(fail, why):
    t, gmail, _ = texts_with(MAILS)
    gmail.fail = fail
    got = t.state()
    assert got["connected"] is False and got["why"].startswith(why) and got["texts"] == []


def test_gmail_stumbling_keeps_the_last_good_list_and_says_so():
    t, gmail, clock = texts_with(MAILS)
    assert len(t.state()["texts"]) == 3
    clock.t += T.CACHE_S + 1
    gmail.fail = 500
    got = t.state()
    assert got["connected"] is True and got["why"] == "Gmail didn't answer just now" and len(got["texts"]) == 3
    clock.t += T.CACHE_S + 1
    gmail.fail = OSError("connection reset")
    assert t.state()["why"] == "Gmail didn't answer just now"
    clock.t += T.CACHE_S + 1
    gmail.fail = None
    assert t.state()["why"] == "" and len(t.state()["texts"]) == 3


def test_a_mail_that_cannot_be_read_is_skipped_and_tried_again_next_time():
    t, gmail, clock = texts_with(MAILS)
    real = gmail.__call__
    broken = {"18c0ffee02"}

    def flaky(url, token, params=None, timeout=0):
        if url.rsplit("/", 1)[1] in broken:
            return 500, {}
        return real(url, token, params, timeout)
    t.http = flaky
    assert [x["id"] for x in t.state()["texts"]] == ["18c0ffee01", "18c0ffee03"]
    broken.clear()
    clock.t += T.CACHE_S + 1
    assert [x["id"] for x in t.state()["texts"]] == ["18c0ffee02", "18c0ffee01", "18c0ffee03"]


def test_ids_that_are_not_gmail_ids_are_never_asked_for():
    t, gmail, _ = texts_with({})
    real = gmail.__call__
    t.http = lambda url, token, params=None, timeout=0: (200, {"messages": [{"id": "../../etc"}, {"id": "18c0ffee09"}, {"nope": 1}]}) if url.endswith("/messages") else real(url, token, params, timeout)
    assert t.state()["texts"] == [] and gmail.fetched() == ["18c0ffee09"]


# ── the doors ──
@pytest.fixture
def env():
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    texts = app.extensions["texts"]
    gmail = Gmail(MAILS)
    texts.link, texts.http, texts.clock = Link(), gmail, Clock(T0)
    c = app.test_client()
    login(c, ADMIN)
    return app, c, texts, gmail, {"Authorization": f"Bearer {_pair(app.test_client())}"}


def test_the_page_is_a_bridge_screen_that_asks_for_the_state_and_has_no_controls_of_its_own(env):
    app, c, *_ = env
    page = c.get("/texts").get_data(as_text=True)
    assert 'data-app="texts"' in page and '<body class="bridge"' in page and 'data-state="/texts/state"' in page
    section = page.split('<section class="bp" id="texts"', 1)[1].split("</section>", 1)[0]
    assert "Bridge.PList" in page and "<button" not in section                                    # rows are text and a copyable code
    assert 'data-tel="tile:texts"' in c.get("/apps").get_data(as_text=True)


def test_the_state_is_json_the_captain_reads_and_nobody_caches(env):
    app, c, texts, gmail, H = env
    r = c.get("/texts/state")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    got = r.get_json()
    assert got["fresh"]["code"] == "482913" and len(got["texts"]) == 3 and got["connected"] is True


def test_nobody_and_a_friend_cannot_read_his_texts(env):
    app, c, texts, gmail, H = env
    for who in (None, FRIEND):
        other = app.test_client()
        if who:
            login(other, who)
        for path in ("/texts", "/texts/state"):
            assert other.get(path).status_code in (302, 401, 403), (who, path)
    assert gmail.calls == []                                       # Gmail was never asked for them


def test_purples_door_takes_the_home_token_and_nothing_else(env):
    app, c, texts, gmail, H = env
    anon = app.test_client()
    assert anon.get("/home/texts/code").status_code == 401
    assert anon.get("/home/texts/code", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/home/texts/code").status_code == 401            # his own signed-in browser is not purple
    got = anon.get("/home/texts/code", headers=H)
    assert got.status_code == 200 and got.get_json()["code"] == "482913" and got.get_json()["sender"] == "22000"
    texts.clock.t = T0 + 600
    texts._cache = None
    assert anon.get("/home/texts/code", headers=H).get_json()["code"] is None


def test_the_launcher_has_a_texts_tile_for_the_captain_only(env):
    app, c, *_ = env
    assert 'data-tel="tile:texts"' in c.get("/apps").get_data(as_text=True)
    friend = app.test_client()
    login(friend, FRIEND)
    assert 'data-tel="tile:texts"' not in friend.get("/apps").get_data(as_text=True)
