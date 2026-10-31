"""mentat/research_stage.py: concept questions are researched and cited instead of answered with the nearest tool's number (2026-10-31, the one-way speed of light).

The search and the model are stubbed; what is tested is the gate, the rules an answer must keep, and where the stage sits in /ask. Needs a sigma-ground checkout for the
gate's measurement against its question suites and for the app tests, like the other app tests."""
import importlib.util
import json
from pathlib import Path

import pytest

import research_stage as rs

ONE_WAY = "does light travel away from us at the same speed it travels back when we measure it?"
SOURCES = [{"title": "One-way speed of light", "url": "https://en.wikipedia.org/wiki/One-way_speed_of_light",
            "content": "The one-way speed of light cannot be measured independently of how distant clocks are synchronised; only the two-way average speed c is measured. Einstein synchronisation sets the two directions equal by convention."},
           {"title": "Reichenbach", "url": "https://plato.stanford.edu/entries/spacetime-convch/",
            "content": "Any epsilon between 0 and 1 gives a consistent theory with the same predictions: the equality of the one-way speeds is a convention."}]
GOOD = "It depends. Every experiment measures the two-way speed [1]; the equality of the two one-way speeds is a convention of how clocks are synchronised [1][2]."


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(rs, "SEARCH_URL", "http://searx")
    monkeypatch.setattr(rs, "MODEL", "stub")


def run(on, question=ONE_WAY, sources=SOURCES, text=GOOD, miss=None):
    return rs.answer(question, ollama_url="http://o", miss=miss, search_fn=lambda q: sources, ask_fn=lambda *a: text, rewrite_fn=lambda q, u: "one-way speed of light synchronisation convention")


# ── the gate ──
@pytest.mark.parametrize("q", [ONE_WAY, "Is the Earth flat?", "Can entangled particles communicate faster than light?", "Why is the sky blue?",
                               "Will a perpetual motion machine work if I use sufficiently strong neodymium magnets?", "How does a magnet attract iron?"])
def test_concept_questions_are_gated(q):
    assert rs.is_conceptual(q)


@pytest.mark.parametrize("q", ["How fast does light travel in glass?", "What is the escape velocity of Mars?", "Does a 5 kg ball fall faster than a 1 kg ball?", "Is 17 prime?",
                               "How does sin(u)cos(u) change with u? Give the derivative.", "Calculate the energy of a 500 nm photon", "How many moons does Jupiter have?",
                               "Escape velocity of Mars", "derivative of x^2 sin x"])
def test_questions_that_ask_for_a_number_are_not_gated(q):
    assert not rs.is_conceptual(q)


def test_the_gate_fires_on_none_of_the_suite_questions_that_ask_for_a_value():
    sg = pytest.importorskip("sigma_ground")
    root = Path(sg.__file__).resolve().parent
    questions = [x["question"] for x in json.loads((root / "mcp/benchmark/questions.json").read_text(encoding="utf-8"))]
    suite = root.parent / "local-cache" / "router_math_suite.json"
    if suite.is_file():
        questions += [x.get("question") or x.get("q") for x in json.loads(suite.read_text(encoding="utf-8"))["items"]]
    assert len(questions) >= 150
    assert [q for q in questions if rs.is_conceptual(q)] == []                 # 0 of the 150 physics and 0 of the 329 maths questions, measured 2026-10-31


# ── the rules an answer keeps ──
def test_a_cited_answer_is_returned_with_its_sources_and_no_value(on):
    r = run(on)
    assert r["value"] is None and r["inputs"]["route"] == "research" and r["provenance_tag"] is None
    assert [s["n"] for s in r["inputs"]["sources"]] == [1, 2]
    assert "computed nothing" in r["notes"] and "https://en.wikipedia.org" in r["notes"] and r["display"] == GOOD
    assert "not a sigma-ground computation" in r["source"]


@pytest.mark.parametrize("text,why", [("It depends on the convention.", "no citation"), ("It depends [3].", "cites a result that does not exist"),
                                      ("It depends [0].", "cites a result that does not exist"),
                                      ("The one-way speed is 299792458 m/s in every direction [1].", "number(s) not in the question or the sources"),
                                      ("NO_ANSWER", "the results do not answer the question"), ("", "the results do not answer the question")])
def test_an_answer_that_breaks_a_rule_is_a_miss(on, text, why):
    miss = {}
    assert run(on, text=text, miss=miss) is None
    assert miss["reason"].startswith(why)


def test_a_number_that_is_in_a_source_or_the_question_is_allowed(on):
    s = [dict(SOURCES[0], content="Light takes 1.28 seconds to reach the Moon [sic]."), SOURCES[1]]
    assert run(on, question="Does light take 2 s?", sources=s, text="It takes 1.28 seconds [1], not 2 [1].") is not None


def test_the_technical_query_is_searched_first_and_a_failed_rewrite_costs_only_that(on):
    asked = []
    def search_fn(q):
        asked.append(q)
        return SOURCES
    r = rs.answer(ONE_WAY, ollama_url="http://o", search_fn=search_fn, ask_fn=lambda *a: GOOD, rewrite_fn=lambda q, u: "one-way speed of light synchronisation convention")
    assert asked == ["one-way speed of light synchronisation convention", ONE_WAY] and r["inputs"]["queries"] == asked
    assert len(r["inputs"]["sources"]) == 2                                      # the same two pages from both searches are read once
    def broken(q, u):
        raise OSError("model down")
    miss, asked[:] = {}, []
    assert rs.answer(ONE_WAY, ollama_url="http://o", miss=miss, search_fn=search_fn, ask_fn=lambda *a: GOOD, rewrite_fn=broken) is not None
    assert asked == [ONE_WAY] and miss["rewrite"].startswith("OSError")


def test_one_search_result_or_a_dead_search_is_a_miss_not_a_crash(on):
    miss = {}
    assert run(on, sources=SOURCES[:1], miss=miss) is None and "fewer than two" in miss["reason"]
    def boom(q):
        raise OSError("down")
    miss = {}
    assert rs.answer(ONE_WAY, ollama_url="http://o", miss=miss, search_fn=boom) is None and miss["reason"].startswith("search: OSError")


def test_social_sites_are_dropped_and_authorities_come_first():
    found = [{"url": "https://www.reddit.com/r/AskPhysics/x"}, {"url": "https://www.facebook.com/p/1"}, {"url": "https://blog.example.com/a"},
             {"url": "https://en.wikipedia.org/wiki/One-way_speed_of_light"}, {"url": "https://physics.stackexchange.com/q/1"}, {"url": "https://web.mit.edu/x"}]
    assert [f["url"] for f in rs.rank(found)] == ["https://en.wikipedia.org/wiki/One-way_speed_of_light", "https://physics.stackexchange.com/q/1", "https://web.mit.edu/x", "https://blog.example.com/a"]


def test_unset_means_off(monkeypatch):
    monkeypatch.setattr(rs, "SEARCH_URL", "")
    miss = {}
    assert not rs.enabled() and rs.answer(ONE_WAY, ollama_url="http://o", miss=miss) is None and miss["reason"] == "research stage off"


# ── where it sits in /ask ──
@pytest.fixture(scope="module")
def mentat():
    pytest.importorskip("mcp")
    pytest.importorskip("sigma_ground")
    path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("mentat_app_research", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def live(mentat, monkeypatch, on):
    monkeypatch.setattr(mentat, "USE_LLM", True)
    monkeypatch.setattr(mentat, "OLLAMA_URL", "http://o")
    monkeypatch.setattr(mentat, "_qwen_ready", lambda: True)
    monkeypatch.setattr(mentat.router_stage, "MODEL", "stub-router")                 # the router stage is on, with its answer stubbed per test
    monkeypatch.setattr(rs, "search", lambda q: SOURCES)
    monkeypatch.setattr(rs, "rewrite", lambda q, u: "one-way speed of light")
    monkeypatch.setattr(rs, "_ask_model", lambda *a: GOOD)
    return mentat


def test_a_concept_question_is_researched_and_the_router_never_sees_it(live, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the router answered a concept question")
    monkeypatch.setattr(live.router_stage, "answer", boom)
    r = live._ask(ONE_WAY)
    assert r["inputs"]["route"] == "research" and r["value"] is None


def test_a_concept_question_the_search_cannot_help_falls_through_to_the_old_path(live, monkeypatch):
    monkeypatch.setattr(rs, "search", lambda q: [])
    seen = {}
    monkeypatch.setattr(live.router_stage, "answer", lambda q, **k: seen.setdefault("routed", True) and None)
    r = live._ask(ONE_WAY)
    assert seen.get("routed") and r["inputs"]["router_miss"]["research"]["reason"].startswith("fewer than two")


def test_a_question_asking_for_a_value_is_not_researched(live, monkeypatch):
    monkeypatch.setattr(live.router_stage, "answer", lambda q, **k: {"value": 5.0, "inputs": {"route": "router"}})
    assert live._ask("What is the speed of sound in helium at room temperature?")["inputs"]["route"] == "router"


def test_the_last_stop_researches_what_no_stage_answered(live, monkeypatch):
    monkeypatch.setattr(live.router_stage, "answer", lambda q, **k: None)
    r = live._ask("What would a banana weigh on a neutron star's surface?")
    assert r["inputs"]["route"] == "research" and r["interpreter"] == "research stub"
