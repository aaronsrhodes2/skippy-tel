"""Fixes from the second review of the access-request feature: each one has a test that fails without it."""
import html
import json
import re

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import access_requests as access_mod  # noqa: E402
import auth  # noqa: E402
import discord_admin  # noqa: E402
import friends as friends_mod  # noqa: E402
from authlib.integrations.base_client.errors import MismatchingStateError  # noqa: E402
from test_access_requests import (PAL, STRANGER, as_visitor, env, queue, request_card,  # noqa: E402,F401
                                  session_of, sign_in_with_google)
from test_app import ADMIN, FakeVm, _add_friend, login  # noqa: E402


# ── the admin page can't be scripted by an email address ──────────────────────
HOSTILE = "a'-alert`1`-'@evil.example.com"


def test_an_email_can_never_reach_javascript_on_the_admin_page(env):
    client, vm, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch=pytest.MonkeyPatch(), client=client, email=HOSTILE, name="<img src=x onerror=alert(1)>")
    client.post("/request-access", data={"games": ["valheim"], "note": "\"><script>alert(2)</script>"})
    login(admin, ADMIN)
    admin.post("/admin/friends", data={"email": HOSTILE, "portals": ["valheim"], "from_request": "1"})
    page = admin.get("/admin").data.decode()
    handlers = re.findall(r'\son(?:click|submit|change|load|error)="([^"]*)"', page)
    assert not any("evil.example.com" in h or "alert" in h for h in handlers)      # nothing of theirs inside a handler
    carried = [html.unescape(v) for v in re.findall(r'data-email="([^"]*)"', page)]
    assert HOSTILE in carried                                                                     # it travels as an escaped attribute
    assert "<img src=x" not in page and "<script>alert(2)" not in page


# ── approving is safe from a stale page ───────────────────────────────────────
def test_approving_from_a_stale_card_can_never_take_a_friends_access_away(env):
    client, vm, app = env
    admin = app.test_client()
    _add_friend(admin, PAL, ["dragonwilds", "skippy"])
    login(client, PAL)
    client.post("/request-access", data={"games": ["valheim"]})
    _add_friend(admin, PAL, ["dragonwilds", "skippy", "experiments"])                  # changed after the card was drawn
    admin.post("/admin/friends", data={"email": PAL, "portals": ["valheim"], "from_request": "1"})   # the stale card's tick
    from catalog import PORTALS
    assert json.loads(vm.metadata["portal-friends"])[PAL]["portals"] == [
        p for p in PORTALS if p in ("dragonwilds", "valheim", "skippy", "experiments")]


def test_approving_part_of_a_request_leaves_the_rest_waiting_and_says_so(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim", "factorio"], "note": "hi"})
    login(admin, ADMIN)
    resp = admin.post("/admin/friends", data={"email": STRANGER, "portals": ["valheim"], "from_request": "1"},
                      follow_redirects=True)
    assert "Still asking for: Factorio" in resp.data.decode()
    assert queue(vm)[STRANGER]["games"] == ["factorio"] and STRANGER in json.loads(vm.metadata["portal-friends"])


def test_approving_something_they_did_not_ask_for_keeps_the_request(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim"]})
    login(admin, ADMIN)
    admin.post("/admin/friends", data={"email": STRANGER, "portals": ["skippy"], "from_request": "1"})
    assert queue(vm)[STRANGER]["games"] == ["valheim"]


# ── the VM API misbehaving never turns into a 500, a wipe, or a lost grant ────
def test_a_failed_queue_write_gives_friendly_answers_not_500s(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim"]})
    real_set = vm.set_metadata

    def flaky(key, value):
        if key == "portal-requests":
            raise RuntimeError("412 fingerprint conflict")
        return real_set(key, value)
    vm.set_metadata = flaky
    login(admin, ADMIN)
    approved = admin.post("/admin/friends", data={"email": STRANGER, "portals": ["valheim"], "from_request": "1"})
    assert approved.status_code == 302 and STRANGER in json.loads(vm.metadata["portal-friends"])     # granted, no error page
    assert admin.post("/admin/requests/dismiss", data={"email": STRANGER}, follow_redirects=True).status_code == 200
    other = as_visitor(client, "someone@example.com")
    resp = client.post("/request-access", data={"games": ["valheim"]}, headers=other)
    assert resp.status_code == 503 and "Couldn&#39;t save that just now" in resp.data.decode()


def test_the_friends_list_is_never_wiped_by_a_failed_read(env):
    client, vm, app = env
    admin = app.test_client()
    for who in ("a@x.co", "b@x.co", "c@x.co"):
        _add_friend(admin, who, ["valheim"])
    before = vm.metadata["portal-friends"]
    friends = friends_mod.Friends(vm)
    vm.get_metadata = lambda key: (_ for _ in ()).throw(OSError("compute API down"))
    with pytest.raises(friends_mod.Unavailable):
        friends.upsert("d@x.co", ["valheim"], added_by="test")
    with pytest.raises(friends_mod.Unavailable):
        friends.remove("a@x.co")
    assert vm.metadata["portal-friends"] == before
    vm.get_metadata = lambda key: "{ corrupt"                       # unreadable stored text is never overwritten either
    with pytest.raises(friends_mod.Unavailable):
        friends.upsert("d@x.co", ["valheim"], added_by="test")
    assert vm.metadata["portal-friends"] == before


def test_the_admin_page_says_so_when_the_friends_list_cant_be_saved(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    login(admin, ADMIN)
    monkeypatch.setattr(app.extensions["friends"], "_fresh", lambda: (_ for _ in ()).throw(friends_mod.Unavailable("x")))
    resp = admin.post("/admin/friends", data={"email": "new@x.co", "portals": ["valheim"]}, follow_redirects=True)
    assert resp.status_code == 200 and "nothing was changed" in resp.data.decode()
    resp = admin.post("/admin/friends/remove", data={"email": "new@x.co"}, follow_redirects=True)
    assert resp.status_code == 200 and "nothing was changed" in resp.data.decode()


# ── limits ────────────────────────────────────────────────────────────────────
def test_a_full_queue_says_so_and_still_lets_a_waiting_person_update(env):
    client, vm, app = env
    store = app.extensions["access_requests"]
    for i in range(access_mod.MAX_ENTRIES):
        store.submit(f"p{i}@x.co", "", ["valheim"], "")
    newcomer = as_visitor(client, "newcomer@example.com", ip="4.4.4.4")
    resp = client.post("/request-access", data={"games": ["valheim"]}, headers=newcomer)
    assert resp.status_code == 503 and "list is full" in html.unescape(resp.data.decode())
    waiting = as_visitor(client, "p0@x.co", ip="5.5.5.5")
    assert client.post("/request-access", data={"games": ["factorio"]}, headers=waiting).status_code == 302
    assert queue(vm)["p0@x.co"]["games"] == ["valheim", "factorio"]


def test_a_crowded_address_cannot_burn_a_real_persons_own_allowance(env):
    client, vm, _ = env
    for n in range(6):                                                # six other people use up this address's hour
        client.post("/request-access", data={"games": ["valheim"]}, headers=as_visitor(client, f"other{n}@example.com", ip="8.8.8.8"))
    mine = as_visitor(client, "genuine@example.com", ip="8.8.8.8")
    codes = [client.post("/request-access", data={"games": ["valheim"]}, headers=mine).status_code for _ in range(3)]
    assert codes == [429, 429, 429]                                   # refused for the address, not counted against them
    elsewhere = as_visitor(client, "genuine@example.com", ip="9.9.9.9")
    assert [client.post("/request-access", data={"games": ["valheim"]}, headers=elsewhere).status_code for _ in range(3)] == [302, 302, 302]


def test_an_ipv6_address_is_limited_as_its_whole_network(env):
    client, vm, _ = env
    codes = []
    for n in range(8):                                                # eight addresses in one /64
        headers = as_visitor(client, f"v6-{n}@example.com", ip=f"2001:db8:1:2::{n + 1:x}")
        codes.append(client.post("/request-access", data={"games": ["valheim"]}, headers=headers).status_code)
    assert codes == [302] * 6 + [429] * 2
    other_network = as_visitor(client, "v6-other@example.com", ip="2001:db8:1:3::1")
    assert client.post("/request-access", data={"games": ["valheim"]}, headers=other_network).status_code == 302


def test_a_head_request_never_saves_anything(env, monkeypatch):
    client, vm, _ = env
    sign_in_with_google(monkeypatch, client, STRANGER)
    resp = client.open("/request-access", method="HEAD", data={"games": ["valheim"]})
    assert resp.status_code == 200 and "portal-requests" not in vm.metadata


# ── the request page ──────────────────────────────────────────────────────────
def test_games_already_asked_for_are_ticked_when_the_page_is_reopened(env, monkeypatch):
    client, _, _ = env
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim", "factorio"]})
    page = client.get("/request-access").data.decode()               # no ?game=: only the stored request says what to tick
    assert 'value="valheim" checked' in page and 'value="factorio" checked' in page
    assert 'value="satisfactory" checked' not in page


def test_asking_for_a_game_you_were_just_given_takes_you_to_the_page_not_an_error(env, monkeypatch):
    client, vm, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.get("/request-access?game=valheim")
    login(admin, ADMIN)
    admin.post("/admin/friends", data={"email": STRANGER, "portals": ["valheim"]})      # approved while the form was open
    resp = client.post("/request-access", data={"game": "valheim", "games": ["valheim"]})
    assert resp.status_code == 302 and "portal-requests" not in vm.metadata
    assert "You're in" in client.get(resp.location).data.decode()


# ── signed in but not (yet) on the list ───────────────────────────────────────
def test_a_waiting_visitor_who_reopens_a_link_goes_to_their_request_not_the_sign_in_page(env, monkeypatch):
    client, _, _ = env
    sign_in_with_google(monkeypatch, client, STRANGER)
    client.post("/request-access", data={"games": ["valheim"]})
    expected = {"/": "/request-access", "/games/valheim": "/unauthorized", "/games": "/unauthorized"}
    for url, goes_to in expected.items():                             # straight to their page, never round via the sign-in screen
        resp = client.get(url)
        assert resp.status_code == 302 and resp.location.endswith(goes_to), (url, resp.location)
        landing = client.get(resp.location, follow_redirects=True)
        assert landing.status_code == 200 and "Your request is with Aaron" in landing.data.decode(), url
    assert "sign out" in client.get("/request-access").data.decode()


def test_an_approved_visitor_opening_the_portal_root_is_just_in(env, monkeypatch):
    client, _, app = env
    admin = app.test_client()
    sign_in_with_google(monkeypatch, client, STRANGER)
    login(admin, ADMIN)
    admin.post("/admin/friends", data={"email": STRANGER, "portals": ["valheim"]})
    resp = client.get("/apps")
    assert resp.status_code == 200 and "Sign in with Google" not in resp.data.decode()
    assert session_of(client)["user"]["email"] == STRANGER


# ── sign-in edge cases ────────────────────────────────────────────────────────
def test_a_cancelled_or_expired_sign_in_is_a_friendly_message_not_a_500(env, monkeypatch):
    client, _, _ = env
    resp = client.get("/auth/callback?error=access_denied&state=x", follow_redirects=True)
    assert resp.status_code == 200 and "Sign-in was cancelled or timed out" in resp.data.decode()

    def stale():
        raise MismatchingStateError()
    monkeypatch.setattr(auth.oauth.google, "authorize_access_token", stale)
    resp = client.get("/auth/callback?code=abc&state=nope", follow_redirects=True)
    assert resp.status_code == 200 and "Sign-in was cancelled or timed out" in resp.data.decode()
    assert "user" not in session_of(client) and "visitor" not in session_of(client)


def test_a_verified_account_with_no_email_gets_nothing(env, monkeypatch):
    client, vm, _ = env
    resp = sign_in_with_google(monkeypatch, client, "", verified=True)
    assert resp.location.endswith("/unauthorized") and "visitor" not in session_of(client)
    assert "portal-requests" not in vm.metadata


def test_the_not_authorized_page_lets_you_pick_another_google_account(env, monkeypatch):
    client, _, _ = env
    sign_in_with_google(monkeypatch, client, STRANGER, verified=False)
    assert "login?switch=1" in client.get("/unauthorized").data.decode()


# ── the pinned post and the deploy settings ───────────────────────────────────
@pytest.mark.parametrize("bad", ["https://a.com:99999", "https://a.com:0", "https://a.com:١٢", "https://a.com:", "https://a.com:080000"])
def test_the_pin_rejects_ports_that_cannot_exist(bad):
    notify = discord_admin.notify
    assert notify.portal_url({"PORTAL_URL": bad}) == notify.PORTAL_URL


def test_the_pin_accepts_a_real_port():
    assert discord_admin.notify.portal_url({"PORTAL_URL": "https://games.example.com:8443/"}) == "https://games.example.com:8443"


def test_setup_refuses_a_configured_portal_address_the_pins_would_ignore(env, monkeypatch):
    client, vm, app = env
    called = []
    monkeypatch.setattr(discord_admin, "setup", lambda *a, **k: called.append(1) or {})
    app.config["DISCORD_BOT_TOKEN"] = "tok"
    app.config["PORTAL_URL"] = "portal.example.com/games"
    login(client, ADMIN)
    resp = client.post("/admin/discord", follow_redirects=True)
    assert "isn&#39;t a plain https" in resp.data.decode() and called == []
