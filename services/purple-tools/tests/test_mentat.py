"""purple Skippy's Mentat tools (services/purple-tools/tools.py): the value always comes with its unit and source, a clarification is relayed (never
filled in), a refusal says why, and nothing raises."""
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402

VALUE = {"ok": True, "result": {"value": 11179.93, "units": "m/s", "formula": "v_esc = sqrt(2 G M / r)", "source": "sigma-ground (classical escape velocity)",
                                "interpreted_as": "escape_velocity(earth)", "notes": "Velocity needed to escape.", "uncertainty": None}}
NEEDS = {"ok": True, "result": {"value": None, "error": {"code": "needs_input", "message": "I'd assume copper for the ball."},
                                "clarification": {"follow_up": {"given": {"material_key": "copper", "radius_m": 0.05}, "question": "How long does it fall?"}}}}


def test_a_value_comes_with_its_unit_formula_and_source():
    seen = {}
    text = tools.ask_mentat({"question": "  escape velocity of Earth  "}, post=lambda url, body: seen.update(url=url, body=body) or VALUE)
    assert seen["url"].endswith("/ask") and seen["body"] == {"question": "escape velocity of Earth"}
    assert text.startswith("Answer: 11179.93 m/s") and "Source: sigma-ground (classical escape velocity)" in text and "Formula: v_esc" in text
    assert "None" not in text                                                                                       # no empty fields printed


def test_a_clarification_is_relayed_and_the_defaults_are_never_filled_in_by_the_agent():
    text = tools.ask_mentat({"question": "How long does a ball take to fall?"}, post=lambda url, body: NEEDS)
    assert "needs more from the person" in text and "copper" in text and "Do NOT fill them in yourself" in text and "only if they agree" in text
    seen = {}
    tools.ask_mentat({"question": "q", "given": {"radius_m": 0.05}}, post=lambda url, body: seen.update(body=body) or VALUE)
    assert seen["body"] == {"question": "q", "given": {"radius_m": 0.05}}
    tools.ask_mentat({"question": "q", "given": "not an object"}, post=lambda url, body: seen.update(body=body) or VALUE)
    assert seen["body"] == {"question": "q"}                                                                        # only an object of confirmed inputs goes through


def test_a_named_tool_is_called_with_its_arguments_and_the_name_is_checked():
    seen = {}
    tools.mentat_tool({"name": "schwarzschild_radius", "arguments": {"mass_kg": 1.989e30}}, post=lambda url, body: seen.update(url=url, body=body) or VALUE)
    assert seen["url"].endswith("/tools/schwarzschild_radius") and seen["body"] == {"mass_kg": 1.989e30}
    for bad in ("", "../admin", "a b", "X", "tool;rm"):
        try:
            tools.mentat_tool({"name": bad}, post=lambda url, body: VALUE)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_mentat_refusals_and_errors_say_why():
    assert "could not answer: bad thing" in tools.format_mentat({"result": {"value": None, "error": {"code": "x", "message": "bad thing"}}})
    assert "could not answer: That input looks like code" in tools.format_mentat({"ok": False, "error": "That input looks like code, not maths."})
    assert tools.format_mentat("junk") == "Mentat answered something unreadable."


def test_the_tool_list_can_be_searched_and_is_bounded():
    listing = [{"name": f"orbit_{i}", "description": "an orbit thing", "domain": "gravity", "input_schema": {"properties": {"mass_kg": {}, "r": {}}}} for i in range(60)]
    listing.append({"name": "band_gap_ev", "description": "semiconductor band gap", "domain": "materials", "input_schema": {"properties": {"material": {}}}})
    out = tools.mentat_tools({"search": "orbit"}, get=lambda url: listing)
    assert out.startswith("60 of 61 Mentat tools matching 'orbit'") and out.count("\n- ") == 40 and "orbit_0(mass_kg, r)" in out
    assert "band_gap_ev(material)" in tools.mentat_tools({"search": "materials"}, get=lambda url: listing)
    assert tools.mentat_tools({"search": "zzz"}, get=lambda url: listing) == "No Mentat tool matches 'zzz'."


def test_an_unreachable_mentat_is_words_not_a_crash(monkeypatch):
    def down(url, body, timeout=90.0):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(tools, "http_post_json", down)
    tools.TOOLS["ask_mentat"]["run"] = lambda a: tools.ask_mentat(a, post=down)
    text, is_error = tools.call("ask_mentat", {"question": "x"})
    assert is_error and "could not reach its service at home" in text
