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

import experiments  # noqa: E402

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


def test_the_distillation_branch_reads_the_committed_day_files_and_shows_queued_days(env, tmp_path, monkeypatch):
    # sigma-ground misc/DISTILLATION_BRANCH_PLAN.md: a scored day, a queued day, the causes grouped; Wolfram is a yardstick column only
    folder = tmp_path / "benchmark" / "distill"
    folder.mkdir(parents=True)
    (folder / "distill-2026-10-30.json").write_text(json.dumps({
        "suite": "distill-2026-10-30",
        "summary": {"n": 3, "judged": 2, "mentat_right": 1, "mentat_misses": {"wrong_value": 1}, "unverified": 1, "unasked": 0,
                    "wolfram": {"answered": 2, "no_result": 1, "unasked": 0, "agrees_with_us": 1, "disagrees_with_us": 0}, "mentat_ref": "abc1234def"},
        "rows": [{"id": "a", "question": "integrate x^2 from 0 to 1", "cause": None, "expected_tool": "integrate_expr"},
                 {"id": "b", "question": "derivative of exp(-3*x)", "cause": "wrong_value", "expected_tool": "differentiate_expr"},
                 {"id": "c", "question": "molar mass of sulfuric acid", "cause": "unverified", "expected_tool": None}]}), encoding="utf-8")
    (folder / "distill-2026-10-31.set.json").write_text(json.dumps({"suite": "distill-2026-10-31", "generator": {"made": {"math": 20, "physics": 12}},
                                                                     "items": [{"id": str(i)} for i in range(32)]}), encoding="utf-8")
    monkeypatch.setattr(experiments, "ROOT", tmp_path)
    d = experiments.distillation()
    assert [x["day"] for x in d["days"]] == ["2026-10-30", "2026-10-31"] and d["days"][1]["pending"] and d["days"][1]["n"] == 32
    assert d["days"][0]["right"] == 1 and d["days"][0]["wa_answered"] == 2 and d["days"][0]["ref"] == "abc1234"
    assert d["causes"] == [{"cause": "wrong_value", "tool": "differentiate_expr", "rows": 1, "examples": ["derivative of exp(-3*x)"]}]
    assert d["total"] == {"n": 3, "judged": 2, "right": 1, "wa_answered": 2, "wa_agrees": 1}
    client, _, _ = env
    page = client.get("/experiments/benchmark").data.decode()
    assert "Improvement distillation" in page and "2026-10-30" in page and "queued: being asked" in page and "wrong value" in page
    assert "nothing it answers is used to improve Mentat" in page
    monkeypatch.setattr(experiments, "ROOT", tmp_path / "nowhere")
    assert experiments.distillation()["days"] == [] and "No days yet." in client.get("/experiments/benchmark").data.decode()


def test_every_minebench_run_can_be_reached_however_late_its_pictures_load(env):
    """The pager counted its pages once, before the pictures arrived, and stopped at page 4 of 9 (runs 4 to 8 out of reach). Now it
    counts again whenever something in the flow loads, and each picture holds its place before it loads."""
    script = (STATIC / "bridge.js").read_text(encoding="utf-8")
    assert 'flow.addEventListener("load", refresh, true)' in script
    client, _, _ = env
    page = client.get("/experiments/minebench").get_data(as_text=True)
    assert page.count('<img class="views" width="1800" height="1320"') == len(experiments.MINEBENCH_RUNS)
