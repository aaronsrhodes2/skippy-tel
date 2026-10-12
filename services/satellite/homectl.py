"""Set up the house: find the lights and switches on the network, pair them with their setup codes, give each a name and a room.

    ./.venv/Scripts/python homectl.py scan                                  every HomeKit accessory on the network (paired or not)
    ./.venv/Scripts/python homectl.py pair <id> <setup-code> "Living room dimmer" [--area "Living room"]
    ./.venv/Scripts/python homectl.py list                                  what Skippy can control, and its state
    ./.venv/Scripts/python homectl.py label <id> --name "..." --area "..."
    ./.venv/Scripts/python homectl.py unpair <id>
    ./.venv/Scripts/python homectl.py say "kitchen light" on                try a control (the same code the voice uses)

The setup code is the 8 digits on the device (a Leviton dimmer: the label on its side and in the Leviton app). It is used once, never stored; what is
stored is the pairing key, in state/home/home.json (git-ignored). Set SKIPPY_HOME_STATE to keep it elsewhere.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from homekit import HomeKit, Store
from house import HomeError

ROOT = Path(__file__).resolve().parents[2]


def store_path(env=os.environ) -> Path:
    return Path(env.get("SKIPPY_HOME_STATE") or ROOT / "state" / "home") / "home.json"


def main(argv=None, hk: HomeKit | None = None, out=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("list")
    p = sub.add_parser("pair"); p.add_argument("id"); p.add_argument("code"); p.add_argument("name"); p.add_argument("--area", default="")
    p = sub.add_parser("label"); p.add_argument("id"); p.add_argument("--name", default=""); p.add_argument("--area", default="")
    p = sub.add_parser("unpair"); p.add_argument("id")
    p = sub.add_parser("say"); p.add_argument("name"); p.add_argument("action"); p.add_argument("value", nargs="?")
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
    except HomeError as e:
        out(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
