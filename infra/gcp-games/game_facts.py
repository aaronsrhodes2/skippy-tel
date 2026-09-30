#!/usr/bin/env python3
"""What is installed, per running game: the facts behind the portal's admin-only "Server admin" card.

diag.sh (every 2 minutes) runs this from instance metadata (`game-facts`) and its output lands in the
serial-port snapshot the portal already reads for join codes. One line per fact, per game that is up:

    VERSION <game>=<installed server version>            valheim: l-1.0.16 (Steam build 25527701)
    IMAGE <game>=<image tag> <image built, UTC>          ghcr.io/lloesche/valheim-server:latest 2026-09-19T19:48:18Z
    UPDATED <game>=<server files last updated, UTC>      Steam's appmanifest LastUpdated (Steam games only)
    STARTED <game>=<container started, UTC>
    MODS <game>=<name>@<version>;<name>@<version>         empty = the game has mod support and none is loaded;
                                                          no line = this game has no mod support we can read
    MODUPDATE factorio=checked:2,downloaded:0,...        what the last start's mod update did (Factorio only)

Where each one comes from was observed in the real images (docs/games/MODS_AND_UPDATES.md); the samples the
tests use are under portal/tests/fixtures/admin_panel/. Never printed: settings, environment, passwords, tokens
(`docker inspect` is only ever asked for fixed fields, never its environment; logs are only searched, never echoed).
Read-only, stdlib only, and every source is optional: a missing file or a failing docker call drops that one line.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path("/srv/games/data")
COMPOSE_GAMES = ("valheim", "factorio", "satisfactory", "dragonwilds")     # one container each, named like the game
ALL_GAMES = COMPOSE_GAMES + ("everquest",)
EQ_PREFIX, EQ_CONTAINER = "akk-stack-", "akk-stack-eqemu-server-1"          # AkkStack's own compose project
LOG_BUDGET_S = 20                     # a runaway `docker logs` is cut off, keeping what it found so far
MAX_MODS = 300
VERSION_OK = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,39}$")

# Where a Steam game's appmanifest can be, relative to data/<game>. Valheim's is observed: the image mounts
# data/valheim/server at /opt/valheim and steamcmd installs into /opt/valheim/dl/server. The others are where their
# images install, UNVERIFIED for Satisfactory (/config/gamefiles) and Dragonwilds (/home/steam/server-files), so
# several places are tried.
APPMANIFEST_DIRS = ("server/dl/server/steamapps", "gamefiles/steamapps", "steamapps", "server-files/steamapps")


def run(*argv: str, timeout: int = 20) -> str:
    """A fixed argv, no shell. Anything that goes wrong reads as no answer."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def clean(text: str, limit: int = 160) -> str:
    """One printable line: it goes into a serial-port snapshot that is parsed line by line."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(text))
    return " ".join(text.split())[:limit]


def safe_version(text: str) -> str:
    text = str(text).strip()
    return text if VERSION_OK.match(text) else "?"


def iso_utc(value) -> str:
    """`2026-09-19T19:48:18.589333296Z`, `2025-02-27T19:35:09.786664332-06:00` (docker) or epoch seconds (Steam)
    -> `2026-09-19T19:48:18Z`. "" when it isn't a date."""
    try:
        if isinstance(value, (int, float)) or re.fullmatch(r"\d{9,11}", str(value).strip()):
            when = datetime.fromtimestamp(int(value), tz=timezone.utc)
        else:
            m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)", str(value).strip())
            if not m:
                return ""
            zone = timedelta(0) if m.group(3) == "Z" else \
                (1 if m.group(3)[0] == "+" else -1) * timedelta(hours=int(m.group(3)[1:3]), minutes=int(m.group(3)[4:6]))
            when = (datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S") - zone).replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return ""
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── parsers: text in, facts out (the tests feed them real captured files) ──────────────────────────────────

def steam_manifest(text: str) -> dict:
    """buildid and LastUpdated from a Steam appmanifest_<appid>.acf."""
    build = re.search(r'"buildid"\s+"(\d+)"', text)
    updated = re.search(r'"LastUpdated"\s+"(\d+)"', text)
    return {"build": build.group(1) if build else "", "updated": iso_utc(updated.group(1)) if updated else ""}


def valheim_version(text: str) -> str:
    """The server's own line, once per start: `Valheim version: l-1.0.16 (network version 40)` (the BepInEx log has
    `Valheim Version: l-1.0.16`). The last one wins."""
    found = re.findall(r"[Vv]alheim [Vv]ersion: (\S+)", text)
    return safe_version(found[-1]) if found else ""


def valheim_framework(text: str) -> str:
    """Which mod loader this start ran with, from the image's `Server config is ..., mod: BepInEx` line
    (`none` for a vanilla server). "" when the line isn't there."""
    found = re.findall(r"\bmod: (\w+)\s*$", text, re.M)
    return found[-1] if found else ""


_PLUGIN = re.compile(r"\[Info\s*:\s*BepInEx\] Loading \[(.+?) (\S+)\](?: \(([^)]*)\))?\s*$", re.M)
_PACK = re.compile(r"BepInExPack Valheim version (\S+)")


def bepinex_mods(text: str) -> list[tuple[str, str]]:
    """The mods a BepInEx start loaded, from BepInEx's own LogOutput.log (rewritten on every start):
    `[Info   :   BepInEx] Loading [Lab Plugin 1.2.3] (com.skippy.labplugin)`, preceded by the pack's own version
    line. The BepInEx pack is listed first as BepInExPack_Valheim (Thunderstore's name for it)."""
    mods: list[tuple[str, str]] = []
    pack = _PACK.search(text)
    if pack:
        mods.append(("BepInExPack_Valheim", safe_version(pack.group(1))))
    seen = set()
    for name, version, _guid in _PLUGIN.findall(text):
        if (name, version) not in seen:
            seen.add((name, version))
            mods.append((name, safe_version(version)))
    return mods


def factorio_version(text: str) -> str:
    """First line of factorio-current.log: `... Factorio 2.0.77 (build 84539, linux64, headless, space-age)`."""
    m = re.search(r"Factorio (\d+\.\d+\.\d+) \(build (\d+)", text)
    return f"{m.group(1)} (build {m.group(2)})" if m else ""


def factorio_mods(log: str, mods_dir_names: list[str]) -> list[tuple[str, str]]:
    """What the server loaded, from factorio-current.log (rewritten on every start). Every loaded mod has a
    `Checksum of <name>: <n>` line; the version is on `Loading mod <name> <version> (data.lua)`, which a mod with
    no data.lua doesn't get (observed): its version then comes from its file name in the mods folder,
    `<name>_<version>.zip` (or an unzipped `<name>_<version>/`)."""
    versions = {name: v for name, v in re.findall(r"Loading mod (\S+) (\S+) \(data\.lua\)", log)}
    on_disk = {}
    for entry in mods_dir_names:
        m = re.fullmatch(r"(.+)_(\d+(?:\.\d+)+)(?:\.zip)?", entry)
        if m:
            on_disk[m.group(1)] = m.group(2)
    names = []
    for name in re.findall(r"^\s*[\d.]+ Checksum of ([^\s:]+):", log, re.M):
        if name not in names:
            names.append(name)
    for name in versions:                    # a log without checksum lines still yields what it loaded
        if name != "core" and name not in names:
            names.append(name)
    return [(n, safe_version(versions.get(n) or on_disk.get(n) or "")) for n in names if n != "core"]


def factorio_update_summary(lines) -> dict[str, int | str]:
    """What the image's mod updater (UPDATE_MODS_ON_START) did this start, from the strings its scripts print
    (update-mods.sh, read in the real image). {} when it didn't run. The counts restart at each run."""
    def fresh() -> dict[str, int | str]:
        return {"checked": 0, "downloaded": 0, "current": 0, "failed": 0, "skipped": 0}

    out: dict[str, int | str] = {}
    for line in lines:
        text = line.strip()
        if "docker-update-mods.sh" in text:
            out = fresh()
            continue
        if text.startswith("You need to provide your Factorio"):
            out = out or fresh()
            out["nocreds"] = 1
            continue
        if not out:
            continue
        if text.startswith("Checking for update of mod"):
            out["checked"] = int(out["checked"]) + 1
        elif text == "Already up-to-date.":
            out["current"] = int(out["current"]) + 1
        elif text == "Download complete.":
            out["downloaded"] = int(out["downloaded"]) + 1
        elif text.startswith("Download failed: Code") or text in ("SHA1 mismatch!", "Downloaded file missing!"):
            out["failed"] = int(out["failed"]) + 1
            code = re.search(r"Code (\d+)", text)
            if code and "code" not in out:
                out["code"] = code.group(1)
        elif text.startswith(("Custom mod not on", "Not compatible with version", "Skipping mod ")) \
                and "factorio version mismatch" not in text and "unsatisfied base dependency" not in text:
            out["skipped"] = int(out["skipped"]) + 1
    return out


def uplugin_mods(files: dict[str, str]) -> list[tuple[str, str]]:
    """Satisfactory mods (UNVERIFIED: read from the modding docs, not from a run): each one is a folder under
    FactoryGame/Mods/ with a `<name>.uplugin` JSON file holding FriendlyName and VersionName."""
    mods = []
    for fallback, text in sorted(files.items()):
        try:
            info = json.loads(text)
        except ValueError:
            continue
        if isinstance(info, dict):
            mods.append((clean(info.get("FriendlyName") or fallback, 80), safe_version(info.get("VersionName") or "")))
    return mods


def akkstack_commit(head: str) -> str:
    """The commit AkkStack is checked out at, from .git/HEAD (a detached checkout holds the bare hash)."""
    head = head.strip()
    return head[:7] if re.fullmatch(r"[0-9a-f]{40}", head) else ""


# ── rendering ──────────────────────────────────────────────────────────────────────────────────────────────

def mods_field(mods: list[tuple[str, str]]) -> str:
    """`name@version;name@version`. ';' can't be in a name (it is the separator); the parser splits on the last '@'."""
    parts = [f"{clean(n, 80).replace(';', ',')}@{v}" for n, v in mods[:MAX_MODS]]
    return ";".join(parts)


def render(game: str, facts: dict) -> list[str]:
    """The snapshot lines for one game (see the module docstring)."""
    out = []
    if facts.get("version"):
        out.append(f"VERSION {game}={clean(facts['version'])}")
    if facts.get("image"):
        out.append(f"IMAGE {game}={clean(facts['image'], 120)} {facts.get('image_built', '')}".rstrip())
    if facts.get("updated"):
        out.append(f"UPDATED {game}={facts['updated']}")
    if facts.get("started"):
        out.append(f"STARTED {game}={facts['started']}")
    if facts.get("mods") is not None:
        out.append(f"MODS {game}={mods_field(facts['mods'])}")
    if facts.get("modupdate"):
        out.append(f"MODUPDATE {game}=" + ",".join(f"{k}:{v}" for k, v in facts["modupdate"].items()))
    return out


# ── reading the VM ─────────────────────────────────────────────────────────────────────────────────────────

def read(path: Path, limit: int = 4_000_000) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def scan_log(container: str, patterns: dict[str, re.Pattern]) -> dict[str, list[str]]:
    """Stream `docker logs`, keeping only the lines that match a pattern (never the whole log, never echoed)."""
    hits: dict[str, list[str]] = {k: [] for k in patterns}
    try:
        proc = subprocess.Popen(["docker", "logs", container], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
    except OSError:
        return hits
    deadline = time.monotonic() + LOG_BUDGET_S
    try:
        for line in proc.stdout:
            for key, pattern in patterns.items():
                if pattern.search(line):
                    hits[key].append(line.rstrip("\n")[:400])
                    if len(hits[key]) > 2000:
                        del hits[key][:1000]
            if time.monotonic() > deadline:
                break
    finally:
        proc.kill()
        proc.wait()
    return hits


def appmanifest(data: Path, game: str) -> dict:
    for sub in APPMANIFEST_DIRS:
        for path in sorted((data / game / sub).glob("appmanifest_*.acf")):
            info = steam_manifest(read(path, 200_000))
            if info["build"] or info["updated"]:
                return info
    return {}


def container_facts(name: str) -> dict:
    """image tag, when the image was built and when the container started: fixed fields only, never `.Config.Env`."""
    row = run("docker", "inspect", "-f", "{{.Config.Image}}\t{{.Image}}\t{{.State.StartedAt}}", name).split("\t")
    if len(row) != 3:
        return {}
    built = run("docker", "image", "inspect", "-f", "{{.Created}}", row[1])
    return {"image": row[0], "image_built": iso_utc(built), "started": iso_utc(row[2])}


def running_names() -> set[str]:
    return set(run("docker", "ps", "--format", "{{.Names}}").split())


def collect(data: Path = DATA, names: dict[str, str] | None = None, up: set[str] | None = None) -> dict[str, dict]:
    """Facts for every game that is up. `names` maps a game to its container (default: named like the game;
    EverQuest is AkkStack's own project); `up` is the set of running container names (default: ask docker)."""
    up = running_names() if up is None else up
    names = dict(names or {})
    if "everquest" not in names:
        names["everquest"] = EQ_CONTAINER if EQ_CONTAINER in up else next((n for n in sorted(up) if n.startswith(EQ_PREFIX)), "")
    facts: dict[str, dict] = {}
    for game in ALL_GAMES:
        container = names.get(game, game)
        if not container or container not in up:
            continue
        f = container_facts(container)
        if game == "valheim":
            f.update(valheim(data, container))
        elif game == "factorio":
            f.update(factorio(data, container))
        elif game == "everquest":
            f["version"] = akk_version(data)
        else:
            manifest = appmanifest(data, game)
            if manifest:
                f["version"] = f"Steam build {manifest['build']}" if manifest["build"] else ""
                f["updated"] = manifest["updated"]
            if game == "satisfactory":
                f["mods"] = uplugin_mods({p.parent.name: read(p, 100_000)
                                          for p in (data / "satisfactory" / "gamefiles" / "FactoryGame" / "Mods").glob("*/*.uplugin")})
        facts[game] = {k: v for k, v in f.items() if v not in ("", None) or k == "mods"}
    return facts


def valheim(data: Path, container: str) -> dict:
    hits = scan_log(container, {"version": re.compile(r"[Vv]alheim [Vv]ersion: "), "mod": re.compile(r"\bmod: \w+\s*$")})
    version = valheim_version("\n".join(hits["version"]))
    framework = valheim_framework("\n".join(hits["mod"]))
    manifest = appmanifest(data, "valheim")
    mods: list[tuple[str, str]] = []
    if framework == "BepInEx":                # LogOutput.log outlives the run: only trust it while BepInEx is what ran
        bep = read(data / "valheim" / "server" / "bepinex" / "BepInEx" / "LogOutput.log")
        mods = bepinex_mods(bep)
        version = version or valheim_version(bep)
    if manifest.get("build"):
        version = f"{version} (Steam build {manifest['build']})" if version else f"Steam build {manifest['build']}"
    return {"version": version, "updated": manifest.get("updated", ""), "mods": mods}


def factorio(data: Path, container: str) -> dict:
    log = read(data / "factorio" / "factorio-current.log")
    try:
        names = [p.name for p in (data / "factorio" / "mods").iterdir()]
    except OSError:
        names = []
    version = factorio_version(log)
    if not version:                           # the image labels itself with the version it carries
        version = safe_version(run("docker", "inspect", "-f", '{{index .Config.Labels "factorio.version"}}', container))
        version = "" if version == "?" else version
    hits = scan_log(container, {"u": re.compile(r"docker-update-mods\.sh|Checking for update of mod|Already up-to-date|"
                                                r"Download (complete|failed)|SHA1 mismatch|Downloaded file missing|"
                                                r"Custom mod not on|Not compatible with version|^Skipping mod |"
                                                r"You need to provide your Factorio")})
    return {"version": version, "mods": factorio_mods(log, names), "modupdate": factorio_update_summary(hits["u"])}


def akk_version(data: Path) -> str:
    commit = akkstack_commit(read(data / "everquest" / "akk-stack" / ".git" / "HEAD", 1000))
    return f"AkkStack {commit}" if commit else ""


def main(argv: list[str]) -> int:
    data = Path(argv[argv.index("--data") + 1]) if "--data" in argv else DATA
    facts = collect(data)
    for game in ALL_GAMES:
        for line in render(game, facts.get(game, {})):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
