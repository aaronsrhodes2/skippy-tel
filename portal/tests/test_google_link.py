"""Connect Google for Skippy (google_link.py): admin only, told apart from a normal sign-in by its state, the refresh token kept
on the portal and never in a page or a log line, and skippy-pc's /home/google/access opened only by the home token."""
import json
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import auth  # noqa: E402
import google_link  # noqa: E402
from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login, _pair  # noqa: E402

REFRESH = "1//refresh-SECRET-do-not-leak"
ACCESS = "ya29.access-SECRET"


class FakeGoogle:
    """Google's token and revoke endpoints."""

    def __init__(self):
        self.calls, self.answer = [], (200, {"access_token": ACCESS, "expires_in": 3599})

    def __call__(self, url, data):
        self.calls.append((url, dict(data)))
        if url == google_link.REVOKE_URL:
            return 200, {}
        return self.answer


@pytest.fixture
def world(monkeypatch):
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    fake = FakeGoogle()
    app.extensions["google_link"].post = fake
    sent = {}

    def redirect_to_google(redirect_uri, **kwargs):              # what the connect key asks Google for, without the network
        sent.update(kwargs, redirect_uri=redirect_uri)
        from flask import redirect
        return redirect("https://accounts.google.com/o/oauth2/v2/auth?state=" + kwargs.get("state", ""))
    monkeypatch.setattr(auth.oauth.google, "authorize_redirect", redirect_to_google)
    return app, app.test_client(), fake, sent


def answer(monkeypatch, email=ADMIN, refresh=REFRESH, scope=" ".join(["openid", *google_link.SCOPES]), verified=True):
    token = {"access_token": ACCESS, "expires_in": 3599, "scope": scope,
             "userinfo": {"email": email, "email_verified": verified, "name": "Captain"}}
    if refresh:
        token["refresh_token"] = refresh
    monkeypatch.setattr(auth.oauth.google, "authorize_access_token", lambda **kw: token)


def connect(client):
    r = client.get("/admin/google/connect")
    assert r.status_code == 302 and r.location.startswith("https://accounts.google.com/")
    return parse_qs(urlparse(r.location).query)["state"][0]


def test_connect_is_admin_only_and_asks_read_only_offline_access_at_the_existing_callback(world):
    app, client, _, sent = world
    assert client.get("/admin/google/connect").status_code == 302 and not sent           # signed out: the sign-in, not Google
    login(client, FRIEND)
    assert client.get("/admin/google/connect").status_code == 403 and not sent
    assert client.post("/admin/google/disconnect").status_code == 403
    login(client, ADMIN)
    connect(client)
    assert sent["redirect_uri"].endswith("/auth/callback")                               # the registered one: no new redirect URI
    scopes = sent["scope"].split()
    assert set(google_link.SCOPES) <= set(scopes) and {"openid", "email"} <= set(scopes)
    assert all(s.endswith(".readonly") for s in scopes if s.startswith("https://"))      # read only, every one
    assert sent["access_type"] == "offline" and sent["prompt"] == "consent" and sent["include_granted_scopes"] == "true"


def test_the_callback_keeps_the_grant_without_changing_the_sign_in_and_never_shows_or_logs_the_token(world, monkeypatch, capsys):
    app, client, fake, _ = world
    login(client, ADMIN)
    state = connect(client)
    answer(monkeypatch)
    r = client.get(f"/auth/callback?state={state}&code=x")
    assert r.status_code == 302 and r.location.endswith("/admin#google")
    with client.session_transaction() as s:
        assert s["user"]["email"] == ADMIN and google_link.STATE_KEY not in s
        assert REFRESH not in json.dumps(dict(s), default=str)
    rec = app.extensions["google_link"].record()
    assert rec["refresh_token"] == REFRESH and rec["email"] == ADMIN
    page = client.get("/admin").data.decode()
    assert "Connected:" in page and ADMIN in page and REFRESH not in page and ACCESS not in page
    out = capsys.readouterr()
    assert REFRESH not in out.out + out.err and ACCESS not in out.out + out.err


def test_a_normal_sign_in_is_not_taken_for_the_connect_flow(world, monkeypatch):
    app, client, _, _ = world
    login(client, ADMIN)
    connect(client)                                       # pressed Connect, then signed in normally instead (another state)
    answer(monkeypatch, scope="openid email profile", refresh=None)
    r = client.get("/auth/callback?state=some-other-state&code=x")
    assert r.status_code == 302 and "/admin" not in r.location
    assert app.extensions["google_link"].record() is None


def test_only_an_admins_own_verified_account_is_kept(world, monkeypatch):
    app, client, _, _ = world
    login(client, ADMIN)
    for email, verified in ((FRIEND, True), ("stranger@example.com", True), (ADMIN, False)):
        state = connect(client)
        answer(monkeypatch, email=email, verified=verified)
        client.get(f"/auth/callback?state={state}&code=x")
        assert app.extensions["google_link"].record() is None, email
        with client.session_transaction() as s:
            assert s["user"]["email"] == ADMIN                       # the sign-in is never switched to the other account


def test_a_friend_cannot_finish_the_flow_with_a_stolen_state(world, monkeypatch):
    app, client, _, _ = world
    login(client, FRIEND)
    with client.session_transaction() as s:
        s[google_link.STATE_KEY] = "planted"
    answer(monkeypatch)
    assert client.get("/auth/callback?state=planted&code=x").status_code == 403
    assert app.extensions["google_link"].record() is None


def test_the_home_route_needs_the_home_token_and_hands_out_short_lived_access(world, monkeypatch):
    app, client, fake, _ = world
    assert client.get("/home/google/access").status_code == 401
    token = _pair(client)
    home = {"Authorization": f"Bearer {token}"}
    assert client.get("/home/google/access").status_code == 401                         # the Captain's own cookie doesn't open it
    assert client.get("/home/google/access", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/home/google/access", headers=home).get_json() == {
        "connected": False, "why": "not connected: press Connect Google for Skippy in Admin"}
    app.extensions["google_link"].save(ADMIN, {"refresh_token": REFRESH, "scope": " ".join(google_link.SCOPES)})
    got = client.get("/home/google/access", headers=home).get_json()
    assert got["connected"] and got["access_token"] == ACCESS and 0 < got["expires_in"] <= 3600 and "refresh_token" not in got
    url, data = fake.calls[-1]
    assert url == google_link.TOKEN_URL and data["client_id"] == "id" and data["client_secret"] == "secret"
    assert data["refresh_token"] == REFRESH and data["grant_type"] == "refresh_token"
    client.get("/home/google/access", headers=home)
    assert len(fake.calls) == 1                                                          # cached while it lasts


def test_a_withdrawn_grant_says_so_and_disconnect_revokes_it(world):
    app, client, fake, _ = world
    link = app.extensions["google_link"]
    link.save(ADMIN, {"refresh_token": REFRESH, "scope": " ".join(google_link.SCOPES)})
    fake.answer = (400, {"error": "invalid_grant"})
    assert link.access()["connected"] is False and link.state()["broken"]
    login(client, ADMIN)
    assert "Connect Google for Skippy" in client.get("/admin").data.decode()
    client.post("/admin/google/disconnect")
    assert link.record() is None and fake.calls[-1] == (google_link.REVOKE_URL, {"token": REFRESH})
