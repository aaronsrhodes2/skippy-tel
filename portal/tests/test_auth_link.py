"""The glasses' sign-in link (authlink.py): the Captain mints a one-time link on /hud, OpenViture opens it and is signed in as him, once. Anything that is
not exactly that (an edited token, an old one, a spent one, a browser that is not OpenViture, a minter who is no longer an admin) is turned away."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import authlink  # noqa: E402
import tabletop  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login  # noqa: E402

OV = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36 OpenViture/0.9"}
CHROME = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36"}
USER = {"email": "cap@example.com", "name": "The Captain", "picture": None}


@pytest.fixture
def env(monkeypatch):
    store = tabletop.MemoryStore()
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy(), tabletop=tabletop.Tabletop(store))
    dms = []
    monkeypatch.setattr(app.extensions["notifier"], "alert", lambda text, image=None: dms.append(text) or True)
    c = app.test_client()
    login(c, ADMIN)
    return app, c, store, dms


def mint(c):
    return c.post("/auth/link/new", headers={"X-Link": "1"}, json={})


def token_of(r):
    return r.get_json()["url"].split("?t=")[1]


def test_the_captain_mints_a_link_on_the_canonical_address_and_a_friend_or_a_stranger_cannot(env):
    app, c, store, _ = env
    r = mint(c)
    d = r.get_json()
    assert r.status_code == 200 and d["expires_in"] == authlink.TTL_S and "/auth/link?t=" in d["url"] and d["url"].startswith("http")
    nonce, mac = token_of(r).split(".")
    assert authlink.NONCE.fullmatch(nonce) and authlink.MAC.fullmatch(mac)
    assert store.get(f"auth-links/{nonce}.json")                                          # kept in the bucket
    app.config["PORTAL_URL"] = "https://makerportal.live"
    assert mint(c).get_json()["url"].startswith("https://makerportal.live/auth/link?t=")
    assert c.post("/auth/link/new", json={}).status_code == 400                           # not from one of our pages (no X-Link)
    friend = app.test_client(); login(friend, FRIEND)
    assert friend.post("/auth/link/new", headers={"X-Link": "1"}, json={}).status_code == 403
    assert app.test_client().post("/auth/link/new", headers={"X-Link": "1"}, json={}).status_code in (302, 401, 403)
    assert c.get("/auth/link/new").status_code == 405                                      # a GET never mints


def test_openviture_opening_the_link_is_signed_in_as_the_minter_once_and_the_captain_is_told(env):
    app, c, store, dms = env
    token = token_of(mint(c))
    glasses = app.test_client()
    assert glasses.get("/hud").status_code in (302, 401, 403)                              # a browser with no cookies is nobody
    r = glasses.get(f"/auth/link?t={token}", headers=OV)
    assert r.status_code == 302 and r.headers["Location"].endswith("/hud")
    assert glasses.get("/hud").status_code == 200                                          # and now it is the Captain's
    with glasses.session_transaction() as s:
        assert s["user"]["email"] == ADMIN
    assert dms == ["The glasses signed in with a link you made."]
    assert not [k for k in store.files if k.startswith("auth-links/")]                    # spent: gone from the bucket
    other = app.test_client()
    assert other.get(f"/auth/link?t={token}", headers=OV).status_code == 403              # the same link, again
    assert other.get("/hud").status_code in (302, 401, 403) and len(dms) == 1


def test_a_browser_that_is_not_openviture_is_turned_away_without_spending_the_link(env):
    app, c, store, dms = env
    token = token_of(mint(c))
    chrome = app.test_client()
    for headers in (CHROME, {}):
        r = chrome.get(f"/auth/link?t={token}", headers=headers)
        assert r.status_code == 403 and b"OpenViture" in r.data
    assert chrome.get("/hud").status_code in (302, 401, 403) and dms == []
    assert app.test_client().get(f"/auth/link?t={token}", headers=OV).status_code == 302   # a chat app's preview did not burn it


def test_an_edited_old_or_made_up_token_is_refused(env):
    app, c, store, dms = env
    token = token_of(mint(c))
    nonce, mac = token.split(".")
    flipped = mac[:-1] + ("A" if mac[-1] != "A" else "B")
    glasses = app.test_client()
    for bad in ("", "x", nonce, f"{nonce}.", f"{nonce}.{flipped}", f"{nonce[:-1]}A.{mac}", "A" * 32 + "." + "A" * 22, token + "0", token.upper(), "../" + token):
        assert glasses.get("/auth/link", query_string={"t": bad}, headers=OV).status_code == 403, bad
    assert store.get(f"auth-links/{nonce}.json")                                           # the genuine one is untouched by all that
    assert glasses.get(f"/auth/link?t={token}", headers=OV).status_code == 302
    assert dms == ["The glasses signed in with a link you made."]


def test_a_link_dies_after_five_minutes(env):
    app, c, store, dms = env
    links = app.extensions["auth_links"]
    now = [1_800_000_000.0]
    links.clock = lambda: now[0]
    token = token_of(mint(c))
    now[0] += authlink.TTL_S - 1
    assert app.test_client().get(f"/auth/link?t={token}", headers=OV).status_code == 302   # the last second
    token = token_of(mint(c))
    now[0] += authlink.TTL_S + 1
    assert app.test_client().get(f"/auth/link?t={token}", headers=OV).status_code == 403
    assert len(dms) == 1


def test_a_minter_who_is_no_longer_an_admin_signs_nobody_in(env):
    app, c, store, dms = env
    token = token_of(mint(c))
    app.config["ADMIN_EMAILS"] = frozenset()
    glasses = app.test_client()
    assert glasses.get(f"/auth/link?t={token}", headers=OV).status_code == 403
    assert glasses.get("/hud").status_code in (302, 401, 403) and dms == []
    app.config["ADMIN_EMAILS"] = frozenset({ADMIN})
    assert glasses.get(f"/auth/link?t={token}", headers=OV).status_code == 403             # and it was spent by the attempt


def test_too_many_tries_from_one_address_are_stopped(env):
    app, c, *_ = env
    glasses = app.test_client()
    codes = [glasses.get("/auth/link?t=nope", headers=OV).status_code for _ in range(authlink.TRIES[0] + 3)]
    assert codes[:authlink.TRIES[0]] == [403] * authlink.TRIES[0] and codes[-1] == 429
    assert glasses.get(f"/auth/link?t={token_of(mint(c))}", headers=OV).status_code == 429   # even a good one, from that address


# ── the Links themselves ──
def links_with(store=None, secret=b"s3cret", now=None):
    t = now or [1_800_000_000.0]
    return authlink.Links(secret, store, lambda: t[0], log=lambda line: None), t


def test_a_redeploy_between_the_mint_and_the_use_loses_nothing_and_a_second_instance_cannot_spend_it_twice():
    store = tabletop.MemoryStore()
    old, now = links_with(store)
    new, _ = links_with(store, now=now)                                 # the same secret and bucket: the portal after a deploy
    token = old.mint(USER)
    assert new.redeem(token)["email"] == USER["email"]
    assert old.redeem(token) is None and new.redeem(token) is None       # spent in the bucket, whichever instance asks
    assert not store.files


def test_a_different_secret_never_accepts_a_token_and_does_not_touch_the_bucket():
    store = tabletop.MemoryStore()
    a, now = links_with(store, b"one")
    b, _ = links_with(store, b"two", now=now)
    token = a.mint(USER)
    assert b.redeem(token) is None
    assert len(store.files) == 1 and a.redeem(token)                     # still there for its own portal


def test_a_bucket_that_cannot_confirm_the_spend_refuses_and_one_that_cannot_keep_the_link_makes_none():
    class Flaky(tabletop.MemoryStore):
        fail = None

        def delete(self, path):
            if self.fail == "delete":
                raise OSError("bucket down")
            return super().delete(path)

        def put(self, path, data, content_type):
            if self.fail == "put":
                raise OSError("bucket down")
            return super().put(path, data, content_type)
    store = Flaky()
    links, _ = links_with(store)
    token = links.mint(USER)
    store.fail = "delete"
    assert links.redeem(token) is None                                    # cannot be sure it is spent: no session
    store.fail = "put"
    with pytest.raises(RuntimeError):
        links.mint(USER)


def test_a_mint_sweeps_out_the_links_nobody_used():
    store = tabletop.MemoryStore()
    links, now = links_with(store)
    old = [links.mint(USER) for _ in range(3)]
    now[0] += authlink.TTL_S + 60
    links.mint(USER)
    assert len(store.files) == 1                                          # only the new one is left in the bucket
    assert all(links.redeem(t) is None for t in old)


def test_without_a_bucket_links_live_in_memory_and_are_single_use():
    links, now = links_with(None)
    token = links.mint(USER)
    assert links.redeem(token)["name"] == "The Captain" and links.redeem(token) is None
