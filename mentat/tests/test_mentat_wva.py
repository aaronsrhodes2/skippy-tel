"""Weapon vs. Armor on Mentat (mentat/wva.py + its routes in mentat/app.py).

The validation tests need nothing but this repo (they run in CI). The rest run the real
game from a sigma-ground checkout (SIGMA_GROUND_DIR or PYTHONPATH) and skip without one:
    SIGMA_GROUND_DIR=/path/to/sigma-ground python -m pytest mentat/tests -q
"""
import importlib.util
import math
import re
from pathlib import Path

import pytest

import wva

C = 299_792_458.0
TYPES = {t: s["params"] for t, s in wva.TYPES.items()}
MATERIALS = ("steel_mild", "kevlar", "ceramic_alumina", "tungsten", "lead")
ENVS = ("vacuum", "atmosphere_earth")
AK47 = {"type": "kinetic", "mass_kg": 0.0079, "velocity_ms": 715, "radius_m": 0.0038, "material": "steel_mild"}
PLATE = [{"material": "ceramic_alumina", "thickness_m": 0.015}, {"material": "kevlar", "thickness_m": 0.010}]


def _clean(body):
    return wva.clean(body, types=TYPES, materials=MATERIALS, environments=ENVS)


def _fight(**over):
    return {"weapon": dict(AK47), "armor_layers": [dict(x) for x in PLATE], "environment": "vacuum",
            "distance_m": 100, **over}


# ── validation (no sigma-ground needed) ────────────────────────────────────────

def test_clean_rebuilds_the_fight_from_allow_listed_fields_only():
    body = _fight()
    body["weapon"].update(_jet_remaining_m=99.0, _depth_traversed_m=5.0, __class__="x")   # the game's internal state
    body["armor_layers"][0]["temperature_k"] = 5000
    body["extra"] = "ignored"
    fight, clamped = _clean(body)
    assert fight == {"weapon": {**AK47, "velocity_ms": 715.0}, "armor_layers": PLATE,
                     "environment": "vacuum", "distance_m": 100.0}
    assert clamped == []


def test_clean_clamps_numbers_to_the_games_limits_and_names_them():
    body = _fight(distance_m=-3)
    body["weapon"]["velocity_ms"] = 1e12
    body["armor_layers"].append({"gap": True, "thickness_m": 50})
    fight, clamped = _clean(body)
    assert fight["weapon"]["velocity_ms"] == 0.95 * C and fight["distance_m"] == 1.0
    assert fight["armor_layers"][-1] == {"gap": True, "thickness_m": 2.0}
    assert clamped == ["weapon.velocity_ms", "armor_layers[3].thickness_m", "distance_m"]


@pytest.mark.parametrize("change, why", [
    (lambda b: b.clear(), "weapon must be an object"),
    (lambda b: b.update(weapon="AK-47"), "weapon must be an object"),
    (lambda b: b["weapon"].update(type="orbital_laser"), "weapon.type must be one of"),
    (lambda b: b["weapon"].update(velocity_ms="fast"), "weapon.velocity_ms must be a number"),
    (lambda b: b["weapon"].update(velocity_ms=True), "weapon.velocity_ms must be a number"),
    (lambda b: b["weapon"].update(velocity_ms=float("nan")), "must be a finite number"),
    (lambda b: b["weapon"].update(mass_kg=float("inf")), "must be a finite number"),
    (lambda b: b["weapon"].pop("mass_kg"), "weapon.mass_kg is missing"),
    (lambda b: b["weapon"].update(material="adamantium"), "weapon.material must be one of"),
    (lambda b: b.update(armor_layers=[]), "armor_layers must be a list"),
    (lambda b: b.update(armor_layers="steel"), "armor_layers must be a list"),
    (lambda b: b.update(armor_layers=PLATE * 6), "at most 10 layers"),
    (lambda b: b["armor_layers"].append("steel"), "armor_layers[3] must be an object"),
    (lambda b: b["armor_layers"][0].update(material="unobtainium"), "armor_layers[1].material"),
    (lambda b: b["armor_layers"][0].pop("thickness_m"), "armor_layers[1].thickness_m is missing"),
    (lambda b: b["armor_layers"][0].update(gap="yes"), "gap must be true or false"),
    (lambda b: b.update(armor_layers=[{"gap": True, "thickness_m": 0.1}]), "at least one layer of material"),
    (lambda b: b.update(environment="mars"), "environment must be one of"),
    (lambda b: b.update(distance_m="far"), "distance_m must be a number"),
])
def test_clean_refuses_what_the_game_cannot_run(change, why):
    body = _fight()
    change(body)
    with pytest.raises(wva.Invalid, match=re.escape(why)):
        _clean(body)
    with pytest.raises(wva.Invalid):
        _clean(None)


def test_a_shaped_charge_flag_must_be_a_real_boolean():
    rpg = {"type": "explosive", "tnt_kg": 0.35, "standoff_m": 0}
    fight, _ = _clean(_fight(weapon=dict(rpg)))
    assert fight["weapon"]["shaped_charge"] is False                      # absent = the game's default
    with pytest.raises(wva.Invalid, match="shaped_charge must be true or false"):
        _clean(_fight(weapon={**rpg, "shaped_charge": "yes"}))


def test_the_placeholder_for_a_relocated_constant_refuses_every_use():
    gone = wva._Relocated("SIGMA_CONV", "moved to ssbm-theory")
    for use in (lambda: gone * 0.999, lambda: 1.6 / gone, lambda: min(1.6, gone), lambda: f"{gone:.3f}",
                lambda: float(gone), lambda: gone ** 2, lambda: bool(gone)):
        with pytest.raises(wva.NotInSigmaGround, match="ssbm-theory"):
            use()
    assert "SIGMA_CONV" in repr(gone)                                    # but it can be looked at


# ── the real game (needs a sigma-ground checkout) ──────────────────────────────

@pytest.fixture(scope="module")
def mentat():
    pytest.importorskip("mcp")
    pytest.importorskip("sigma_ground")
    try:
        import games.weapon_vs_armor.presets  # noqa: F401
    except ImportError:
        pytest.skip("no sigma-ground checkout with games/weapon_vs_armor on the path")
    path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("mentat_app", path)      # not `app`: the portal has one too
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.GAME is not None, module.GAME_ERROR
    return module


@pytest.fixture
def client(mentat):
    mentat._hits.clear()
    yield mentat.app.test_client()
    mentat._hits.clear()


@pytest.fixture
def catalog(client):
    return client.get("/wva/catalog").get_json()["result"]


def _preset(catalog, kind, key):
    return next(x for x in catalog[kind] if x["key"] == key)


def test_mentat_still_serves_its_tools_at_this_sigma_ground(client):
    h = client.get("/health")
    body = h.get_json()
    assert h.status_code == 200 and body["ready"] is True and body["accepting"] is True and body["service"] == "mentat"   # ready, not just a 200
    assert body["jobs"]["running"] == 0 and body["tools"] > 100 and body["detail"] == ""
    assert len(client.get("/tools").get_json()) > 100
    r = client.post("/tools/convert_units", json={"value": 1, "from_units": "mile", "to_units": "km"}).get_json()
    assert r["ok"] and abs(r["result"]["value"] - 1.609344) < 1e-9


def test_the_catalog_is_the_games_own_presets_materials_and_environments(catalog):
    from games.weapon_vs_armor import materials, presets
    from games.weapon_vs_armor.physics.environment import ENVIRONMENTS
    assert [w["key"] for w in catalog["weapons"]] == list(presets.WEAPON_PRESETS)
    assert [a["key"] for a in catalog["armors"]] == list(presets.ARMOR_PRESETS)
    assert [e["key"] for e in catalog["environments"]] == list(ENVIRONMENTS)
    served = [m["key"] for m in catalog["materials"]]
    assert set(served) | set(catalog["missing_materials"]) == set(materials.all_materials())
    for m in catalog["materials"]:                                       # the game's own colours, sampled
        assert m["heat"][0][0] == 300 and m["molten_k"] == materials.MELTING_POINTS_K[m["key"]]
        for t, colour in m["heat"] + [[m["molten_k"], m["molten"]], [m["vapor_k"], m["vapor"]]]:
            assert colour == wva._hex(materials.temperature_color(m["key"], t)), (m["key"], t)
        assert m["color"] == wva._hex(materials.MATERIAL_COLORS[m["key"]])
    for w in catalog["weapons"]:                                         # every served preset, sent back unchanged
        if "weapon" in w:
            assert all(w["weapon"][k] == v for k, v in presets.get_weapon(w["key"]).items()), w["key"]
    served_weapons = [w for w in catalog["weapons"] if "weapon" in w]
    assert len(served_weapons) >= 24 and {"ak47", "hel_100kw", "rpg7", "plasma_cannon", "proton_cannon"} <= \
        {w["key"] for w in served_weapons}


def test_every_served_preset_fits_the_limits_without_clamping(mentat, catalog):
    for w in catalog["weapons"]:
        if "weapon" in w:
            fight, clamped = mentat.GAME.clean({"weapon": w["weapon"], "armor_layers": PLATE, "environment": "vacuum"})
            assert clamped == [] and fight["weapon"] == w["weapon"], w["key"]
    for a in catalog["armors"]:
        _, clamped = mentat.GAME.clean({"weapon": AK47, "armor_layers": a["layers"], "environment": "vacuum"})
        assert clamped == [], a["key"]


def test_weapons_whose_physics_needs_a_relocated_constant_are_unavailable_not_faked(mentat, client, catalog):
    import sigma_ground.field.constants as sgc
    grav = catalog["types"]["gravitational"]
    if hasattr(sgc, "SIGMA_CONV"):                                       # a sigma-ground that has it again: served
        assert grav["available"]
        return
    assert not grav["available"] and "SIGMA_CONV" in grav["why"] and "ssbm-theory" in grav["why"]
    assert "why" in _preset(catalog, "weapons", "sigma_spike_weapon")
    r = client.post("/wva/solve", json={"weapon": {"type": "gravitational", "tidal_gradient_ms2": 1e7},
                                        "armor_layers": PLATE, "environment": "vacuum"})
    assert r.status_code == 400 and "SIGMA_CONV" in r.get_json()["error"]
    # the adapter left nothing behind: Mentat's constant tools still don't know SIGMA_CONV
    looked_up = client.post("/tools/lookup_constant", json={"name": "SIGMA_CONV"}).get_json()["result"]
    assert looked_up["value"] is None


def test_a_fight_is_the_games_own_solve_with_nothing_in_between(client, catalog):
    from games.weapon_vs_armor.physics.solver import solve
    weapon = _preset(catalog, "weapons", "ak47")["weapon"]
    armor = _preset(catalog, "armors", "nij_iv")["layers"]
    r = client.post("/wva/solve", json={"weapon": weapon, "armor_layers": armor,
                                        "environment": "atmosphere_earth", "distance_m": 100})
    assert r.status_code == 200
    got = r.get_json()["result"]
    want = solve(weapon, armor, "atmosphere_earth", 100.0)
    assert got["verdict"] == want.verdict and got["layers_penetrated"] == want.layers_penetrated
    assert got["summary_lines"] == want.summary_lines and got["breached"] == want.breached
    for g, s in zip(got["layers"], want.layers, strict=True):
        assert (g["material"], g["penetrated"], g["notes"]) == (s.material, s.penetrated, s.notes)
        for k in ("temperature_k", "damage_depth_m", "energy_deposited_j", "thickness_m"):
            assert math.isclose(g[k], getattr(s, k), rel_tol=1e-12, abs_tol=0), k
        assert g["color"].startswith("#") and len(g["color"]) == 7
    assert got["inputs"]["armor_layers"] == armor and got["clamped"] == []


def test_gaps_come_back_as_gaps_and_a_breach_says_so(client, catalog):
    from games.weapon_vs_armor.physics.solver import solve
    weapon = _preset(catalog, "weapons", "railgun_relativistic")["weapon"]
    armor = _preset(catalog, "armors", "spacecraft_hull")["layers"]
    got = client.post("/wva/solve", json={"weapon": weapon, "armor_layers": armor, "environment": "vacuum",
                                          "distance_m": 1000}).get_json()["result"]
    want = solve(weapon, armor, "vacuum", 1000.0)
    assert got["breached"] == want.breached and got["verdict"] == want.verdict
    gaps = [x for x in got["layers"] if x["gap"]]
    assert all(x["material"] == "gap" and x["color"] is None for x in gaps)
    assert len(got["layers"]) == len(want.layers)


def test_injected_game_state_never_reaches_the_solver(client, catalog):
    rpg = _preset(catalog, "weapons", "rpg7")["weapon"]
    tank = _preset(catalog, "armors", "tank_composite")["layers"]
    fair = client.post("/wva/solve", json={"weapon": rpg, "armor_layers": tank, "environment": "vacuum"}).get_json()
    cheat = client.post("/wva/solve", json={"weapon": {**rpg, "_jet_remaining_m": 1000.0}, "armor_layers": tank,
                                            "environment": "vacuum"}).get_json()
    assert cheat["result"]["layers"] == fair["result"]["layers"]
    assert "_jet_remaining_m" not in cheat["result"]["inputs"]["weapon"]


def test_bad_requests_are_refused_and_out_of_range_numbers_clamped_and_said(client):
    assert client.post("/wva/solve", json={"weapon": AK47, "armor_layers": PLATE, "environment": "mars"}).status_code == 400
    assert client.post("/wva/solve", data="not json", content_type="application/json").status_code == 400
    big = {"weapon": AK47, "armor_layers": PLATE, "environment": "vacuum", "pad": "x" * 9000}
    assert client.post("/wva/solve", json=big).status_code == 413
    r = client.post("/wva/solve", json={"weapon": {**AK47, "velocity_ms": 1e12}, "armor_layers": PLATE,
                                        "environment": "vacuum"}).get_json()["result"]
    assert r["clamped"] == ["weapon.velocity_ms"] and r["inputs"]["weapon"]["velocity_ms"] == 0.95 * C


def test_mentats_rate_limit_and_timeout_cover_the_game(mentat, client, monkeypatch):
    fight = {"weapon": AK47, "armor_layers": PLATE, "environment": "vacuum"}
    monkeypatch.setattr(mentat, "RATE_PER_MIN", 2)
    assert [client.post("/wva/solve", json=fight).status_code for _ in range(3)] == [200, 200, 429]
    mentat._hits.clear()
    import time
    real = mentat.GAME._solve
    monkeypatch.setattr(mentat.GAME, "_solve", lambda *a: time.sleep(0.5) or real(*a))
    monkeypatch.setattr(mentat, "CALL_TIMEOUT_S", 0.05)
    assert client.post("/wva/solve", json=fight).status_code == 504


def test_health_is_not_ready_when_the_physics_tools_answer_wrongly_or_a_dependency_is_missing(client, mentat, monkeypatch):
    mentat_app = mentat
    mentat_app._checks.clear()
    monkeypatch.setattr(mentat_app, "_call", lambda name, args: {"value": 1.0})              # a known question answered wrongly
    bad = client.get("/health")
    assert bad.status_code == 503 and bad.get_json()["ready"] is False and "wrongly" in bad.get_json()["detail"]
    mentat_app._checks.clear()
    monkeypatch.undo()
    monkeypatch.setattr(mentat_app, "USE_LLM", True)
    monkeypatch.setattr(mentat_app, "_qwen_ready", lambda: False)                           # skippy-core's chat model is not offered
    no_llm = client.get("/health")
    assert no_llm.status_code == 503 and no_llm.get_json()["deps"]["llm"]["ready"] is False and "waiting on llm" in no_llm.get_json()["detail"]
    mentat_app._checks.clear()


def test_a_question_in_flight_shows_as_a_running_job(client, mentat, monkeypatch):
    mentat_app = mentat
    seen = {}
    real = mentat_app._ask

    def slow(question, given):
        seen["report"] = mentat_app.READY.report()
        return real(question, given)
    monkeypatch.setattr(mentat_app, "_ask", slow)
    client.post("/ask", json={"question": "Schwarzschild radius of a 1 kg mass?"})
    assert seen["report"]["jobs"]["running"] == 1 and seen["report"]["jobs"]["oldest_s"] is not None
    assert mentat_app.READY.report()["jobs"]["running"] == 0                                # and gone when it finished
