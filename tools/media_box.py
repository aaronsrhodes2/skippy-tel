#!/usr/bin/env python3
"""The bedroom media box as a deployment target (the Captain, 2026-11-12: "the media box is now a deployment target for us like the hubs").

    python tools/media_box.py status            what is on the box against services/media-box/manifest.json (read only)
    python tools/media_box.py deploy            make the box match the manifest: install what is missing or different, remove the stock apps listed
    python tools/media_box.py deploy --dry-run  say what deploy would do, change nothing
    python tools/media_box.py pair IP:PORT CODE one-time: pair with the box's "Wireless debugging > Pair device with pairing code" screen

Every app is pinned by the SHA-256 of the exact file that is installed. A file is looked for in state/media-box/cache/<sha256>.<apk|apkm> first, else
downloaded (GitHub release asset, or APKMirror's page flow) and refused unless its hash matches. An .apkm bundle is unpacked in a fresh temp folder, its
base.apk's signing certificate is checked against the pin when the manifest has one, and only the splits the manifest lists are installed.
The box is reached with wireless adb: already paired, its connect port changes at every reboot, so it is found through mDNS (`adb mdns services`), or
5555 when the box has been put on a fixed port. What was installed is remembered in state/media-box/installed.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "services" / "media-box" / "manifest.json"
CACHE = ROOT / "state" / "media-box" / "cache"
STATE = ROOT / "state" / "media-box" / "installed.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


# ── pure parts (tested) ──
def load_manifest(path: Path = MANIFEST) -> dict:
    m = json.loads(path.read_text(encoding="utf-8"))
    for key in ("name", "host", "apps", "remove"):
        if key not in m:
            raise ValueError(f"manifest lacks {key!r}")
    for app in m["apps"]:
        if not re.fullmatch(r"[0-9a-f]{64}", app.get("sha256", "")):
            raise ValueError(f"{app.get('package')}: the pin must be a SHA-256")
        if app.get("kind") not in ("apk", "apkm"):
            raise ValueError(f"{app.get('package')}: kind must be apk or apkm")
    return m


def parse_mdns(text: str, host: str, serial: str = "") -> str | None:
    """`adb mdns services` -> "ip:port" of the box's connect service, or None. The box is known by its serial number (its address comes from DHCP and
    changed on 2026-11-12), else by the address in the manifest."""
    for line in text.splitlines():
        if "_adb-tls-connect" in line:
            m = re.search(r"(\d+\.\d+\.\d+\.\d+):(\d+)", line)
            if m and ((serial and serial in line) or m.group(1) == host):
                return f"{m.group(1)}:{m.group(2)}"
    return None


def parse_packages(text: str) -> set[str]:
    return {line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("package:")}


def plan(manifest: dict, installed: set[str], recorded: dict) -> dict:
    """What deploy has to do: {"install": [app...], "remove": [package...]}. An app is installed when it is absent or its recorded hash is not the pin."""
    todo = [a for a in manifest["apps"] if a["package"] not in installed or recorded.get(a["package"]) != a["sha256"]]
    return {"install": todo, "remove": [p for p in manifest["remove"] if p in installed]}


def pick_splits(names: list[str], wanted: list[str]) -> list[str]:
    """The files of a bundle to install: base.apk and the split_config.<x>.apk of each wanted x (in that order)."""
    out = ["base.apk"] if "base.apk" in names else []
    for w in wanted:
        n = f"split_config.{w}.apk"
        if n in names:
            out.append(n)
    if not out:
        raise ValueError("the bundle has no base.apk")
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cached(app: dict, cache: Path = CACHE) -> Path | None:
    p = cache / f"{app['sha256']}.{app['kind']}"
    return p if p.is_file() and sha256_of(p) == app["sha256"] else None


# ── the network ──
def _get(url: str, jar: dict | None = None, referer: str = "", binary: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Referer": referer} if referer else {}), **({"Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())} if jar else {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        if jar is not None:
            for c in r.headers.get_all("Set-Cookie") or []:
                k, _, v = c.split(";", 1)[0].partition("=")
                jar[k] = v
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def fetch(app: dict, cache: Path = CACHE) -> Path:
    """The pinned file, from the cache or downloaded and hash-checked."""
    hit = cached(app, cache)
    if hit:
        return hit
    src = app["source"]
    if "github" in src:
        url = f"https://github.com/{src['github']}/releases/download/{src['tag']}/{src['asset']}"
        data = _get(url, binary=True)
    elif "apkmirror" in src:
        base, jar = "https://www.apkmirror.com", {}
        page = f"{base}/apk/{src['apkmirror']}/{src['release']}-release/{src['variant']}/"
        html = _get(page, jar)
        key = re.search(r'href="([^"]*download/\?key=[^"]+)"', html)
        if not key:
            raise RuntimeError("APKMirror: no download button on the variant page")
        html2 = _get(base + key.group(1).replace("&amp;", "&"), jar, referer=page)
        final = re.search(r'href="(/wp-content/themes/APKMirror/download\.php\?id=[^"]+)"', html2)
        if not final:
            raise RuntimeError("APKMirror: no final download link (the site may be asking for a check)")
        data = _get(base + final.group(1).replace("&amp;", "&"), jar, referer="https://www.apkmirror.com/", binary=True)
    else:
        raise ValueError("unknown source")
    got = hashlib.sha256(data).hexdigest()
    if got != app["sha256"]:
        raise RuntimeError(f"{app['package']}: the download is {got}, the pin is {app['sha256']}: refused")
    cache.mkdir(parents=True, exist_ok=True)
    p = cache / f"{app['sha256']}.{app['kind']}"
    p.write_bytes(data)
    return p


# ── adb ──
def adb(*args: str, serial: str | None = None, timeout: int = 300) -> str:
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timed out"                                      # a box that is off, or has wireless debugging off, does not answer a connect
    return (r.stdout + r.stderr).strip()


def scan_adb_port(host: str, port: int = 5555, workers: int = 64) -> list[str]:
    """Addresses on host's /24 that answer on the fixed adb port (the box's Wi-Fi MAC is random per boot, so its DHCP address moves)."""
    import concurrent.futures
    import socket
    prefix = host.rsplit(".", 1)[0]

    def probe(i: int) -> str | None:
        ip = f"{prefix}.{i}"
        try:
            with socket.create_connection((ip, port), timeout=0.4):
                return ip
        except OSError:
            return None
    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        return [ip for ip in ex.map(probe, range(1, 255)) if ip]


def find_serial(host: str, serial: str = "") -> str | None:
    """A connected adb serial for the box. The fixed port (the box's "Internet Adb" switch, 5555, which survives reboots) at the manifest address, then
    wherever mDNS says the box is, then a scan of the subnet for 5555 (the box is told by its serial number). Wireless debugging's random port is the last
    resort: Android drops that setting at reboot."""
    for line in adb("devices").splitlines()[1:]:
        if line.endswith("device") and line.startswith(host + ":"):
            return line.split()[0]
    mdns = parse_mdns(adb("mdns", "services"), host, serial)
    tried: list[str] = []
    for ip in [host] + ([mdns.split(":")[0]] if mdns else []):
        cand = f"{ip}:5555"
        if cand not in tried:
            tried.append(cand)
            if "connected" in adb("connect", cand, timeout=15) and _is_box(cand, serial):
                return cand
    for ip in scan_adb_port(host):
        cand = f"{ip}:5555"
        if cand not in tried and "connected" in adb("connect", cand, timeout=15) and _is_box(cand, serial):
            return cand
    if mdns and "connected" in adb("connect", mdns, timeout=20):
        return mdns
    return None


def _is_box(cand: str, serial: str) -> bool:
    """The device at cand is the box (its serial number matches), never another Android on the net."""
    return not serial or adb("shell", "getprop", "ro.serialno", serial=cand, timeout=20).strip() == serial


def check_signer(apk: Path, want: str) -> None:
    for tool in sorted(Path("C:/Android/sdk/build-tools").glob("*/apksigner.bat"), reverse=True) + [Path(shutil.which("apksigner") or "")]:
        if tool and tool.is_file():
            out = subprocess.run([str(tool), "verify", "--print-certs", str(apk)], capture_output=True, text=True).stdout
            m = re.search(r"certificate SHA-256 digest: ([0-9a-f]{64})", out)
            if not m or m.group(1) != want:
                raise RuntimeError(f"{apk.name}: signed by {m.group(1) if m else 'nobody'}, the pin says {want}")
            return
    raise RuntimeError("apksigner not found (Android SDK build-tools), so the signer cannot be checked")


def install(serial: str, app: dict, path: Path) -> str:
    if app["kind"] == "apk":
        if app.get("signer_sha256"):
            check_signer(path, app["signer_sha256"])
        return adb("install", "-r", str(path), serial=serial)
    with tempfile.TemporaryDirectory(prefix="media-box-") as tmp:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            files = pick_splits(names, app.get("splits", []))
            for n in files:
                z.extract(n, tmp)
        if app.get("signer_sha256"):
            check_signer(Path(tmp) / "base.apk", app["signer_sha256"])
        return adb("install-multiple", "-r", *[str(Path(tmp) / n) for n in files], serial=serial)


def installed_packages(serial: str) -> set[str]:
    return parse_packages(adb("shell", "pm", "list", "packages", serial=serial))


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    d = sub.add_parser("deploy")
    d.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("pair")
    p.add_argument("addr")
    p.add_argument("code")
    a = ap.parse_args(argv)
    if a.cmd == "pair":
        print(adb("pair", a.addr, a.code))
        return 0
    m = load_manifest()
    serial = find_serial(m["host"], m.get("serial", ""))
    if serial is None:
        print(f"{m['name']}: not reachable. Is it on, with Developer options > Wireless debugging on? (pair once with: media_box.py pair IP:PORT CODE)")
        return 1
    have, recorded = installed_packages(serial), load_state()
    todo = plan(m, have, recorded)
    if a.cmd == "status":
        print(f"{m['name']} at {serial}")
        for app in m["apps"]:
            print(f"  {app['name']:<10} {'installed' if app['package'] in have else 'MISSING':<10} {'pinned' if recorded.get(app['package']) == app['sha256'] else 'not recorded as the pinned file'}")
        print(f"  stock apps still on the box: {', '.join(todo['remove']) or 'none'}")
        return 0 if not todo["install"] and not todo["remove"] else 2
    for app in todo["install"]:
        print(f"{'would install' if a.dry_run else 'installing'} {app['name']} ({app['sha256'][:12]})")
        if a.dry_run:
            continue
        out = install(serial, app, fetch(app))
        print("  " + out.splitlines()[-1] if out else "  (no output)")
        if "Success" not in out:
            return 1
        recorded[app["package"]] = app["sha256"]
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(recorded, indent=1), encoding="utf-8")
    launcher = m.get("launcher")
    if launcher and a.cmd == "deploy":
        proj = ROOT / launcher["project"]
        print(f"{'would build' if a.dry_run else 'building'} the launcher ({launcher['project']}) and make it the home screen")
        if not a.dry_run:
            gradle = "gradlew.bat" if sys.platform == "win32" else "./gradlew"
            r = subprocess.run([gradle, "--no-daemon", "assembleDebug", "-q"], cwd=proj, capture_output=True, text=True, timeout=900, shell=sys.platform == "win32")
            apk = proj / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
            if r.returncode != 0 or not apk.is_file():
                print("  launcher build failed: " + (r.stdout + r.stderr)[-600:])
                return 1
            print("  " + (adb("install", "-r", str(apk), serial=serial).splitlines() or ["(no output)"])[-1])
            print("  " + adb("shell", "cmd", "package", "set-home-activity", f"{launcher['package']}/{launcher['activity']}", serial=serial))
    if m.get("timezone") and a.cmd == "deploy" and not a.dry_run:
        adb("shell", "settings", "put", "global", "auto_time_zone", "0", serial=serial)
        adb("shell", "cmd", "alarm", "set-timezone", m["timezone"], serial=serial)
    for pkg in todo["remove"]:
        print(f"{'would remove' if a.dry_run else 'removing'} {pkg}")
        if not a.dry_run:
            print("  " + adb("shell", "pm", "uninstall", "--user", "0", pkg, serial=serial))
    if not todo["install"] and not todo["remove"]:
        print("the box already matches the manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
