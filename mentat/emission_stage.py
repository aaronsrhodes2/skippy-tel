"""A named material's own thermal emission: a fixed rule ahead of the cascade and the router in /ask, like the fold rule (fold_stage.py).

sigma-ground 4209915 added `emissivity` (total at a temperature: Hagen-Rubens for a metal, measured for a non-metal; spectral at a wavelength from
measured n+ik) and `thermal_glow` (emissivity -> Planck -> the colour and brightness of a hot block). The trained router (mentat-router-v31)
predates both: it answered "what colour does iron glow at 1300 K" with Wien's peak wavelength and "how well does tungsten radiate heat" with a
blackbody's power, dropping the material (emissivity 1, silently). Visual correctness in SkippyCraft needs these answered, so this rule reads
the question itself:

  - a glow cue (glows, red-hot, incandescent, what colour ... hot) and a temperature  -> thermal_glow
  - an emissivity cue (emissivity, emittance, how well ... radiate, a good radiator)   -> emissivity (300 K when no temperature is given;
                                                                                         spectral when a wavelength is)
  - a radiated-power cue (how much power/heat ... radiate) and a temperature          -> the chain emissivity > blackbody_total_power,
                                                                                         per square metre unless an area is given

It answers only for exactly ONE material it knows and stands aside (None, its reason in `miss["emission"]`) for comparisons, two materials, a
missing temperature, or a tool that has no value (a material sigma-ground has no emissivity for): the router and the cascade carry on.
"""
from __future__ import annotations

import re

# the names the emissivity tool resolves (sigma-ground field/interface/resolve.py aliases + the metal, n+k and measured-emissivity tables)
MATERIALS = ("aluminium oxide", "aluminum oxide", "al2o3", "alumina", "aluminium", "aluminum", "copper", "gold", "iron", "lead", "nickel",
             "platinum", "silver", "mild steel", "carbon steel", "steel", "titanium", "tungsten", "zinc", "depleted uranium", "uranium",
             "mercury", "silicon", "carbon fibre", "carbon fiber", "concrete", "glass", "granite", "abs plastic", "abs", "plastic", "rubber",
             "water", "ice", "oak", "wood")
# two names for one material ("alumina (aluminium oxide ceramic)") count once
SAME = {"aluminium oxide": "alumina", "aluminum oxide": "alumina", "al2o3": "alumina", "aluminium": "aluminum", "carbon fibre": "carbon fiber",
        "mild steel": "steel", "carbon steel": "steel", "abs plastic": "abs", "oak": "wood", "depleted uranium": "uranium"}
_MATERIAL = re.compile(r"\b(" + "|".join(re.escape(m) for m in sorted(MATERIALS, key=len, reverse=True)) + r")\b", re.I)

_GLOW = re.compile(r"\bglow\w*|\b(red|white|yellow|orange)[- ]hot\b|\bincandescen\w*|\bwhat colou?r\b.*\b(hot|heated)\b"
                   r"|\b(hot|heated)\b.*\bwhat colou?r\b", re.I)
_EMISSIVITY = re.compile(r"\bemissivit\w*|\bemittance\b|\bhow (well|good|efficiently)\b.*\bradiat\w*|\b(good|bad|poor) radiator\b", re.I)
_POWER = re.compile(r"\b(how much|what) (power|heat|energy)\b.*\b(radiat\w*|emit\w*|give\w* off)|\bradiat\w* (power|heat)\b"
                    r"|\bradiat\w*\b.*\bwatts?\b|\bpower\b.*\bradiat\w*", re.I)
_NOT_THIS = re.compile(r"\bcompared?\b|\bversus\b|\bvs\.?\b|\bratio\b|\bthan\b", re.I)

_NUM = r"(-?\d[\d,]*(?:\.\d+)?)"
_TEMP = re.compile(_NUM + r"\s*(°\s*[cf]|deg(?:rees?)?\s*[cf]\b|degrees? (?:celsius|fahrenheit)|celsius|fahrenheit|kelvin|[kcf]\b)", re.I)
_WAVE = re.compile(_NUM + r"\s*(nm|nanomet(?:er|re)s?|µm|um|microns?|micromet(?:er|re)s?)\b", re.I)
_AREA = re.compile(_NUM + r"\s*(m2|m²|square met(?:er|re)s?|cm2|cm²|square centimet(?:er|re)s?)", re.I)


def _temperature_k(question: str) -> float | None:
    m = _TEMP.search(question)
    if not m:
        return None
    v, unit = float(m.group(1).replace(",", "")), m.group(2).lower().replace(" ", "")
    if unit.startswith("k"):
        return v
    if unit.endswith("f") or "fahrenheit" in unit:
        return (v - 32.0) * 5.0 / 9.0 + 273.15
    return v + 273.15


def read(question: str) -> tuple[str | None, dict | None, str]:
    """(tool or "chain:power", its arguments, "") or (None, None, why not)."""
    if _GLOW.search(question):
        kind = "thermal_glow"
    elif _EMISSIVITY.search(question):
        kind = "emissivity"
    elif _POWER.search(question):
        kind = "chain:power"
    else:
        return None, None, "no emission cue (glow, emissivity, radiated power)"
    if _NOT_THIS.search(question):
        return None, None, "a comparison"
    names = [m.group(1).lower() for m in _MATERIAL.finditer(question)]
    named = list(dict.fromkeys(SAME.get(n, n) for n in names))
    if len(named) != 1:
        return None, None, "no material it knows is named" if not named else f"more than one material named ({', '.join(named)})"
    args: dict = {"material": names[0]}
    T = _temperature_k(question)
    if T is not None:
        args["temperature_k"] = T
    elif kind != "emissivity":
        return None, None, "no temperature given"
    if kind == "emissivity":
        m = _WAVE.search(question)
        if m:
            v = float(m.group(1).replace(",", ""))
            args["wavelength_m"] = v * (1e-9 if m.group(2).lower().startswith("n") else 1e-6)
    if kind == "chain:power":
        m = _AREA.search(question)
        if m:
            v = float(m.group(1).replace(",", ""))
            args["area_m2"] = v * (1e-4 if m.group(2).lower().startswith(("cm", "square c")) else 1.0)
    return kind, args, ""


def _shape(got: dict, plan: str, chain: list, understood: list) -> dict:
    out = {k: got.get(k) for k in ("value", "units", "source", "formula", "uncertainty", "provenance_tag", "display", "notes")}
    out["error"], out["clarification"], out["assumed"] = None, None, got.get("assumed")
    out["interpreted_as"] = plan
    out["interpreter"] = "emission rule"
    out["inputs"] = {"route": "emission", "chain": chain, "understood": understood}
    return out


def _step(tool: str, args: dict, got: dict) -> dict:
    return {"tool": tool, "args": args, "value": got.get("value"), "units": got.get("units"), "source": got.get("source"),
            "formula": got.get("formula")}


def answer(question: str, *, call, miss: dict | None = None) -> dict | None:
    """The /ask result for a material-emission question, or None (the caller carries on). `call(name, args)` runs a Mentat tool."""
    kind, args, why = read(question)

    def stand_aside(reason: str):
        if miss is not None:
            miss["emission"] = reason
        return None

    if kind is None:
        return stand_aside(why)
    try:
        if kind != "chain:power":
            got = call(kind, args)
            got = got if isinstance(got, dict) else {}
            if got.get("value") is None:
                return stand_aside(f"{kind} gave no value: {str(got.get('notes') or got.get('error'))[:200]}")
            plan = f"{kind}(" + ", ".join(f"{k}={v!r}" for k, v in args.items()) + ")"
            return _shape(got, plan, [_step(kind, args, got)], [{"tool": kind, "params": args}])
        e_args = {"material": args["material"], "temperature_k": args["temperature_k"]}
        e = call("emissivity", e_args)
        e = e if isinstance(e, dict) else {}
        if e.get("value") is None:
            return stand_aside(f"emissivity gave no value: {str(e.get('notes') or e.get('error'))[:200]}")
        p_args = {"temperature_k": args["temperature_k"], "area_m2": args.get("area_m2", 1.0), "emissivity": e["value"]}
        p = call("blackbody_total_power", p_args)
        p = p if isinstance(p, dict) else {}
        if p.get("value") is None:
            return stand_aside(f"blackbody_total_power gave no value: {str(p.get('notes') or p.get('error'))[:200]}")
    except Exception as exc:                                          # noqa: BLE001 — a rule that breaks never stops /ask
        return stand_aside(f"{type(exc).__name__}: {str(exc)[:200]}")
    per = "" if "area_m2" in args else " per square metre of surface"
    got = dict(p)
    got["source"] = f"{p.get('source')}; emissivity {e['value']:.3g} from {e.get('source')}"
    got["notes"] = f"power {args['material']} radiates at {args['temperature_k']:g} K{per} (emissivity {e['value']:.3g}: {e.get('notes')})"
    plan = f"emissivity(material={args['material']!r}, temperature_k={args['temperature_k']!r}) > blackbody_total_power(" + \
        ", ".join(f"{k}={v!r}" for k, v in p_args.items()) + ")"
    return _shape(got, plan, [_step("emissivity", e_args, e), _step("blackbody_total_power", p_args, p)],
                  [{"tool": "emissivity", "params": e_args}, {"tool": "blackbody_total_power", "params": p_args}])
