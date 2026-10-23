"""Set up the house: find the lights and switches on the network, pair them with their setup codes, give each a name and a room.

    ./.venv/Scripts/python homectl.py scan                                  every HomeKit accessory on the network (paired or not)
    ./.venv/Scripts/python homectl.py pair <id> <setup-code> "Living room dimmer" [--area "Living room"]
    ./.venv/Scripts/python homectl.py list                                  what Skippy can control, and its state
    ./.venv/Scripts/python homectl.py label <id> --name "..." --area "..."
    ./.venv/Scripts/python homectl.py unpair <id>
    ./.venv/Scripts/python homectl.py say "kitchen light" on                try a control (the same code the voice uses)
    ./.venv/Scripts/python homectl.py prove "theater fan" --rounds 5        switch it on and off, timing each and counting second tries

The setup code is the 8 digits on the device (a Leviton dimmer: the label on its side and in the Leviton app). It is used once, never stored; what is
stored is the pairing key, in state/home/home.json (git-ignored). Set SKIPPY_HOME_STATE to keep it elsewhere.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from homekit import HomeKit, Store
from house import HomeError

ROOT = Path(__file__).resolve().parents[2]


def store_path(env=os.environ) -> Path:
    return Path(env.get("SKIPPY_HOME_STATE") or ROOT / "state" / "home") / "home.json"


def prove(hk: HomeKit, name: str, rounds: int, gap: float, out=print, sleep=time.sleep) -> int:
    """The proof that it beats Alexa: on, off, `rounds` times, each read back from the device itself. Leaves it as it found it."""
    import house                                                    # noqa: PLC0415
    one, close = hk.find(name)
    if one is None:
        out(f"I cannot find {name!r}." + (" Did you mean: " + ", ".join(hk._label(s) for s in close) + "?" if close else " Is it paired? (homectl.py list)"))
        return 1
    eid, label = one["entity_id"], hk._label(one)
    was = one["state"]
    times, seconds, failures = [], 0, 0
    for i in range(rounds):
        for action in ("on", "off"):
            try:
                hk.last = {}
                said = hk.control(eid, action)
                if not hk.last:
                    raise house.HomeError(said)
                times.append(hk.last["ms"])
                seconds += hk.last["attempts"] == 2
                out(f"{i + 1}  {action:<3}  {hk.last['ms']:>5} ms" + ("  (needed a second try)" if hk.last["attempts"] == 2 else ""))
            except house.HomeError as e:
                failures += 1
                out(f"{i + 1}  {action:<3}  FAILED: {e}")
            sleep(gap)
    if was in ("on", "off"):
        try:
            hk.control(eid, was)
        except house.HomeError:
            pass
    if times:
        times.sort()
        out(f"{label}: {len(times)}/{len(times) + failures} worked, median {times[len(times) // 2]} ms, slowest {times[-1]} ms, "
            f"{seconds} needed a second try (done by Skippy, not you).")
    return 0 if not failures else 1


def main(argv=None, hk: HomeKit | None = None, out=print, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("list")
    p = sub.add_parser("pair"); p.add_argument("id"); p.add_argument("code"); p.add_argument("name"); p.add_argument("--area", default="")
    p = sub.add_parser("label"); p.add_argument("id"); p.add_argument("--name", default=""); p.add_argument("--area", default="")
    p = sub.add_parser("unpair"); p.add_argument("id")
    p = sub.add_parser("say"); p.add_argument("name"); p.add_argument("action"); p.add_argument("value", nargs="?")
    p = sub.add_parser("prove"); p.add_argument("name"); p.add_argument("--rounds", type=int, default=5); p.add_argument("--gap", type=float, default=3.0)
    args = ap.parse_args(argv)
    hk = hk or HomeKit(Store(store_path()))
    try:
        if args.cmd == "scan":
            paired = set(hk.store.load()["devices"])
            for d in sorted(hk.discover(), key=lambda d: d["name"]):
                state = "ours" if d["id"] in paired else ("paired elsewhere" if d["paired"] else "unpaired: can be paired here")
                out(f'{d["id"]}  {d["name"]:<28} {d["host"]:<16} {state}')
        elif args.cmd == "pair":
            out(hk.pair(args.id.lower(), args.code, args.name, args.area))
        elif args.cmd == "list":
            areas = hk.areas()
            for s in hk.states(fresh=True):
                out(f'{s["entity_id"]:<34} {s["state"]:<5} {areas.get(s["entity_id"], "")}')
        elif args.cmd == "label":
            hk.label(args.id.lower(), args.name, args.area)
            out("Done.")
        elif args.cmd == "unpair":
            hk.unpair(args.id.lower())
            out("Forgotten here (the device itself still thinks it is paired: reset it, or remove it in its own app, to pair it elsewhere).")
        elif args.cmd == "say":
            out(hk.control(args.name, args.action, args.value))
        elif args.cmd == "prove":
            return prove(hk, args.name, args.rounds, args.gap, out, sleep)
    except HomeError as e:
        out(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
