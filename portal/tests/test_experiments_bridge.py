"""The experiment demo pages on the bridge: the N-body flip-book (a page of plates drawn from one data file made by a real run of
sigma-ground's shootout), the double-slit, the Mentat playground and the benchmark. What is checked without a browser: the markup, the
data file's shape and provenance, that the script calculates no physics, and that nothing scrolls."""
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

from test_app import env  # noqa: E402,F401

PORTAL = Path(__file__).resolve().parents[1]
STATIC = PORTAL / "static"
DATA = STATIC / "demos" / "nbody" / "flipbook.json"


def test_the_nbody_page_is_a_flipbook_of_plates_with_the_earlier_results_kept_as_they_were(env):
    client, _, _ = env
    page = client.get("/experiments/nbody").data.decode()
    assert '<body class="bridge"' in page and "/static/nbody-flipbook.js" in page and "/static/nbody.css" in page
    for need in ('id="plate"', 'id="scrub"', 'id="prev"', 'id="next"', 'id="play"', 'id="frame"', 'id="cap-t"', 'data-flipbook-url="/static/demos/nbody/flipbook.json"'):
        assert need in page, need
    for src in ("src-earlier", "src-score", "src-fig1", "src-fig2"):                    # the earlier recording, hidden, shown as plates
        assert f'id="{src}"' in page, src
    assert "ΔE/E = 1.5×10⁻⁶" in page and "worst is Mercury at 74%" in page             # as it was recorded, including what doesn't flatter it
    assert client.get("/static/demos/nbody/flipbook.json").status_code == 200


def test_the_flipbooks_data_is_a_real_run_with_its_provenance_and_a_consistent_shape():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    meta = d["meta"]
    assert re.fullmatch(r"[0-9a-f]{40}", meta["sigma_ground"]["head"]), "the run says which commit of sigma-ground it ran"
    assert meta["ran_at_utc"].endswith("Z") and meta["horizon_yr"] == 5.0 and len(meta["bodies"]) == 29
    assert meta["parent"]["Io"] == "Jupiter" and "Mercury" in meta["bodies"]
    names = {p["name"] for p in meta["predictors"]}
    assert {"pure_newton", "standard", "over_physics", "jpl_de440", "kepler"} <= names
    for pred, bodies in d["new"].items():
        for body, c in bodies.items():
            n = len(c["years"])
            assert n >= 2 and all(len(c[k]) == n for k in ("med", "q1", "q3", "n")), (pred, body)
            assert all(v >= 0 for v in c["med"]) and all(a <= b + 1e-18 for a, b in zip(c["q1"], c["q3"])), (pred, body)
    assert d["old"], "the earlier run is kept for the before-and-after plate"
    inner, jov = d["frames"]["inner"], d["frames"]["jovian"]
    assert len(inner["jds"]) == 61 and len(jov["jds"]) == 91                           # five years monthly, ninety days daily
    assert {"Mercury", "Venus", "Earth", "Mars"} <= set(inner["bodies"]) and {"Jupiter", "Io", "Europa", "Ganymede", "Callisto"} <= set(jov["bodies"])
    assert all(len(v) == len(inner["jds"]) for v in inner["bodies"].values()) and inner["truth"]["Earth"]


def test_the_flipbook_script_calculates_no_physics_and_makes_one_request():
    js = (STATIC / "nbody-flipbook.js").read_text(encoding="utf-8")
    assert not re.search(r"299792458|6\.674|1\.989|149597870|AU_KM|\bGM\b|Math\.sqrt|Math\.pow\(", js)   # no constants, no formulas: it draws what the run measured
    assert re.findall(r"fetch\(([\w.]+)", js) == ["root.dataset.flipbookUrl"]
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "nbody.css").read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"overflow(?:-y)?\s*:\s*(?:auto|scroll)", css) and "gradient" not in css


def test_the_other_demos_are_bridge_pages_that_keep_what_they_had(env):
    client, _, _ = env
    mentat = client.get("/experiments/mentat").data.decode()
    for need in ('id="search"', 'id="domain-key"', 'id="host"', 'id="toolsheet"', 'id="tool"', "Bridge.PList", "no unsourced numbers"):
        assert need in mentat, need
    assert "Search about 220 tools" in mentat and "{{" not in mentat.split("<script>")[1]      # rendered, not leaked
    bench = client.get("/experiments/benchmark").data.decode()
    assert "Bridge.pager" in bench and 'class="pg-flow"' in bench and "Mentat vs Wolfram Alpha vs Gemini" in bench
    slit = client.get("/experiments/double-slit").data.decode()
    for need in ('id="btn-play"', 'id="btn-d0"', 'id="d-sl"', 'id="about"', 'class="keybar buttons"'):
        assert need in slit, need
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "experiments.css").read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"overflow(?:-y)?\s*:\s*(?:auto|scroll)", css) and "gradient" not in css
