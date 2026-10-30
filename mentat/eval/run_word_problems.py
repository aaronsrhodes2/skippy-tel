"""Measure mentat/word_stage.py on the held-out set (word_problems.json): the stage alone, a model on an Ollama, evaluate_quantity in process.

    SIGMA_GROUND_DIR=/d/Aaron/development/sigma-ground python -X utf8 mentat/eval/run_word_problems.py \
        --model qwen3:8b --ollama-url http://127.0.0.1:11434 --out <results.json>

A 'compute' item is right when the stage returns a value within 2 percent of the expected one (after converting to the expected unit); a 'decline' item is
right only when the stage returns no value. Reports both counts and every miss with the stage's reason, so a wrong number is told apart from a refusal.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, os.environ.get("SIGMA_GROUND_DIR", "D:/Aaron/development/sigma-ground"))


def same(value, units, expected, expected_units, tol=0.02) -> bool:
    from sigma_ground.mcp.tools.units import _get_registry
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if expected_units and units and units != expected_units:
        try:
            ureg = _get_registry()
            v = float((v * ureg.parse_expression(units)).to(expected_units).magnitude)
        except Exception:                                       # noqa: BLE001
            return False
    return abs(v - expected) <= tol * abs(expected)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap.add_argument("--out", default="")
    ap.add_argument("--set", default="word_problems.json", help="a held-out set in this folder")
    ap.add_argument("--only", default="", help="comma-separated ids")
    args = ap.parse_args()
    os.environ["MENTAT_WORD_MODEL"] = args.model
    import word_stage
    word_stage.MODEL = args.model
    from sigma_ground.mcp.tools.units import evaluate_quantity

    def call(name, a):
        assert name == "evaluate_quantity"
        return evaluate_quantity(**a).to_dict()

    items = json.loads((HERE / args.set).read_text(encoding="utf-8"))["items"]
    if args.only:
        items = [i for i in items if i["id"] in args.only.split(",")]
    rows, right = [], {"compute": 0, "decline": 0}
    total = {"compute": sum(i["kind"] == "compute" for i in items), "decline": sum(i["kind"] == "decline" for i in items)}
    for it in items:
        miss: dict = {}
        t0 = time.time()
        hit = word_stage.answer(it["question"], call=call, ollama_url=args.ollama_url, miss=miss)
        dt = time.time() - t0
        if it["kind"] == "compute":
            ok = hit is not None and same(hit["value"], hit.get("units") or "", it["expected"], it["expected_units"])
        else:
            ok = hit is None
        right[it["kind"]] += ok
        rows.append({"id": it["id"], "kind": it["kind"], "ok": ok, "seconds": round(dt, 1), "value": hit and hit["value"], "units": hit and hit.get("units"),
                     "expression": hit and hit["inputs"]["expression"], "miss": miss or None, "expected": it.get("expected")})
        print(f"{'ok ' if ok else 'BAD'} {it['id']} {dt:5.1f}s  "
              f"{(hit['inputs']['expression'] + ' = ' + str(hit['value']) + ' ' + str(hit.get('units'))) if hit else 'no value: ' + str(miss.get('reason'))[:100]}")
    print(f"\ncompute {right['compute']}/{total['compute']}   decline {right['decline']}/{total['decline']}   model {args.model}")
    if args.out:
        Path(args.out).write_text(json.dumps({"model": args.model, "right": right, "total": total, "rows": rows}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
