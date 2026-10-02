"""Asking for access: the Discord pins link to a game's page; someone without access signs in with Google, asks with
the email Google verified, and the request waits beside the friends on the Admin page. Plus the pin's own text."""
import html
import json
import re
import threading
import time

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import access_requests as access_mod  # noqa: E402
import auth  # noqa: E402
import discord_admin  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, _add_friend, login  # noqa: E402

import app as portal  # noqa: E402

STRANGER = "stranger@example.com"
PAL = "pal@example.com"


@pytest.fixture
def env():
    vm = FakeVm()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy())
    return app.test_client(), vm, app


def sign_in_with_google(monkeypatch, client, email, verified=True, name="Jo"):
    """Play Google's side of the sign-in: the callback receives this identity."""
    monkeypatch.setattr(auth.oauth.google, "authorize_access_token",
                        lambda: {"userinfo": {"email": email, "email_verified": verified, "name": name}})
    return client.get("/auth/callback")


def session_of(client):
    with client.session_transaction() as s:
        return dict(s)


def queue(vm):
    return json.loads(vm.metadata.get("portal-requests", "{}"))


def request_card(page: str) -> str:
    """The first request card on the Admin page (from its heading to the end of that card's form)."""
    start = page.index("Asking for access")
    return page[start:page.index("</form>", start)]


def as_visitor(client, email, ip="1.1.1.1", name=""):
    with client.session_transaction() as s:
        s.clear()
        s["visitor"] = {"email": email, "name": name}
    return {"X-Forwarded-For": f"6.6.6.6, {ip}"}          # only the last hop counts: the caller can't pick their own IP


# ── the store ─────────────────────────────────────────────────────────────────
def test_asking_again_adds_games_and_keeps_the_note_unless_there_is_a_new_one():
    vm = FakeVm()
    store = access_mod.Requests(vm)
    assert store.submit(" Jo@Example.com ", "Jo", ["valheim", "not-a-game"], "from the Discord") == "new"
    assert store.submit("jo@example.com", "", ["factorio"], "") == "updated"
    entry = json.loads(vm.metadata["portal-requests"])["jo@example.com"]
    assert entry["games"] == ["valheim", "factorio"] and entry["note"] == "from the Discord" and entry["name"] == "Jo"
    store.submit("jo@example.com", "Jo", [], "a newer note")
    assert store.get("jo@example.com")["note"] == "a newer note" and store.count() == 1


def test_what_people_type_is_one_short_plain_line():
    store = access_mod.Requests(FakeVm())
    store.submit("a@b.co", "N" * 200, [], "line one\nline\ttwo\x00 " + "x" * 500)
    entry = store.get("a@b.co")
    assert len(entry["name"]) == access_mod.NAME_MAX and len(entry["note"]) == access_mod.NOTE_MAX
    assert "\n" not in entry["note"] and "\x00" not in entry["note"] and "\t" not in entry["note"]
    # invisible characters can't disguise a name or reorder what the admin reads (bidi override, zero-width, BOM)
    assert access_mod.tidy("Ap‮ple​⁦x﻿", 80) == "Applex"
    assert access_mod.tidy("a b c", 80) == "a b c"


def test_a_full_queue_refuses_new_people_instead_of_evicting_genuine_requests():
    store = access_mod.Requests(FakeVm())
    for i in range(access_mod.MAX_ENTRIES):
        store.submit(f"p{i}@x.co", "", ["valheim"], "")
    with pytest.raises(access_mod.Full):
        store.submit("newcomer@x.co", "", ["valheim"], "")
    assert "p0@x.co" in store.all() and "newcomer@x.co" not in store.all()      # the oldest is still there
    assert store.submit("p0@x.co", "", ["factorio"], "") == "updated"           # and someone already waiting can still add to theirs
    assert store.get("p0@x.co")["games"] == ["valheim", "factorio"]


def test_old_requests_expire_so_the_queue_cannot_clog_forever():
    from datetime import datetime, timedelta, timezone
    now = [datetime(2026, 9, 30, tzinfo=timezone.utc)]
    vm = FakeVm()
    store = access_mod.Requests(vm, clock=lambda: now[0])
    for i in range(access_mod.MAX_ENTRIES):
        store.submit(f"p{i}@x.co", "", ["valheim"], "")
    now[0] += timedelta(days=access_mod.EXPIRE_DAYS + 1)
    assert store.submit("fresh@x.co", "", ["valheim"], "") == "new"             # the stale ones made room
    assert list(json.loads(vm.metadata["portal-requests"])) == ["fresh@x.co"]


def test_answering_takes_only_what_was_granted_off_a_request():
    store = access_mod.Requests(FakeVm())
    store.submit("jo@x.co", "Jo", ["valheim", "factorio"], "hi")
    assert store.answer("jo@x.co", ["valheim", "skippy"]) == ["factorio"]         # partly answered: the rest stays waiting
    assert store.get("jo@x.co")["games"] == ["factorio"] and store.get("jo@x.co")["note"] == "hi"
    assert store.answer("jo@x.co", ["skippy"]) == ["factorio"]                     # nothing they asked for: unchanged
    assert store.answer("jo@x.co", ["factorio"]) == [] and store.get("jo@x.co") is None
    assert store.answer("nobody@x.co", ["valheim"]) == []


def test_a_failed_write_is_reported_as_unavailable_and_changes_nothing():
    vm = FakeVm()
    store = access_mod.Requests(vm)
    store.submit("keep@x.co", "", ["valheim"], "")
    before = vm.metadata["portal-requests"]
    vm.set_metadata = lambda key, value: (_ for _ in ()).throw(RuntimeError("412 fingerprint conflict"))
    for call in (lambda: store.submit("new@x.co", "", ["valheim"], ""), lambda: store.answer("keep@x.co", ["valheim"]),
                 lambda: store.dismiss("keep@x.co")):
        with pytest.raises(access_mod.Unavailable):
            call()
    assert vm.metadata["portal-requests"] == before


def test_zalgo_text_and_blank_looking_characters_are_cleaned():
    zalgo = "E" + "".join(chr(0x300 + i) for i in range(80))
    assert access_mod.tidy(zalgo, 80) == "E" + "".join(chr(0x300 + i) for i in range(access_mod.MAX_MARKS))
    assert access_mod.tidy("AㅤB⠀C͏D️E\U000e0100FᅟGᅠHﾠI", 80) == "ABCDEFGHI"
    assert access_mod.tidy("Renée é 中文", 80) == "Renée é 中文"     # real accents and CJK are left alone


def test_dismiss_and_bad_metadata():
    vm = FakeVm()
    store = access_mod.Requests(vm)
    store.submit("a@b.co", "", [], "")
    assert store.dismiss("A@B.co") is True and store.dismiss("a@b.co") is False and store.count() == 0
    vm.metadata["portal-requests"] = "{ not json"
    assert access_mod.Requests(vm).all() == {}
    vm.metadata["portal-requests"] = json.dumps(["a list", {"x": 1}])
    assert access_mod.Requests(vm).all() == {}


def test_a_failed_read_never_turns_into_a_write_that_wipes_the_queue():
    vm = FakeVm()
    store = access_mod.Requests(vm)
    store.submit("keep@x.co", "", ["valheim"], "")
    store.all()                                                       # warm the display cache, as a busy page would
    before = vm.metadata["portal-requests"]
    vm.get_metadata = lambda key: (_ for _ in ()).throw(OSError("the VM API is down"))
    with pytest.raises(access_mod.Unavailable):
        store.submit("new@x.co", "", ["factorio"], "")
    with pytest.raises(access_mod.Unavailable):
        store.dismiss("keep@x.co")
    assert vm.metadata["portal-requests"] == before                   # nothing written, nothing lost
    assert "keep@x.co" in store.all()                                 # and the page still shows what it last saw


def test_simultaneous_requests_are_all_kept():
    class SlowVm(FakeVm):
        def get_metadata(self, key):
            value = super().get_metadata(key)
            time.sleep(0.002)                                         # a read-modify-write window wide enough to lose updates
            return value
    vm = SlowVm()
    store = access_mod.Requests(vm)
    threads = [threading.Thread(target=store.submit, args=(f"p{i}@x.co", "", ["valheim"], "")) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(json.loads(vm.metadata["portal-requests"])) == 20


# ── from the Discord link to the request ──────────────────────────────────────
def test_a_discord_link_to_a_game_survives_the_trip_through_google(env, monkeypatch):
    client, _, _ = env
    _add_friend(client, PAL, ["valheim"])
    client.get("/logout")
    resp = client.get("/games/valheim")                               # what the pinned post links to, signed out
    assert resp.status_code == 302 and resp.location.endswith("/")
    assert session_of(client)["next"] == "/games/valheim"
    done = sign_in_with_google(monkeypatch, client, PAL)
    assert done.status_code == 302 and done.location.endswith("/games/valheim")      # straight to the game
    assert "next" not in session_of(client)


def test_someone_who_signs_in_for_a_game_they_lack_is_offered_a_request_for_it(env, monkeypatch):
    client, _, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    client.get("/logout")
    client.get("/games/valheim")
    done = sign_in_with_google(monkeypatch, client, PAL)
    assert done.location.endswith("/request-access?game=valheim")
    page = client.get(done.location).data.decode()
    assert PAL in page and 'value="valheim" checked' in page and "Send request" in page
    assert "Valheim</b> isn't on your account yet" in page            # it says why they landed here
    assert 'value="dragonwilds"' not in page                          # they already have that one


def test_a_stranger_signed_in_with_google_can_ask_and_is_never_let_in(env, monkeypatch):
    client, vm, _ = env
    client.get("/games/valheim")
    done = sign_in_with_google(monkeypatch, client, STRANGER)
    assert done.location.endswith("/unauthorized")
    where = client.get("/unauthorized")
    assert where.status_code == 302 and where.location.endswith("/request-access?game=valheim")
    assert "user" not in session_of(client) and session_of(client)["visitor"]["email"] == STRANGER
    assert client.get("/games").status_code == 302                    # asking is all a visitor can do
    assert client.get("/admin").status_code == 302 and client.get("/me").status_code == 302
    page = client.get(where.location).data.decode()
    assert STRANGER in page and 'value="valheim" checked' in page
    sent = client.post("/request-access", data={"game": "valheim", "games": ["valheim", "factorio", "bogus"],
                                                "note": "Jo from the Discord"})
    assert sent.status_code == 302 and sent.location.endswith("/request-access?game=valheim")   # not a bare POST result
    [(email, entry)] = queue(vm).items()
    assert email == STRANGER and entry["games"] == ["valheim", "factorio"] and entry["note"] == "Jo from the Discord"
    assert entry["name"] == "Jo"
    page = client.get(sent.location).data.decode()
    assert "Request sent" in page and "Your request is with Aaron" in page and "Update my request" in page
    assert "his dashboard" not in page and "the Captain" not in page      # no pronoun guessed, and friends know him as Aaron


def test_reloading_the_confirmation_does_not_send_the_request_again(env, monkeypatch):
    client, vm, app = env
    sign_in_with_google(monkeypatch, client, STRANGER)
    sent = client.post("/request-access", data={"games": ["valheim"]})
    stored = vm.metadata["portal-requests"]
    for _ in range(5):                                                # a browser reload of the page it landed on
        assert client.get(sent.location).status_code == 200
    assert vm.metadata["portal-requests"] == stored


def test_a_request_must_name_a_game(env, monkeypatch):
    client, vm, _ = env
    sign_in_with_google(monkeypatch, client, STRANGER)
    resp = client.post("/request-access", data={"note": "let me in"})
    assert resp.status_code == 400 and "Tick at least one game" in resp.data.decode()
    assert "portal-requests" not in vm.metadata


def test_an_unverified_google_email_gets_no_request_form(env, monkeypatch):
    client, _, _ = env
    resp = sign_in_with_google(monkeypatch, client, STRANGER, verified=False)
    assert resp.location.endswith("/unauthorized")
    assert "visitor" not in session_of(client) and client.get("/unauthorized").status_code == 403


def test_the_email_asked_for_is_the_one_google_verified_never_one_typed(env, monkeypatch):
    client, vm, _ = env
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim"], "email": "victim@example.com", "who": "victim@example.com"})
    assert list(queue(vm)) == [STRANGER]


def test_signing_in_as_a_different_unlisted_account_replaces_the_old_identity(env, monkeypatch):
    client, vm, _ = env
    _add_friend(client, PAL, ["valheim"])
    login(client, PAL)                                                # a live friend session...
    sign_in_with_google(monkeypatch, client, STRANGER)                # ...then "use a different account"
    state = session_of(client)
    assert "user" not in state and state["visitor"]["email"] == STRANGER
    client.post("/request-access", data={"games": ["factorio"]})
    assert list(queue(vm)) == [STRANGER]                              # filed under who signed in last, not the friend


def test_the_use_a_different_account_link_makes_google_ask_which(env, monkeypatch):
    client, _, _ = env
    seen = []
    monkeypatch.setattr(auth.oauth.google, "authorize_redirect",
                        lambda uri, **extra: seen.append(extra) or ("", 302))
    client.get("/login")
    client.get("/login?switch=1")
    assert seen == [{}, {"prompt": "select_account"}]


def test_signed_out_people_are_sent_to_google_and_nothing_is_stored(env):
    client, vm, _ = env
    page = client.get("/request-access?game=valheim").data.decode()
    assert "Sign in with Google" in page and "Send request" not in page and "You came for <b>Valheim</b>" in page
    assert session_of(client)["next"] == "/games/valheim"
    assert client.post("/request-access", data={"games": ["valheim"]}).status_code == 200
    assert "portal-requests" not in vm.metadata


def test_only_a_games_own_page_can_be_remembered_as_where_to_go_back_to(env):
    client, _, app = env
    with app.test_request_context():
        for bad in ("//evil.example.com", "https://evil.example.com/games/valheim", "/games/valheim/../admin",
                    "/games/valheim/discord", "/games/nonesuch", "/admin", "/games/", "/games/VALHEIM", ""):
            auth.remember_next(bad)
            assert auth.wanted_game() is None, bad
        auth.remember_next("/games/valheim")
        assert auth.wanted_game() == "valheim"
    client.get("/games/valheim/discord")                              # a sub-page of a game: not remembered either
    assert "next" not in session_of(client)


def test_a_person_may_ask_a_few_times_an_hour_and_each_person_has_their_own_allowance(env):
    client, vm, _ = env
    headers = as_visitor(client, STRANGER, ip="1.1.1.1")
    codes = [client.post("/request-access", data={"games": ["valheim"]}, headers=headers).status_code for _ in range(4)]
    assert codes == [302, 302, 302, 429]
    assert "already sent a few requests" in client.post("/request-access", data={"games": ["valheim"]},
                                                        headers=headers).data.decode()
    assert queue(vm)[STRANGER]["games"] == ["valheim"]                # the earlier requests stand
    other = as_visitor(client, "someone.else@example.com", ip="2.2.2.2")     # a different person is unaffected
    assert client.post("/request-access", data={"games": ["valheim"]}, headers=other).status_code == 302


def test_one_address_and_the_whole_queue_have_caps_and_say_the_request_was_not_saved(env):
    client, vm, _ = env
    codes = []
    for n in range(8):                                                # one address, many accounts
        headers = as_visitor(client, f"bot{n}@example.com", ip="9.9.9.9")
        resp = client.post("/request-access", data={"games": ["valheim"]}, headers=headers)
        codes.append(resp.status_code)
    assert codes == [302] * 6 + [429] * 2
    assert "wasn't saved" in html.unescape(resp.data.decode()) and "bot7@example.com" not in queue(vm)
    codes = []
    for n in range(70):                                               # many addresses: the overall cap still holds
        headers = as_visitor(client, f"crowd{n}@example.com", ip=f"7.7.{n // 200}.{n % 200}")
        codes.append(client.post("/request-access", data={"games": ["valheim"]}, headers=headers).status_code)
    assert codes.count(429) >= 10 and len(queue(vm)) <= access_mod.MAX_ENTRIES


def test_a_flood_of_repeated_game_values_is_harmless(env):
    client, vm, _ = env
    headers = as_visitor(client, STRANGER)
    resp = client.post("/request-access", data={"games": ["valheim"] * 5000 + ["factorio"] * 5000}, headers=headers)
    assert resp.status_code == 302 and queue(vm)[STRANGER]["games"] == ["valheim"]    # only the first few values are read


def test_someone_who_has_every_game_queues_nothing(env):
    client, vm, _ = env
    login(client, ADMIN)                                              # a core user sees every game
    page = client.get("/request-access").data.decode()
    assert "already have every game" in page and "Send request" not in page
    assert client.post("/request-access", data={"games": ["valheim"]}).status_code == 302
    assert "portal-requests" not in vm.metadata


def test_a_friend_can_ask_for_another_game_and_cant_ask_for_one_they_have(env):
    client, vm, _ = env
    _add_friend(client, PAL, ["dragonwilds"])
    login(client, PAL)
    client.post("/request-access", data={"games": ["dragonwilds", "factorio"]})
    assert queue(vm)[PAL]["games"] == ["factorio"]


# ── an approved person gets in without asking twice ───────────────────────────
def test_an_approved_visitor_is_let_in_by_the_link_they_already_had_no_second_sign_in(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    client.get("/games/valheim")
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim"]})
    assert client.get("/games/valheim").status_code == 302           # not yet: still only a visitor
    login(admin, ADMIN)
    admin.post("/admin/friends", data={"email": STRANGER, "portals": ["valheim"], "from_request": "1"})
    resp = client.get("/games/valheim")                              # the same browser, the same Discord link
    assert resp.status_code == 200
    state = session_of(client)
    assert state["user"]["email"] == STRANGER and "visitor" not in state


def test_the_request_page_of_an_approved_visitor_says_they_are_in(env, monkeypatch):
    client, _, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER)
    login(admin, ADMIN)
    admin.post("/admin/friends", data={"email": STRANGER, "portals": ["valheim"]})
    page = client.get("/request-access?game=valheim").data.decode()
    assert "You're in" in page and 'href="/games/valheim"' in page
    assert client.get("/unauthorized").location.endswith("/")        # and the generic bounce sends them home, signed in


# ── the Admin page: the same place friends are added ──────────────────────────
def test_admin_sees_requests_in_the_friends_section_and_can_approve_one(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER, name="<script>alert(1)</script>")
    client.post("/request-access", data={"games": ["valheim"], "note": "<b>hi</b> from the Discord"})
    login(admin, ADMIN)
    page = admin.get("/admin").data.decode()
    friends_at = page.index('id="friends"')
    assert page.index("Asking for access (1)") > friends_at and STRANGER in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page          # what they typed is never markup
    assert "<b>hi</b>" not in page
    card = request_card(page)
    assert 'value="valheim" checked' in card and 'name="from_request" value="1"' in card
    assert "Approve" in card and re.search(r'formaction="/admin/requests/dismiss"[^>]*>\s*Dismiss', card)   # both buttons are wired
    admin.post("/admin/friends", data={"email": STRANGER, "portals": ["valheim"], "from_request": "1"})   # Approve
    assert STRANGER in json.loads(vm.metadata["portal-friends"]) and queue(vm) == {}
    assert "Asking for access" not in admin.get("/admin").data.decode()


def test_approving_an_existing_friends_request_adds_to_what_they_have(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    _add_friend(admin, PAL, ["dragonwilds", "skippy", "experiments"])
    login(client, PAL)
    client.post("/request-access", data={"games": ["valheim"]})
    card = request_card(admin.get("/admin").data.decode())
    assert "Already a friend, with:" in card and "Approve adds to that" in card
    for ticked in ("dragonwilds", "skippy", "experiments", "valheim"):                 # what they hold, plus what they asked for
        assert f'value="{ticked}" checked' in card, ticked
    ticks = re.findall(r'name="portals" value="(\w+)" (checked)?', card)
    assert sorted(v for v, c in ticks if c) == ["dragonwilds", "experiments", "skippy", "valheim"]
    admin.post("/admin/friends", data={"email": PAL, "portals": ["dragonwilds", "skippy", "experiments", "valheim"],
                                       "from_request": "1"})
    assert json.loads(vm.metadata["portal-friends"])[PAL]["portals"] == ["dragonwilds", "valheim", "skippy", "experiments"] \
        or sorted(json.loads(vm.metadata["portal-friends"])[PAL]["portals"]) == ["dragonwilds", "experiments", "skippy", "valheim"]
    assert queue(vm) == {}


def test_approve_with_nothing_ticked_grants_nothing_and_keeps_the_request(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim"]})
    login(admin, ADMIN)
    resp = admin.post("/admin/friends", data={"email": STRANGER, "from_request": "1"}, follow_redirects=True)
    assert "Tick at least one thing to approve" in resp.data.decode()
    assert STRANGER in queue(vm) and "portal-friends" not in vm.metadata


def test_saving_a_friends_own_card_only_answers_a_request_it_covers(env):
    client, vm, app = env
    admin = app.test_client()
    _add_friend(admin, PAL, ["dragonwilds"])
    login(client, PAL)
    client.post("/request-access", data={"games": ["valheim"]})
    admin.post("/admin/friends", data={"email": PAL, "portals": ["dragonwilds", "skippy"]})     # Save access: no Valheim
    assert PAL in queue(vm)                                           # still waiting: Valheim wasn't granted
    admin.post("/admin/friends", data={"email": PAL, "portals": ["dragonwilds", "valheim"]})    # Save access: now it is
    assert queue(vm) == {}


def test_a_request_that_no_longer_needs_an_answer_disappears_from_the_queue_and_the_badge(env):
    client, vm, app = env
    admin = app.test_client()
    store = app.extensions["access_requests"]
    store.submit(PAL, "", ["valheim"], "")
    store.submit(ADMIN, "", ["valheim"], "")                          # a core user asking: nothing to grant
    app.extensions["friends"].upsert(PAL, ["valheim"], added_by="test")   # granted, but the cleanup never ran
    login(admin, ADMIN)
    assert "Asking for access" not in admin.get("/admin").data.decode()
    assert "asking" not in admin.get("/").data.decode() and "asking for access" not in admin.get("/me").data.decode()


def test_approving_someone_who_is_already_core_clears_the_request(env):
    client, vm, app = env
    admin = app.test_client()
    app.extensions["access_requests"].submit(ADMIN, "", ["valheim"], "")
    login(admin, ADMIN)
    resp = admin.post("/admin/friends", data={"email": ADMIN, "portals": ["valheim"], "from_request": "1"}, follow_redirects=True)
    assert "already sees everything" in resp.data.decode() and queue(vm) == {}


def test_a_failed_cleanup_after_approving_still_grants_and_does_not_error(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim"]})
    monkeypatch.setattr(app.extensions["access_requests"], "dismiss",
                        lambda email: (_ for _ in ()).throw(access_mod.Unavailable("down")))
    login(admin, ADMIN)
    resp = admin.post("/admin/friends", data={"email": STRANGER, "portals": ["valheim"], "from_request": "1"})
    assert resp.status_code == 302 and STRANGER in json.loads(vm.metadata["portal-friends"])


def test_admin_can_dismiss_a_request_and_only_admins_can(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim"]})
    other = app.test_client()
    login(other, FRIEND)
    assert other.post("/admin/requests/dismiss", data={"email": STRANGER}).status_code == 403
    assert STRANGER in queue(vm)
    login(admin, ADMIN)
    admin.post("/admin/requests/dismiss", data={"email": STRANGER})
    assert queue(vm) == {} and "portal-friends" not in vm.metadata     # dismissed, nothing granted


def test_the_captain_is_told_on_the_launcher_and_the_dashboard(env, monkeypatch):
    client, _, app = env
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim"]})
    admin = app.test_client()
    login(admin, ADMIN)
    launcher = admin.get("/").data.decode()
    assert "1 asking" in launcher and 'href="/admin#friends"' in launcher
    assert "1 person is asking for access" in admin.get("/me").data.decode()
    friend = app.test_client()
    login(friend, FRIEND)
    assert "asking" not in friend.get("/").data.decode()              # friends never see the queue


def test_someone_who_loses_access_can_ask_to_get_it_back(env):
    client, _, app = env
    admin = app.test_client()
    _add_friend(admin, PAL, ["valheim"])
    login(client, PAL)                                                # signed in, with access...
    admin.post("/admin/friends/remove", data={"email": PAL})          # ...then the Captain removes them
    resp = client.get("/games")                                       # their still-open session, next page load
    assert resp.status_code == 302 and resp.location.endswith("/unauthorized")
    assert client.get("/unauthorized").location.endswith("/request-access")
    assert session_of(client)["visitor"]["email"] == PAL


# ── the pinned Discord post ───────────────────────────────────────────────────
GAME_IDS = list(discord_admin.notify.GAMES)


@pytest.mark.parametrize("game", GAME_IDS)
@pytest.mark.parametrize("online", [True, False])
def test_every_pinned_post_links_to_its_games_page_and_says_how_to_ask_for_access(game, online):
    notify = discord_admin.notify
    payload = notify.board(game, online, "1.2.3.4", "30", "", {}, 1_700_000_000)
    [field] = [f for f in payload["embeds"][0]["fields"] if f["name"] == "Maker Portal"]
    name = notify.GAMES[game][0]
    assert f"[Open {name} on the Maker Portal]({notify.PORTAL_URL}/games/{game})" in field["value"]
    assert "Request access" in field["value"] and len(field["value"]) < 1024        # Discord's field limit
    total = sum(len(f["name"]) + len(f["value"]) for f in payload["embeds"][0]["fields"])
    assert total < 5000 and len(payload["embeds"][0]["fields"]) <= 25             # and the embed stays within Discord's limits


def test_the_pin_uses_the_portal_address_from_the_env_only_if_it_is_a_plain_https_origin():
    notify = discord_admin.notify
    link = lambda env: [f for f in notify.board("valheim", False, "", "30", "", env, 1)["embeds"][0]["fields"]
                        if f["name"] == "Maker Portal"][0]["value"]
    assert "(https://games.example.com/games/valheim)" in link({"PORTAL_URL": "https://games.example.com/"})
    for bad in ("http://games.example.com", "javascript:alert(1)", "https://x.com/evil path", "https://a.com)[b](https://c.com",
                "https://user@evil.com", "ftp://x", "", "   "):
        assert f"({notify.PORTAL_URL}/games/valheim)" in link({"PORTAL_URL": bad}), bad


class FakeDiscord:
    """Just enough of Discord's REST API for setup(): records every call."""

    def __init__(self):
        self.calls, self.boards = [], {}

    def __call__(self, method, path, payload=None, auth=True):
        self.calls.append((method, path, payload))
        if path == "/guilds/g1/channels" and method == "GET":
            return []
        if path == "/guilds/g1/channels":
            return {"id": f"ch{len(self.calls)}", "name": payload["name"], "type": payload["type"]}
        if path.endswith("/invites"):
            return {"code": "inv"}
        if path.endswith("/webhooks") and method == "GET":
            return []
        if path.endswith("/webhooks"):
            return {"id": "1", "token": "t"}
        if "?wait=true" in path:
            self.boards[path] = payload
            return {"id": f"post{len(self.boards)}"}
        return {}


def test_discord_setup_pins_posts_that_link_to_the_address_the_portal_is_served_on(monkeypatch):
    fake = FakeDiscord()
    monkeypatch.setattr(discord_admin, "Discord", lambda token: fake)
    updates = discord_admin.setup("tok", "inv", ["valheim"], {"DISCORD_GUILD_ID": "g1"}, portal_url="https://console.example.org/")
    assert updates["PORTAL_URL"] == "https://console.example.org"       # kept, so the VM's own edits link the same place
    [payload] = fake.boards.values()
    values = [f["value"] for f in payload["embeds"][0]["fields"]]
    assert any("(https://console.example.org/games/valheim)" in v for v in values)
    assert any(c[0] == "PUT" and c[1].endswith("/pins/post1") for c in fake.calls)      # and it is pinned


def test_discord_setup_ignores_a_portal_address_that_is_not_a_plain_https_origin(monkeypatch):
    fake = FakeDiscord()
    monkeypatch.setattr(discord_admin, "Discord", lambda token: fake)
    updates = discord_admin.setup("tok", "inv", ["valheim"], {"DISCORD_GUILD_ID": "g1"}, portal_url="http://localhost/")
    assert "PORTAL_URL" not in updates
    assert f"({discord_admin.notify.PORTAL_URL}/games/valheim)" in json.dumps(list(fake.boards.values()))


def test_a_configured_portal_address_beats_whichever_host_ran_setup(env, monkeypatch):
    client, vm, app = env
    seen = []
    monkeypatch.setattr(discord_admin, "setup", lambda token, invite, games, env, portal_url=None: seen.append(portal_url) or {})
    app.config["DISCORD_BOT_TOKEN"] = "tok"
    login(client, ADMIN)
    client.post("/admin/discord")
    app.config["PORTAL_URL"] = "https://console.example.org"
    client.post("/admin/discord")
    assert seen == ["http://localhost/", "https://console.example.org"]


def test_terraform_never_wipes_what_the_portal_keeps_in_metadata():
    from pathlib import Path
    tf = (Path(__file__).resolve().parents[2] / "infra" / "gcp-games" / "main.tf").read_text(encoding="utf-8")
    block = re.search(r"ignore_changes\s*=\s*\[(.*?)\n\s*\]", tf, re.S).group(1)
    for key in ("portal-friends", "portal-requests", "portal-imports", "portal-home", "portal-experiments"):
        assert f'metadata["{key}"]' in block, key
