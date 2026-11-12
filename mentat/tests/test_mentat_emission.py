"""Mentat's emission rule in /ask (mentat/emission_stage.py): one named material plus a glow, emissivity or radiated-power cue goes to
thermal_glow, emissivity, or the chain emissivity > blackbody_total_power; nothing else does.

The tools are faked (a recorder); the tools themselves are tested in sigma-ground (sigma_ground/mcp/test_emissivity_tools.py). The miss that
started this: the trained router answered "what colour does iron glow at 1300 K" with Wien's peak wavelength and "how well does tungsten
radiate heat" with a blackbody's power, the material dropped.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

MENTAT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def stage():
    spec = importlib.util.spec_from_file_location("emission_stage", MENTAT / "emission_stage.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["emission_stage"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("question, tool, args", [
    ("What colour does iron glow at 1300 K?", "thermal_glow", {"material": "iron", "temperature_k": 1300.0}),
    ("What color is tungsten when it's heated to 3000 kelvin?", "thermal_glow", {"material": "tungsten", "temperature_k": 3000.0}),
    ("Is steel glowing at 600 °C?", "thermal_glow", {"material": "steel", "temperature_k": 873.15}),
    ("Is copper red-hot at 1800 F?", "thermal_glow", {"material": "copper", "temperature_k": pytest.approx(1255.37, abs=0.01)}),
    ("What is the total emissivity of alumina (aluminium oxide ceramic) at 300 K?", "emissivity",
     {"material": "alumina", "temperature_k": 300.0}),
    ("emissivity of rubber", "emissivity", {"material": "rubber"}),
    ("How well does polished tungsten radiate heat at 2000 K?", "emissivity", {"material": "tungsten", "temperature_k": 2000.0}),
    ("Spectral emissivity of gold at 550 nm and 1000 K?", "emissivity",
     {"material": "gold", "temperature_k": 1000.0, "wavelength_m": pytest.approx(550e-9)}),
    ("emittance of ice at 10 microns", "emissivity", {"material": "ice", "wavelength_m": pytest.approx(10e-6)}),
])
def test_reads_the_question(stage, question, tool, args):
    got_tool, got_args, why = stage.read(question)
    assert (got_tool, why) == (tool, "")
    assert got_args == args


def test_alumina_names_one_material(stage):
    """Two names for one material count once: the first name is the one passed (sigma-ground resolves both the same)."""
    tool, args, why = stage.read("emissivity of alumina (Al2O3) at 300 K")
    assert (tool, args, why) == ("emissivity", {"material": "alumina", "temperature_k": 300.0}, "")


@pytest.mark.parametrize("question, why", [
    ("What colour does iron glow?", "no temperature given"),
    ("How much power does a blackbody radiate at 1000 K?", "no material it knows is named"),
    ("Does gold glow brighter than silver at 1500 K?", "a comparison"),
    ("Emissivity of copper and of gold at 300 K", "more than one material named (copper, gold)"),
    ("What is the density of iron?", "no emission cue (glow, emissivity, radiated power)"),
    ("Wien peak wavelength at 1300 K", "no emission cue (glow, emissivity, radiated power)"),
])
def test_stands_aside(stage, question, why):
    assert stage.read(question) == (None, None, why)


class Recorder:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, name, args):
        self.calls.append((name, args))
        return self.replies[name]


def test_power_is_a_chain(stage):
    call = Recorder({"emissivity": {"value": 0.15, "units": "dimensionless", "source": "Hagen-Rubens", "notes": "total"},
                     "blackbody_total_power": {"value": 43000.0, "units": "W", "source": "Stefan-Boltzmann", "formula": "P = eps sigma A T^4"}})
    out = stage.answer("How much heat does iron radiate at 1500 K?", call=call)
    assert call.calls == [("emissivity", {"material": "iron", "temperature_k": 1500.0}),
                          ("blackbody_total_power", {"temperature_k": 1500.0, "area_m2": 1.0, "emissivity": 0.15})]
    assert out["value"] == 43000.0 and out["units"] == "W"
    assert "per square metre" in out["notes"] and "Hagen-Rubens" in out["source"]
    assert [s["tool"] for s in out["inputs"]["chain"]] == ["emissivity", "blackbody_total_power"]
    assert out["interpreter"] == "emission rule"


def test_power_with_an_area(stage):
    call = Recorder({"emissivity": {"value": 0.9, "source": "table"}, "blackbody_total_power": {"value": 1.0, "units": "W"}})
    stage.answer("What power does a 20 cm² patch of concrete radiate at 400 K?", call=call)
    assert call.calls[1][1]["area_m2"] == pytest.approx(20e-4)


def test_a_tool_with_no_value_stands_aside(stage):
    miss = {}
    call = Recorder({"emissivity": {"value": None, "notes": "no emissivity basis"}})
    assert stage.answer("emissivity of glass", call=call, miss=miss) is None
    assert miss["emission"].startswith("emissivity gave no value")


def test_a_single_call(stage):
    call = Recorder({"thermal_glow": {"value": {"visible": True, "rgb": [1.0, 0.1, 0.0]}, "units": "", "source": "glow"}})
    out = stage.answer("What colour does iron glow at 1300 K?", call=call)
    assert out["interpreted_as"] == "thermal_glow(material='iron', temperature_k=1300.0)"
    assert out["value"]["visible"]
