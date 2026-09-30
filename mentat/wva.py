"""Weapon vs. Armor over HTTP: sigma-ground's own game solver, served by Mentat.

The game is sigma-ground's `games/weapon_vs_armor` (a pygame desktop game). Its
physics, `physics/solver.py: solve(weapon, armor_layers, environment, distance_m)`,
is pure Python on top of sigma_ground.field.interface; the pygame parts (render/,
ui/) are never imported here. No physics lives in this file. It only:

  • imports the game from the pinned sigma-ground (`load`, with one adapter, below),
  • builds the catalog a browser needs to draw and edit a fight (`Game.catalog`),
  • validates and clamps every input before the game's solve() sees it (`clean`),
  • turns the game's DamageResult into small JSON (`Game.solve`).

`clean` and the limits don't need sigma-ground, so they're tested in CI; the rest
is tested against a real sigma-ground checkout when one is on the path.

The adapter: sigma-ground moved SIGMA_CONV out of sigma_ground.field.constants
(to the separate ssbm-theory project), but the game still imports it, so the game
can't even be imported as-is. While the game imports, and only then, the name is
bound to a placeholder that refuses every use; it is removed from sigma-ground's
constants module again straight after, so Mentat's constant tools never see it.
Modules that import the name without using it (plasma) work; weapons whose physics
does use it (gravitational) fail on first use and are reported as unavailable,
with the reason. No value is ever made up for it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

C_LIGHT = 299_792_458.0

# Names the game imports from sigma_ground.field.constants that sigma-ground no longer defines.
RELOCATED = {
    "SIGMA_CONV": ("sigma-ground moved SIGMA_CONV, the sigma-field constant this weapon's physics needs, "
                   "out to the separate ssbm-theory project, so the game can't compute it at this commit"),
}

# Input limits. They mirror the game's own custom-weapon editor sliders (ui/weapon_panel.py),
# armor builder (ui/armor_panel.py: 10 layers, 1 mm to 2 m) and range slider (ui/env_panel.py:
# 1 m to 10 km), widened only where a preset goes past a slider (the Thermonuclear and
# Photon Torpedo presets' yields; the neutron pulse's 5 cm beam). Every served preset must sit
# inside them unchanged (tests check that).
MAX_LAYERS = 10
THICKNESS_M = (0.001, 2.0)
DISTANCE_M = (1.0, 10_000.0)
MAX_BODY_BYTES = 8_000
RAMP_SAMPLES = 16          # temperature_color() samples per material, 300 K up to its melting point


class Invalid(ValueError):
    """The request isn't a fight the game can run; the message says why, in plain words."""


class NotInSigmaGround(RuntimeError):
    """A constant the game uses that its sigma-ground no longer defines."""


class _Relocated:
    """Stands in for a constant sigma-ground removed. Any arithmetic, comparison or formatting
    raises NotInSigmaGround, so code that needs the value fails instead of getting a made-up one."""

    def __init__(self, name: str, why: str):
        self.name, self.why = name, why

    def __repr__(self):
        return f"<{self.name}: not in this sigma-ground>"

    def _refuse(self, *_args, **_kwargs):
        raise NotInSigmaGround(self.why)

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _refuse
    __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = __mod__ = __pow__ = __rpow__ = _refuse
    __neg__ = __pos__ = __abs__ = __float__ = __int__ = __index__ = __round__ = __bool__ = _refuse
    __lt__ = __le__ = __gt__ = __ge__ = __format__ = _refuse


# ── the weapon parameters the browser may set ───────────────────────────────────

@dataclass(frozen=True)
class Num:
    key: str
    label: str
    unit: str
    lo: float
    hi: float
    log: bool = True
    show: tuple[str, float] | None = None     # how the browser shows it: (unit, factor), e.g. ("mm", 1000)

    def spec(self) -> dict:
        out = {"key": self.key, "kind": "number", "label": self.label, "unit": self.unit,
               "min": self.lo, "max": self.hi, "log": self.log}
        if self.show:
            out["show"] = {"unit": self.show[0], "factor": self.show[1]}
        return out


@dataclass(frozen=True)
class Choice:
    key: str
    label: str
    choices: tuple[str, ...] | None = None    # None = any served material

    def spec(self, materials) -> dict:
        return {"key": self.key, "kind": "choice", "label": self.label,
                "choices": list(self.choices if self.choices is not None else materials)}


@dataclass(frozen=True)
class Flag:
    key: str
    label: str

    def spec(self) -> dict:
        return {"key": self.key, "kind": "bool", "label": self.label}


MM = ("mm", 1000.0)
TYPES: dict[str, dict] = {
    "kinetic": {"label": "Kinetic", "about": "A mass at speed: Poncelet penetration, Birkhoff above the material's sound speed.",
                "params": (Num("mass_kg", "Mass", "kg", 0.001, 5000),
                           Num("velocity_ms", "Velocity", "m/s", 1, 0.95 * C_LIGHT),
                           Num("radius_m", "Radius", "m", 0.001, 0.5, show=MM),
                           Choice("material", "Penetrator material"))},
    "laser": {"label": "Laser", "about": "A beam: Beer-Lambert absorption heats each layer until it melts or ablates.",
              "params": (Num("power_w", "Power", "W", 0.001, 1e12),
                         Num("wavelength_nm", "Wavelength", "nm", 0.001, 2000),
                         Num("pulse_duration_s", "Pulse duration", "s", 1e-12, 10),
                         Num("spot_radius_m", "Spot radius", "m", 0.0001, 0.5, show=MM))},
    "plasma": {"label": "Plasma", "about": "Superheated ionised gas: thermal flux into the surface plus a pressure wave.",
               "params": (Num("temperature_ev", "Temperature", "eV", 0.03, 10_000),
                          Num("pressure_pa", "Pressure", "Pa", 100, 1e9),
                          Num("duration_s", "Duration", "s", 0.001, 10, log=False),
                          Num("contact_radius_m", "Contact radius", "m", 0.005, 1.0, log=False, show=MM))},
    "explosive": {"label": "Explosive", "about": "Blast overpressure (Hopkinson-Cranz), or a shaped-charge jet that erodes layer by layer.",
                  "params": (Num("tnt_kg", "Yield (TNT)", "kg", 0.0001, 1e10),
                             Num("standoff_m", "Standoff", "m", 0, 500, log=False),
                             Flag("shaped_charge", "Shaped charge (HEAT jet)"))},
    "particle": {"label": "Particle beam", "about": "Relativistic particles: Bethe-style stopping power and a Bragg peak.",
                 "params": (Num("energy_mev", "Beam energy", "MeV", 0.1, 1e9),
                            Num("current_a", "Beam current", "A", 1e-12, 0.01),
                            Num("duration_s", "Duration", "s", 0.001, 10, log=False),
                            Num("beam_radius_m", "Beam radius", "m", 0.001, 0.05, show=MM),
                            Choice("particle", "Particle", ("proton", "neutron", "electron")))},
    "gravitational": {"label": "Exotic", "about": "Tidal stress and the sigma-field disruptor.",
                      "params": (Num("tidal_gradient_ms2", "Tidal gradient", "m/s²/m", 0, 1e13, log=False),)},
}


def _number(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid(f"{where} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise Invalid(f"{where} must be a finite number")
    return value


def _clamp(value, lo, hi, where: str, clamped: list[str]) -> float:
    value = _number(value, where)
    out = min(max(value, lo), hi)
    if out != value:
        clamped.append(where)
    return out


def clean(body, *, types, materials, environments) -> tuple[dict, list[str]]:
    """Turn an untrusted request into exactly the arguments solve() takes.

    `types` maps each weapon type offered to its params (TYPES entries, possibly narrowed);
    `materials` and `environments` are the allow-lists. Wrong types, unknown names, too many
    layers or a stack with no material are refused (Invalid); numbers out of range are clamped
    to the game's limits and named in the returned list, so the caller can say so. Anything
    not asked for (e.g. the game's internal `_jet_remaining_m`) is dropped, never passed on.
    """
    if not isinstance(body, dict):
        raise Invalid("send a JSON object: {weapon, armor_layers, environment, distance_m}")
    clamped: list[str] = []

    weapon = body.get("weapon")
    if not isinstance(weapon, dict):
        raise Invalid("weapon must be an object with a type and its numbers")
    wtype = weapon.get("type")
    if not isinstance(wtype, str) or wtype not in types:
        raise Invalid(f"weapon.type must be one of: {', '.join(types)}")
    w: dict = {"type": wtype}
    for p in types[wtype]:
        where, value = f"weapon.{p.key}", weapon.get(p.key)
        if isinstance(p, Num):
            if value is None:
                raise Invalid(f"{where} is missing")
            w[p.key] = _clamp(value, p.lo, p.hi, where, clamped)
        elif isinstance(p, Choice):
            allowed = p.choices if p.choices is not None else materials
            if not isinstance(value, str) or value not in allowed:
                raise Invalid(f"{where} must be one of: {', '.join(allowed)}")
            w[p.key] = value
        else:                                                   # Flag
            if value is None:
                value = False
            if not isinstance(value, bool):
                raise Invalid(f"{where} must be true or false")
            w[p.key] = value

    layers_in = body.get("armor_layers")
    if not isinstance(layers_in, list) or not layers_in:
        raise Invalid("armor_layers must be a list of layers")
    if len(layers_in) > MAX_LAYERS:
        raise Invalid(f"at most {MAX_LAYERS} layers (gaps count), like the game")
    layers = []
    for i, layer in enumerate(layers_in, 1):
        where = f"armor_layers[{i}]"
        if not isinstance(layer, dict):
            raise Invalid(f"{where} must be an object")
        gap = layer.get("gap", False)
        if not isinstance(gap, bool):
            raise Invalid(f"{where}.gap must be true or false")
        thick = layer.get("thickness_m")
        if thick is None:
            raise Invalid(f"{where}.thickness_m is missing")
        thick = _clamp(thick, *THICKNESS_M, f"{where}.thickness_m", clamped)
        if gap:
            layers.append({"gap": True, "thickness_m": thick})
            continue
        mat = layer.get("material")
        if not isinstance(mat, str) or mat not in materials:
            raise Invalid(f"{where}.material must be one of the game's materials")
        layers.append({"material": mat, "thickness_m": thick})
    if all(layer.get("gap") for layer in layers):
        raise Invalid("the armor needs at least one layer of material, not only gaps")

    env = body.get("environment")
    if not isinstance(env, str) or env not in environments:
        raise Invalid(f"environment must be one of: {', '.join(environments)}")
    distance = _clamp(body.get("distance_m", 100.0), *DISTANCE_M, "distance_m", clamped)
    return {"weapon": w, "armor_layers": layers, "environment": env, "distance_m": distance}, clamped


# ── the game itself ────────────────────────────────────────────────────────────

def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(c))) for c in rgb))


def _finite(x):
    """JSON has no inf/NaN; a non-finite number from the model is reported as null, never replaced."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _import_game():
    import sigma_ground.field.constants as sgc
    added = [name for name in RELOCATED if not hasattr(sgc, name)]
    for name in added:
        setattr(sgc, name, _Relocated(name, RELOCATED[name]))
    try:
        from games.weapon_vs_armor import materials, presets
        from games.weapon_vs_armor.physics import environment, gravitational, solver
    finally:
        for name in added:
            delattr(sgc, name)
    return solver, presets, materials, environment, gravitational


class Game:
    """The game, loaded once per process. `catalog` is static; `solve` runs one fight."""

    def __init__(self, ref: str = ""):
        self.ref = ref
        solver, presets, materials, environment, gravitational = _import_game()
        self._solve, self._materials = solver.solve, materials

        # Materials: every one the game can actually use (sigma-ground's MATERIALS table has
        # some the game has no melting point or colour for, e.g. zinc).
        served, self.missing_materials = [], {}
        for name in materials.all_materials():
            try:
                materials.get_material(name)
                materials.temperature_color(name, 300.0)
                served.append(name)
            except Exception as exc:  # noqa: BLE001 — a gap in the game's tables: leave it out, say which
                self.missing_materials[name] = f"the game has no {exc} data for it"
        self.materials = tuple(served)
        self.environments = tuple(environment.ENVIRONMENTS)

        # The exotic weapon's sigma spike, bounded by the game's own constant when it has one.
        sigma = getattr(gravitational, "SIGMA_CONV", None)
        self.types = {t: tuple(spec["params"]) for t, spec in TYPES.items()}
        if isinstance(sigma, (int, float)):
            self.types["gravitational"] += (Num("sigma_spike", "Sigma-field spike", "", 0, 0.99 * sigma, log=False),)

        # Probe every preset once through the real solver: a weapon type whose physics can't
        # run at this sigma-ground (a relocated constant) is unavailable, with the reason.
        probe = [{"material": "steel_mild", "thickness_m": 0.01}]
        self.unavailable: dict[str, str] = {}
        self.presets = []
        for key, preset in presets.WEAPON_PRESETS.items():
            weapon = presets.get_weapon(key)
            wtype = weapon.get("type")
            entry = {"key": key, "name": preset["name"], "type": wtype}
            try:
                if wtype not in self.types:
                    raise Invalid(f"the browser version doesn't know the {wtype!r} weapon type yet")
                self._solve(weapon, probe, "vacuum", 1.0)
                fight, clamped = clean({"weapon": weapon, "armor_layers": probe, "environment": "vacuum"},
                                       types=self.types, materials=self.materials, environments=self.environments)
                lost = [k for k in weapon if k not in fight["weapon"] or fight["weapon"][k] != weapon[k]]
                if lost or clamped:
                    raise Invalid(f"its {', '.join(lost or clamped)} can't be sent within the browser version's limits")
                entry["weapon"] = fight["weapon"]
            except NotInSigmaGround as exc:
                self.unavailable[wtype] = str(exc)
                entry["why"] = str(exc)
            except Exception as exc:  # noqa: BLE001 — one broken preset shouldn't take the game down
                entry["why"] = str(exc)[:300]
            self.presets.append(entry)
        self.offered = {t: p for t, p in self.types.items() if t not in self.unavailable}

        self.armors = [{"key": k, "name": a["name"], "layers": presets.get_armor(k)}
                       for k, a in presets.ARMOR_PRESETS.items()
                       if all(layer.get("gap") or layer.get("material") in self.materials for layer in a["layers"])]
        self.catalog = self._catalog(materials, environment)

    # ── what the browser needs ────────────────────────────────────────────────

    def _ramp(self, name: str) -> dict:
        """The game's own temperature_color(), sampled: heating from 300 K up to the melting point
        (the browser interpolates between samples), then its molten and vaporised colours."""
        m = self._materials
        melt = float(m.MELTING_POINTS_K[name])
        # (water ice melts at 273 K in the game, so it is already "molten" at 300 K: one sample)
        temps = sorted({round(300.0 + max(0.0, melt - 300.0) * i / RAMP_SAMPLES) for i in range(RAMP_SAMPLES)})
        return {"heat": [[t, _hex(m.temperature_color(name, t))] for t in temps],
                "molten_k": melt, "molten": _hex(m.temperature_color(name, melt)),
                "vapor_k": 1.5 * melt, "vapor": _hex(m.temperature_color(name, 1.5 * melt))}

    def _catalog(self, materials, environment) -> dict:
        group_of = {mat: g for g, mats in materials.MATERIAL_GROUPS.items() for mat in mats}
        mats = []
        for name in self.materials:
            props = materials.get_material(name)
            label, _, about = props["description"].partition(" — ")
            mats.append({"key": name, "label": label.strip() or name, "about": about.strip(),
                         "group": group_of.get(name, "Other"),
                         "melting_point_k": props["melting_point_k"], "density_kg_m3": props["density_kg_m3"],
                         "color": _hex(props["color"]), **self._ramp(name)})
        types = {}
        for t, spec in TYPES.items():
            entry = {"label": spec["label"], "about": spec["about"], "available": t in self.offered}
            if t in self.offered:
                entry["params"] = [p.spec(self.materials) if isinstance(p, Choice) else p.spec()
                                   for p in self.types[t]]
            else:
                entry["why"] = self.unavailable.get(t, "not available")
            types[t] = entry
        return {
            "sigma_ground_ref": self.ref,
            "types": types,
            "weapons": self.presets,
            "armors": self.armors,
            "materials": mats,
            "missing_materials": self.missing_materials,
            "environments": [{"key": k, "label": e["label"], "about": e["description"]}
                             for k, e in environment.ENVIRONMENTS.items()],
            "limits": {"layers_max": MAX_LAYERS, "thickness_m": list(THICKNESS_M), "distance_m": list(DISTANCE_M)},
            "defaults": {"weapon": "default", "armor": "default", "environment": "atmosphere_earth",
                         "distance_m": 100.0},
        }

    # ── one fight ─────────────────────────────────────────────────────────────

    def clean(self, body) -> tuple[dict, list[str]]:
        wtype = body.get("weapon", {}).get("type") if isinstance(body, dict) and isinstance(body.get("weapon"), dict) else None
        if isinstance(wtype, str) and wtype in self.unavailable:
            raise Invalid(f"{TYPES[wtype]['label']} weapons can't run here: {self.unavailable[wtype]}.")
        return clean(body, types=self.offered, materials=self.materials, environments=self.environments)

    def solve(self, fight: dict, clamped: list[str] | None = None) -> dict:
        """Run the game's solve() on a cleaned fight; its DamageResult, as JSON."""
        res = self._solve(fight["weapon"], fight["armor_layers"], fight["environment"], fight["distance_m"])
        layers = []
        for st in res.layers:
            gap = st.material == "gap"
            layers.append({
                "material": st.material, "gap": gap, "thickness_m": _finite(st.thickness_m),
                "penetrated": bool(st.penetrated), "damage_depth_m": _finite(st.damage_depth_m),
                "damage_fraction": _finite(st.damage_fraction), "energy_deposited_j": _finite(st.energy_deposited_j),
                "temperature_k": _finite(st.temperature_k), "notes": str(st.notes)[:400],
                "color": None if gap or _finite(st.temperature_k) is None
                else _hex(self._materials.temperature_color(st.material, st.temperature_k)),
            })
        return {
            "verdict": res.verdict, "breached": bool(res.breached),
            "layers_penetrated": res.layers_penetrated, "total_layers": res.total_layers,
            "weapon_type": res.weapon_type, "environment": res.environment,
            "summary_lines": [str(s)[:400] for s in res.summary_lines],
            "layers": layers, "inputs": fight, "clamped": list(clamped or []),
            "sigma_ground_ref": self.ref,
        }
