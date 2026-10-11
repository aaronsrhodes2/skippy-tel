"""/me's private MCP pane: only the Captain, only names from his own list (the bridge checks that), results readable only by the asker."""
import pytest

from test_app import ADMIN, FRIEND, SETTINGS, FakeSkippy, FakeVm, login

import app as portal


@pytest.fixture
def env():
    app = portal.create_app({**SETTINGS, "CHAT_ENABLED": True}, vm=FakeVm(), skippy=FakeSkippy())
    return app.test_client(), None, app


def pair_and_report(client, app):
    home = app.extensions["home"]
    home.accept({"capabilities": {}})            # skippy-pc reports in
    return home


def test_the_mcp_routes_are_the_captains_alone(env):
    client, _, app = env
    anon = app.test_client()
    for method, url in (("post", "/me/mcp"), ("get", "/me/mcp/result?job=abcdef12")):
        assert getattr(anon, method)(url, json={}).status_code in (302, 401, 403), url
    login(client, FRIEND)
    for method, url in (("post", "/me/mcp"), ("get", "/me/mcp/result?job=abcdef12")):
        assert getattr(client, method)(url, json={}).status_code in (302, 401, 403), url


def test_the_captain_gets_job_ids_for_the_list_and_for_a_named_test_and_only_he_reads_them(env):
    client, _, app = env
    home = pair_and_report(client, app)
    login(client, ADMIN)
    listing = client.post("/me/mcp", json={}).get_json()["job"]
    test = client.post("/me/mcp", json={"name": "ollama"}).get_json()["job"]
    kinds = [(j["kind"], j.get("name")) for j in home.jobs_reply()["jobs"]]
    assert ("mcp_list", None) in kinds and ("mcp_test", "ollama") in kinds
    assert client.get(f"/me/mcp/result?job={listing}").get_json()["state"] == "running"
    home.finish(listing, {"result": {"servers": [{"name": "ollama", "transport": "stdio", "via": "uvx"}]}})
    assert client.get(f"/me/mcp/result?job={listing}").get_json()["result"]["servers"][0]["name"] == "ollama"
    assert test != listing
    friend = app.test_client()
    login(friend, FRIEND)
    assert friend.get(f"/me/mcp/result?job={listing}").status_code in (302, 401, 403)


def test_a_bad_name_is_refused_before_any_job_exists_and_a_silent_home_is_a_reason(env):
    client, _, app = env
    login(client, ADMIN)
    assert client.post("/me/mcp", json={}).status_code == 503                 # skippy-pc isn't reporting yet
    pair_and_report(client, app)
    for bad in ("rm -rf /", "a" * 61, "", 5, ["x"], "../x"):
        assert client.post("/me/mcp", json={"name": bad}).status_code == 400, bad
    assert client.get("/me/mcp/result?job=../../x").get_json() == {"state": "unknown"}


def test_the_dashboard_opens_on_mcp_and_home_and_the_pane_is_there(env):
    client, _, app = env
    login(client, ADMIN)
    page = client.get("/me").data.decode()
    assert 'id="mcp"' in page and page.index('data-pane="mcp"') < page.index('data-pane="home"') < page.index('data-pane="server"')
