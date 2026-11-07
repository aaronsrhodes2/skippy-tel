"""Set up the house: find the lights and switches on the network, pair them with their setup codes, give each a name and a room.

    ./.venv/Scripts/python homectl.py scan                                  every HomeKit accessory on the network (paired or not)
    ./.venv/Scripts/python homectl.py pair <id> <setup-code> "Living room dimmer" [--area "Living room"]
    ./.venv/Scripts/python homectl.py list                                  what Skippy can control, and its state
    ./.venv/Scripts/python homectl.py label <id> --name "..." --area "..."
    ./.venv/Scripts/python homectl.py unpair <id>
    ./.venv/Scripts/python homectl.py say "kitchen light" on                try a control (the same code the voice uses)
    ./.venv/Scripts/python homectl.py prove "theater fan" --rounds 5        switch it on and off, timing each and counting second tries

Matter (through skippy-core's controller, MATTER_URL in the vault; core/compose.matter.yml):
    ./.venv/Scripts/python homectl.py matter-info                           is the controller there, ready for Wi-Fi and Bluetooth setup
    ./.venv/Scripts/python homectl.py matter-pair 34970112332 "Theater Fan" --area "Theater"
                                                                            a NEW device: its 11-digit code (or MT:... from the QR code),
                                                                            set up over Bluetooth with the vault's ROOM_WIFI_SSID/PASSWORD
    ./.venv/Scripts/python homectl.py matter-pair 12345678901 "Thermostat" --network
                                                                            a device already on the network: the Nest, shared from the
                                                                            Google Home app (Settings > Linked Matter apps > Link apps)
    ./.venv/Scripts/python homectl.py matter-import levitons.txt                  # many at once: one `code, name, room` a line
                                                                            # (the My Leviton app: device > Device Settings >
                                                                            # Matter Pairing Code); safe to run again
    ./.venv/Scripts/python homectl.py identify "kitchen light"                 # make it flash: which switch is which
    ./.venv/Scripts/python homectl.py matter-label <node> --name "..." --area "..."
    ./.venv/Scripts/python homectl.py matter-forget <node>
    ./.venv/Scripts/python homectl.py storm start                           the colour lights drift between blue and white, each on its own random
                                                                            course, like a roiling stormy sky (lightshow.py); `storm stop` puts them
                                                                            back as they were, `storm status`, `storm start "LIFX 1787" ...` for some
`list`, `say` and `prove` cover HomeKit and Matter together.

The setup code is the 8 digits on the device (a Leviton dimmer: the label on its side and in the Leviton app). It is used once, never stored; what is
stored is the pairing key, in state/home/home.json (git-ignored). Set SKIPPY_HOME_STATE to keep it elsewhere.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

from homekit import HomeKit, Store
from house import Combined, HomeError

ROOT = Path(__file__).resolve().parents[2]


def store_path(env=os.environ) -> Path:
    return Path(env.get("SKIPPY_HOME_STATE") or ROOT / "state" / "home") / "home.json"


def matter_path(env=os.environ) -> Path:
    return Path(env.get("SKIPPY_HOME_STATE") or ROOT / "state" / "home") / "matter.json"


def generac_folder(env: dict) -> Path:
    """state/generac: the hub's generator mirror (generac.py)."""
    return Path(env.get("GENERAC_STATE") or Path(__file__).resolve().parents[2] / "state" / "generac")


def build_house(env: dict):
    """The house: what Skippy can control, through every backend that is set up (None when none is). hub.py shares it across rooms; house_mcp.py and the home
    reporter (the portal chat's house tools) build the same one.
    HomeKit (paired with homectl.py), Matter (skippy-core's controller: MATTER_URL), the garage door's ratgdo (GARAGE_ESPHOME), Leviton's cloud (LEVITON_EMAIL + LEVITON_PASSWORD),
    the Nest (Google Device Access through the portal's Google grant: SKIPPY_HOME_TOKEN; SKIPPY_NEST=0 leaves it out),
    the cameras (read only: Frigate on skippy-core, FRIGATE_URL, and the Reolink doorbell's button, REOLINK_DOORBELL; SKIPPY_CAMS=0 leaves them out),
    the Generac generator (read only, local: genmon on the generator's Pi at GENMON_URL; SKIPPY_GENERAC=0 leaves it out)
    and Home Assistant (HASS_URL + HASS_TOKEN)."""
    sensitive = env.get("HASS_SENSITIVE") == "1"
    parts = []
    if Store(store_path(env)).load()["devices"]:                    # lights and switches paired with homectl.py: HomeKit, natively
        kit = HomeKit(Store(store_path(env)), sensitive=sensitive)
        kit.states()                                                # learn what is there now, so keep-warm knows what to ping
        kit.keep_warm(float(env.get("HOMEKIT_KEEP_WARM", "30")))
        parts.append(kit)
    if env.get("MATTER_URL"):                                       # Matter, through skippy-core's controller (core/compose.matter.yml)
        from matter import Matter, Names                            # noqa: PLC0415
        parts.append(Matter(env["MATTER_URL"], Names(matter_path(env)), sensitive=sensitive))
    if env.get("GARAGE_ESPHOME"):                                   # the garage door: a ratgdo32 on the opener (rooms/garage.yaml), over ESPHome
        from garage import Garage                                   # noqa: PLC0415
        parts.append(Garage(env["GARAGE_ESPHOME"], env.get("GARAGE_ESPHOME_KEY", ""), sensitive=sensitive))
    if env.get("LEVITON_EMAIL") and env.get("LEVITON_PASSWORD"):    # every Leviton switch on his account, through Leviton's cloud: after
        from leviton import Leviton                                 # noqa: PLC0415  the local backends, so a switch paired locally wins
        parts.append(Leviton(env["LEVITON_EMAIL"], env["LEVITON_PASSWORD"], sensitive=sensitive))
    if env.get("SKIPPY_HOME_TOKEN") and env.get("SKIPPY_NEST", "1") != "0":   # the Nest thermostat, through Google's Device Access with the
        import google_home                                          # noqa: PLC0415  portal's Google grant (nest.py); quiet until it is connected
        from nest import Nest                                       # noqa: PLC0415
        parts.append(Nest(google_home.Google(env, log=lambda m: None), env.get("NEST_PROJECT_ID", ""), sensitive=sensitive))
    if env.get("FRIGATE_URL"):                                      # the cameras: Frigate on skippy-core and the Reolink doorbell, all local (cams.py)
        import cams                                                 # noqa: PLC0415
        part = cams.from_env(env, sensitive=sensitive)
        if part is not None:
            parts.append(part)
    if env.get("SKIPPY_GENERAC", "1") != "0" and (env.get("GENMON_URL") or (generac_folder(env) / "gen.json").is_file()):
        import generac                                              # noqa: PLC0415  the standby generator through genmon on its Pi (generac.py)
        parts.append(generac.from_env(env, generac_folder(env), sensitive=sensitive))   # only the hub polls, the rest read its mirror
    if env.get("HASS_URL") and env.get("HASS_TOKEN"):               # or Home Assistant, if he runs one (the vault holds both)
        from hass import Hass                                       # noqa: PLC0415
        parts.append(Hass(env["HASS_URL"], env["HASS_TOKEN"], sensitive=sensitive))
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else Combined(parts, sensitive=sensitive)


def load_env(path: str | None = None) -> dict:
    env = dict(os.environ)
    for candidate in (path, os.environ.get("SKIPPY_ENV"), "G:/My Drive/vault/.env"):
        if candidate and Path(candidate).is_file():
            for line in Path(candidate).read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
            break
    return env


def _env() -> dict:
    return load_env()


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


def new_device_wifi(env: dict) -> tuple[str, str]:
    """The Wi-Fi a brand-new device joins during Bluetooth setup: ROOM_WIFI_SSID/ROOM_WIFI_PASSWORD, else the house network the vault already
    names (HOME_WIFI/HOME_WIFI_PWD), the same fallback rooms/flash.sh uses for the pods."""
    ssid, password = env.get("ROOM_WIFI_SSID", ""), env.get("ROOM_WIFI_PASSWORD", "")
    if not (ssid and password):
        ssid, password = env.get("HOME_WIFI", ""), env.get("HOME_WIFI_PWD", "")
    return ssid, password


def matter_import(m, text: str, env: dict, new: bool, out=print) -> int:
    """Pair a list of devices one after another; one that fails doesn't stop the rest; a code already paired is skipped."""
    from matter import parse_import                                 # noqa: PLC0415
    rows = parse_import(text)
    if new:
        ssid, password = new_device_wifi(env)
        if not (ssid and password):
            raise HomeError("New devices need the Wi-Fi to join: ROOM_WIFI_SSID and ROOM_WIFI_PASSWORD (else HOME_WIFI/HOME_WIFI_PWD) in the vault .env.")
        m.set_wifi(ssid, password)
    done = skipped = failed = 0
    for n, (code, name, area) in enumerate(rows, 1):
        if m.already(code) is not None:
            out(f"{n}/{len(rows)} {name}: already paired")
            skipped += 1
            continue
        try:
            out(f"{n}/{len(rows)} {name}: " + m.pair(code, name, area, network_only=not new))
            done += 1
        except HomeError as e:
            out(f"{n}/{len(rows)} {name}: FAILED: {e}")
            failed += 1
    out(f"{done} paired, {skipped} already done, {failed} failed." + (" Run it again for the failed ones (a fresh code from the app if "
                                                                   "it timed out)." if failed else ""))
    return 0 if not failed else 1


def main(argv=None, hk: HomeKit | None = None, out=print, sleep=time.sleep, mt=None, env: dict | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("list")
    p = sub.add_parser("pair"); p.add_argument("id"); p.add_argument("code"); p.add_argument("name"); p.add_argument("--area", default="")
    p = sub.add_parser("label"); p.add_argument("id"); p.add_argument("--name", default=""); p.add_argument("--area", default="")
    p = sub.add_parser("unpair"); p.add_argument("id")
    p = sub.add_parser("say"); p.add_argument("name"); p.add_argument("action"); p.add_argument("value", nargs="?")
    p = sub.add_parser("prove"); p.add_argument("name"); p.add_argument("--rounds", type=int, default=5); p.add_argument("--gap", type=float, default=3.0)
    sub.add_parser("matter-info")
    p = sub.add_parser("matter-pair"); p.add_argument("code"); p.add_argument("name"); p.add_argument("--area", default=""); p.add_argument("--network", action="store_true")
    p = sub.add_parser("matter-label"); p.add_argument("node", type=int); p.add_argument("--name", default=""); p.add_argument("--area", default="")
    p = sub.add_parser("matter-forget"); p.add_argument("node", type=int)
    p = sub.add_parser("matter-import"); p.add_argument("file"); p.add_argument("--new", action="store_true")
    p = sub.add_parser("identify"); p.add_argument("target")
    p = sub.add_parser("storm"); p.add_argument("action", choices=("start", "stop", "status", "run")); p.add_argument("lights", nargs="*")
    args = ap.parse_args(argv)
    env = env if env is not None else _env()
    hk = hk or HomeKit(Store(store_path(env)))

    def matter():
        nonlocal mt
        if mt is None:
            if not env.get("MATTER_URL"):
                raise HomeError("MATTER_URL isn't set: put MATTER_URL=ws://skippy-core:5580/ws in the vault .env "
                                "(skippy-core runs the controller once core/install/matter.sh --apply has run there).")
            from matter import Matter, Names                        # noqa: PLC0415
            mt = Matter(env["MATTER_URL"], Names(matter_path(env)))
        return mt

    def house():
        return Combined([hk, matter()]) if (mt is not None or env.get("MATTER_URL")) else hk
    try:
        if args.cmd == "scan":
            paired = set(hk.store.load()["devices"])
            for d in sorted(hk.discover(), key=lambda d: d["name"]):
                state = "ours" if d["id"] in paired else ("paired elsewhere" if d["paired"] else "unpaired: can be paired here")
                out(f'{d["id"]}  {d["name"]:<28} {d["host"]:<16} {state}')
        elif args.cmd == "pair":
            out(hk.pair(args.id.lower(), args.code, args.name, args.area))
        elif args.cmd == "list":
            h = house()
            rows = h.states(fresh=True)
            areas = h.areas()
            for s in rows:
                out(f'{s["entity_id"]:<34} {s["state"]:<5} {areas.get(s["entity_id"], "")}')
        elif args.cmd == "label":
            hk.label(args.id.lower(), args.name, args.area)
            out("Done.")
        elif args.cmd == "unpair":
            hk.unpair(args.id.lower())
            out("Forgotten here (the device itself still thinks it is paired: reset it, or remove it in its own app, to pair it elsewhere).")
        elif args.cmd == "say":
            out(house().control(args.name, args.action, args.value))
        elif args.cmd == "prove":
            return prove(house(), args.name, args.rounds, args.gap, out, sleep)
        elif args.cmd == "matter-info":
            info = matter().info()
            out(f"Matter controller: fabric {info.get('fabric_id')}, SDK {info.get('sdk_version')}")
            out(f"  Bluetooth (for brand-new devices): {'ready' if info.get('bluetooth_enabled') else 'NOT available'}")
            out(f"  Wi-Fi details for new devices: {'set' if info.get('wifi_credentials_set') else 'not set (matter-pair sets them)'}")
        elif args.cmd == "matter-pair":
            m = matter()
            if not args.network:
                ssid, password = new_device_wifi(env)
                if not (ssid and password):
                    raise HomeError("A new device needs the Wi-Fi it should join: put ROOM_WIFI_SSID and ROOM_WIFI_PASSWORD "
                                    "(a 2.4 GHz network; else HOME_WIFI/HOME_WIFI_PWD) in the vault .env. (A device already on the network: add --network.)")
                m.set_wifi(ssid, password)
            out("Pairing (a new device over Bluetooth takes a minute or two; keep it close to skippy-core) ...")
            out(m.pair(args.code, args.name, args.area, network_only=args.network))
        elif args.cmd == "matter-label":
            matter().label(args.node, args.name, args.area)
            out("Done.")
        elif args.cmd == "matter-import":
            return matter_import(matter(), Path(args.file).read_text(encoding="utf-8"), env, args.new, out)
        elif args.cmd == "identify":
            if re.fullmatch(r"[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}", args.target):     # a HomeKit id from scan: paired or not
                out(hk.identify(accessory_id=args.target.lower()))
            else:
                h = house()
                one, close = h.find(args.target)
                if one is None:
                    out(h._ambiguous(args.target, close))
                    return 1
                backend = (h.owner(one["entity_id"]) if isinstance(h, Combined) else None) or h
                out(f"{h._label(one)}: " + backend.identify(eid=one["entity_id"]))
        elif args.cmd == "matter-forget":
            matter().forget(args.node)
            out("Removed from skippy-core's Matter fabric.")
        elif args.cmd == "storm":
            import lightshow                                        # noqa: PLC0415
            return lightshow.command(args.action, args.lights, env, matter_path(env).parent, matter, out)
    except HomeError as e:
        out(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
