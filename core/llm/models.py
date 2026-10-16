#!/usr/bin/env python3
"""SkippyCore's model slots: pick the variant per slot, check the files, render llama-swap's config.

The manifest (core/models.json) gives every slot a small STAND-IN and the FULL model. Which one a slot runs is
decided by CORE_FULL (a comma list of slots, "all", or empty), never by code: the simulator and CI run stand-ins,
the Bosgame onlines the big ones as their downloads land and verify.

    python3 core/llm/models.py plan     [--full chat,general]          what each slot would run, and whether it fits
    python3 core/llm/models.py check    [--full ...] [--hash]          are the files there (size; --hash: sha256 too)? exit 1 if not
    python3 core/llm/models.py render   [--full ...] --out DIR         write DIR/config.yaml (llama-swap) + DIR/shim.json
    python3 core/llm/models.py fetch    [--full ...] [--slots chat]    download what's missing from Hugging Face, verify, print hashes

MODELS_ROOT (default /srv/models) holds the files; GPU_LAYERS (default 999) is passed to llama-server as -ngl
(the simulator sets 0: no GPU there, same image, same flags otherwise). Standard library only: this runs on the
bare Ubuntu host from the deployer, before any container exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE.parent / "models.json"
CHUNK = 8 * 1024 * 1024
GB = 1024 ** 3
OVERHEAD_GB = 4          # per loaded model: KV cache at the slot's ctx, compute buffers (estimate; the burn-in measures it)
IDLE_TTL_S = 900         # an on-demand model unloads after 15 idle minutes (frees memory for image/music jobs)
LLAMA_SERVER = "/app/llama-server"   # where the llama-swap image keeps it


class ManifestError(ValueError):
    pass


def load(path: Path = MANIFEST) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    slots = data.get("slots") or {}
    if not slots:
        raise ManifestError("no slots")
    seen: dict[str, str] = {}
    for name, slot in slots.items():
        for variant in ("standin", "full"):
            v = slot.get(variant) or {}
            if not v.get("repo") or not v.get("files"):
                raise ManifestError(f"{name}.{variant}: repo and files are required")
            for f in v["files"]:
                if not f.get("path") or not isinstance(f.get("size"), int) or f["size"] <= 0:
                    raise ManifestError(f"{name}.{variant}: every file needs a path and a positive size")
                if ".." in Path(f["path"]).parts or Path(f["path"]).is_absolute():
                    raise ManifestError(f"{name}.{variant}: {f['path']} must stay under the repo folder")
                sha = f.get("sha256")
                if sha is not None and (len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)):
                    raise ManifestError(f"{name}.{variant}: {f['path']}: sha256 must be 64 lowercase hex or null")
        for alias in [name, *slot.get("aliases", [])]:
            if alias in seen:
                raise ManifestError(f"name {alias!r} is used by both {seen[alias]} and {name}")
            seen[alias] = name
    return data


def parse_full(value: str | None, slots: dict) -> set[str]:
    """CORE_FULL: "" (all stand-ins), "all", or a comma list of slot names. An unknown name is an error, not a no-op."""
    value = (value or "").strip()
    if not value:
        return set()
    if value == "all":
        return set(slots)
    names = {s.strip() for s in value.split(",") if s.strip()}
    unknown = names - set(slots)
    if unknown:
        raise ManifestError(f"CORE_FULL names unknown slot(s): {', '.join(sorted(unknown))} (slots: {', '.join(slots)})")
    return names


def repo_dir(repo: str) -> str:
    return "huggingface/" + repo.replace("/", "__")


def plan(data: dict, full: set[str]) -> dict:
    """Per slot: the chosen variant, its files (relative to MODELS_ROOT) and its size."""
    out = {}
    for name, slot in data["slots"].items():
        variant = "full" if name in full else "standin"
        v = slot[variant]
        files = [{**f, "rel": f"{repo_dir(v['repo'])}/{f['path']}"} for f in v["files"]]
        out[name] = {
            "variant": variant, "repo": v["repo"], "files": files, "ctx": v.get("ctx", 4096), "args": v.get("args", []),
            "size": sum(f["size"] for f in files), "resident": bool(slot.get("resident")),
            "aliases": slot.get("aliases", []),
        }
    return out


def budget(data: dict, chosen: dict) -> dict:
    """Peak memory: every resident slot, plus the biggest on-demand one (they swap, never load together)."""
    def need(s):
        return s["size"] / GB + OVERHEAD_GB
    resident = sum(need(s) for s in chosen.values() if s["resident"])
    on_demand = max([need(s) for s in chosen.values() if not s["resident"]] or [0])
    peak = resident + on_demand
    limit = float(data.get("memory_budget_gb", 110))
    return {"peak_gb": round(peak, 1), "limit_gb": limit, "fits": peak <= limit}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def check(chosen: dict, root: Path, *, hash_files: bool = False) -> list[str]:
    """Problems with the chosen files ([] = ready). Size always; sha256 when asked and pinned."""
    problems = []
    for name, s in chosen.items():
        for f in s["files"]:
            p = root / f["rel"]
            if not p.is_file():
                problems.append(f"{name} ({s['variant']}): missing {p}")
                continue
            size = p.stat().st_size
            if size != f["size"]:
                problems.append(f"{name} ({s['variant']}): {p} is {size} bytes, expected {f['size']} (partial download?)")
                continue
            if hash_files and f.get("sha256"):
                got = sha256_of(p)
                if got != f["sha256"]:
                    problems.append(f"{name} ({s['variant']}): {p} sha256 {got} != pinned {f['sha256']}")
    return problems


def render(chosen: dict, gpu_layers: int, extra_args: str = "") -> tuple[dict, dict]:
    """llama-swap's config (JSON is valid YAML, so no YAML library is needed) and the shim's name table.

    Resident slots sit in a persistent group that nothing unloads; on-demand slots share one swap group, so loading
    one unloads the other (the 120B and the coder never sit in memory together) and they drop after IDLE_TTL_S."""
    models, resident, on_demand, names = {}, [], [], []
    for name, s in chosen.items():
        first = s["files"][0]["rel"]          # llama.cpp finds the other parts of a split GGUF from the first one
        cmd = [LLAMA_SERVER, "--host", "127.0.0.1", "--port", "${PORT}", "-m", f"/models/{first}",
               "--ctx-size", str(s["ctx"]), "-ngl", str(gpu_layers), "--alias", name, *s["args"],
               *shlex.split(extra_args)]
        entry = {"cmd": " ".join(cmd), "aliases": list(s["aliases"])}
        if not s["resident"]:
            entry["ttl"] = IDLE_TTL_S
        models[name] = entry
        (resident if s["resident"] else on_demand).append(name)
        for n in [name, *s["aliases"]]:
            names.append({"name": n, "slot": name, "variant": s["variant"], "repo": s["repo"], "size": s["size"]})
    groups = {}
    if resident:
        groups["resident"] = {"swap": False, "exclusive": False, "persistent": True, "members": resident}
    if on_demand:
        groups["on-demand"] = {"swap": True, "exclusive": False, "members": on_demand}
    config = {"healthCheckTimeout": 600, "logLevel": "info", "startPort": 10001, "models": models, "groups": groups}
    return config, {"models": names}


def _download(url: str, dest: Path, size: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    token = os.environ.get("HF_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab" if have and r.status == 206 else "wb") as out:
        done, last = (have if r.status == 206 else 0), time.monotonic()
        for block in iter(lambda: r.read(CHUNK), b""):
            out.write(block)
            done += len(block)
            if time.monotonic() - last > 10:
                print(f"  {dest.name}: {done / GB:.2f} / {size / GB:.2f} GB", flush=True)
                last = time.monotonic()
    if part.stat().st_size != size:
        raise OSError(f"{dest.name}: got {part.stat().st_size} bytes, expected {size}")
    part.replace(dest)


def fetch(chosen: dict, root: Path, slots: set[str] | None = None) -> list[str]:
    """Download missing files (resumable), check size and any pinned hash, and print each file's sha256 to pin."""
    problems = []
    for name, s in chosen.items():
        if slots and name not in slots:
            continue
        for f in s["files"]:
            p = root / f["rel"]
            if not (p.is_file() and p.stat().st_size == f["size"]):
                url = f"https://huggingface.co/{s['repo']}/resolve/main/{f['path']}"
                print(f"{name} ({s['variant']}): downloading {url}", flush=True)
                try:
                    _download(url, p, f["size"])
                except OSError as e:
                    problems.append(f"{name}: {e}")
                    continue
            got = sha256_of(p)
            pinned = f.get("sha256")
            state = "ok" if got == pinned else ("NOT PINNED" if not pinned else f"MISMATCH (pinned {pinned})")
            print(f"{name} ({s['variant']}): {f['rel']} sha256 {got} {state}", flush=True)
            if pinned and got != pinned:
                problems.append(f"{name}: {p} sha256 mismatch")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "check", "render", "fetch"])
    ap.add_argument("--full", default=os.environ.get("CORE_FULL", ""), help="slots to run full-size (default: $CORE_FULL)")
    ap.add_argument("--root", default=os.environ.get("MODELS_ROOT", "/srv/models"))
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--out", help="render: directory for config.yaml and shim.json")
    ap.add_argument("--hash", action="store_true", help="check: also verify pinned sha256 (reads every byte)")
    ap.add_argument("--slots", default="", help="fetch: only these slots")
    a = ap.parse_args(argv)
    try:
        data = load(Path(a.manifest))
        chosen = plan(data, parse_full(a.full, data["slots"]))
    except (ManifestError, OSError, json.JSONDecodeError) as e:
        print(f"manifest: {e}", file=sys.stderr)
        return 2
    root = Path(a.root)
    b = budget(data, chosen)
    if a.cmd == "plan":
        for name, s in chosen.items():
            print(f"{name:8} {s['variant']:8} {s['size'] / GB:6.1f} GB  {s['repo']}  {'resident' if s['resident'] else 'on demand'}")
        print(f"peak {b['peak_gb']} GB of {b['limit_gb']} GB: {'fits' if b['fits'] else 'DOES NOT FIT'}")
        return 0 if b["fits"] else 1
    if not b["fits"]:
        print(f"memory: peak {b['peak_gb']} GB is over the {b['limit_gb']} GB budget", file=sys.stderr)
        return 1
    if a.cmd == "fetch":
        problems = fetch(chosen, root, {s for s in a.slots.split(",") if s} or None)
    else:
        problems = check(chosen, root, hash_files=a.hash)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    if a.cmd == "render":
        if not a.out:
            print("render needs --out", file=sys.stderr)
            return 2
        config, shim = render(chosen, int(os.environ.get("GPU_LAYERS", "999")), os.environ.get("LLAMA_EXTRA_ARGS", ""))
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "config.yaml").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        (out / "shim.json").write_text(json.dumps(shim, indent=2) + "\n", encoding="utf-8")
        print(f"rendered {out}/config.yaml: " + ", ".join(f"{n}={s['variant']}" for n, s in chosen.items()))
    elif a.cmd == "check":
        print("ready: " + ", ".join(f"{n}={s['variant']}" for n, s in chosen.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
