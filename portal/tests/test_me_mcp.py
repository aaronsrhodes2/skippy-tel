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


def test_the_dashboard_is_private_mcp_and_home_and_the_rest_is_in_the_graveyard(env):
    client, _, app = env
    login(client, ADMIN)
    page = client.get("/me").data.decode()
    assert 'id="mcp"' in page and 'id="home"' in page and page.index('data-pane="mcp"') < page.index('data-pane="home"')
    for retired in ("server", "core", "activity-pane", "watcher", "spend", "people", "experiments", "links"):
        assert f'id="{retired}"' not in page and f'data-pane="{retired}"' not in page, retired     # retired, not deleted
    assert 'id="people"' in client.get("/me?all=1").data.decode()                                      # ?all=1 shows them again


def test_experiments_live_on_the_main_portal_with_the_captains_show_hide_there(env):
    client, _, app = env
    anon = app.test_client()
    assert "Show or hide" not in anon.get("/experiments").data.decode()                                # the public page never mentions it
    login(client, ADMIN)
    page = client.get("/experiments").data.decode()
    assert "Show or hide (Captain)" in page and 'name="next" value="experiments"' in page
    resp = client.post("/me/experiments", data={"slug": "nbody", "next": "experiments"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/experiments")
