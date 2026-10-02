"""The portal's other names send people to https://makerportal.live (one address for the Google sign-in to come back to);
the canonical name and the run.app fallback are served as they are."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
from test_app import SETTINGS, FakeSkippy, FakeVm  # noqa: E402


@pytest.fixture
def client():
    return portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy()).test_client()


@pytest.mark.parametrize("host", sorted(portal.OTHER_HOSTS))
def test_every_other_name_redirects_to_the_canonical_one_keeping_path_and_query(client, host):
    r = client.get("/music?x=1&y=two", headers={"Host": host})
    assert r.status_code == 301
    assert r.headers["Location"] == "https://makerportal.live/music?x=1&y=two"


def test_a_forwarded_host_is_what_counts(client):
    r = client.get("/", headers={"Host": "internal", "X-Forwarded-Host": "makerportal.studio"})
    assert r.headers["Location"] == "https://makerportal.live/"


@pytest.mark.parametrize("host", ["makerportal.live", "skippy-portal-1015050363686.us-west1.run.app"])
def test_the_canonical_name_and_the_run_app_fallback_are_served(client, host):
    assert client.get("/version", headers={"Host": host}).status_code == 200
