"""The road door (core/routes/routes.py): a calendar's messy location becomes the searches Nominatim can answer (business name and suite
dropped, the town only as a last, approximate, resort), a route is Valhalla's own km and seconds per leg, and /health is ready only when
both engines answer (an hour-long first import says so instead of failing)."""
import sys
import urllib.error
import urllib.parse

import pytest

from core_testlib import CORE, load

sys.path.insert(0, str(CORE / "routes"))
routes = load("routes/routes.py", "core_routes")


def test_a_calendar_location_becomes_searches_from_exact_to_the_town():
    got = routes.candidates("Blue Bottle Coffee, 66 Mint St Suite 4, San Francisco, CA 94103, USA")
    assert got == [("Blue Bottle Coffee, 66 Mint St Suite 4, San Francisco, CA 94103", False),
                   ("Blue Bottle Coffee, 66 Mint St, San Francisco, CA 94103", False),
                   ("66 Mint St, San Francisco, CA 94103", False),
                   ("San Francisco, CA 94103", True)]
    assert routes.candidates("Los Gatos, CA") == [("Los Gatos, CA", False)]          # never down to the state alone
    assert routes.candidates("  ") == []


def test_geocode_tries_each_search_until_one_answers_and_says_when_it_is_only_the_town():
    asked = []

    def get(url, timeout=15):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        asked.append(q)
        if q["q"] == "San Francisco, CA 94103":
            return [{"lat": "37.77", "lon": "-122.41", "display_name": "San Francisco, California", "addresstype": "city"}]
        return []
    got = routes.geocode("Nowhere Cafe, 1 Fake Way, San Francisco, CA 94103", near=(37.17, -122.14), bounded=True, get=get)
    assert got["lat"] == 37.77 and got["approx"] is True and "Nominatim" in got["source"]
    assert [a["q"] for a in asked] == ["Nowhere Cafe, 1 Fake Way, San Francisco, CA 94103", "1 Fake Way, San Francisco, CA 94103",
                                       "San Francisco, CA 94103", "1 Fake Way"]        # (the street, near the town: not there either)
    assert asked[0]["bounded"] == "1" and asked[0]["viewbox"] == "-123.1400,38.1700,-121.1400,36.1700"
    assert routes.geocode("Nowhere at all", get=lambda u, timeout=15: []) is None


def test_only_the_town_found_means_the_street_is_looked_for_near_that_town():
    """171 Geyer Rd's postal town is Scotts Valley, but OpenStreetMap files the road under Skypark: the full address and the street with
    its town find nothing, the town does, and then the street alone, close to it, does."""
    asked = []

    def get(url, timeout=15):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        asked.append(q)
        if q["q"] == "Scotts Valley, CA 95066":
            return [{"lat": "37.0511", "lon": "-122.0147", "display_name": "Scotts Valley", "addresstype": "town"}]
        if q["q"] == "171 Geyer Rd" and q.get("bounded") == "1":
            return [{"lat": "37.0576", "lon": "-122.0361", "display_name": "Geyer Road, Skypark", "addresstype": "road"}]
        return []
    got = routes.geocode("171 Geyer Rd, Scotts Valley, CA 95066, USA", near=(37.174, -122.142), get=get)
    assert (got["lat"], got["lon"], got["approx"]) == (37.0576, -122.0361, False) and got["query"] == "171 Geyer Rd (near Scotts Valley, CA 95066)"
    assert asked[-1]["viewbox"] == "-122.1147,37.1511,-121.9147,36.9511"          # within ~11 km of the town, not of home
    town_only = routes.geocode("Nowhere Rd 9, Scotts Valley, CA 95066", get=lambda u, timeout=15: get(u) if "Scotts" in u else [])
    assert town_only["approx"] is True


def test_a_route_is_valhallas_own_km_and_minutes_per_leg():
    sent = []

    def post(url, body, timeout=30):
        sent.append((url, body))
        return {"trip": {"summary": {"length": 60.5, "time": 3000.0},
                         "legs": [{"summary": {"length": 30.2, "time": 1490.0}}, {"summary": {"length": 30.3, "time": 1510.0}}]}}
    got = routes.route([[37.17, -122.14], [37.77, -122.41], [37.17, -122.14]], post=post, valhalla_status={"tileset_last_modified": 1793404800})
    assert got["km"] == 60.5 and got["minutes"] == 50 and got["legs"] == [{"km": 30.2, "minutes": 25}, {"km": 30.3, "minutes": 25}]
    assert sent[0][1]["costing"] == "auto" and sent[0][1]["units"] == "kilometers" and len(sent[0][1]["locations"]) == 3
    assert "no live traffic" in got["source"]
    for bad in ([], [[1, 2]], [[91, 0], [0, 0]], [["x", 0], [0, 0]]):
        with pytest.raises(ValueError):
            routes.route(bad, post=post)


def test_health_is_ready_only_when_both_engines_answer(monkeypatch):
    def importing(url, timeout=3):
        if "/status?format=json" in url:
            raise urllib.error.URLError("connection refused")
        return {"version": "3.9.1"}
    got = routes.READY.report(ready=True, deps={k: {"ready": v["ready"], "detail": v["detail"]}
                                               for k, v in routes.check_deps(importing, force=True).items()})
    assert got["ready"] is False and "imports California's addresses" in got["detail"]

    def both(url, timeout=3):
        return {"status": 0, "message": "OK"} if "format=json" in url else {"version": "3.9.1", "tileset_last_modified": 1}
    routes.check_deps(both, force=True)
    got = routes.health(get=both)
    assert got["ready"] is True and set(got["deps"]) == {"valhalla", "nominatim"} and routes.status_code(got) == 200 \
        if hasattr(routes, "status_code") else got["ready"]
