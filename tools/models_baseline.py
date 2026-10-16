#!/usr/bin/env python3
"""Download the baseline model archive (tools/models_baseline.json) into MODELS_ROOT, once, resumably.

    python tools/models_baseline.py plan                  what would download, how big, what's done (no network)
    python tools/models_baseline.py run [--group core]    download everything not done yet, core first; then hash the new files
    python tools/models_baseline.py status                the last run's summary (state/models-baseline.json)

It is the midnight job's body (tools/install_models_baseline_task.sh registers it on skippy-pc). Built on
tools/models_archive.py: the same tree (huggingface/<Org>__<Repo>/, SOURCE.json with revision and licence) and its
MANIFEST.json, so `python tools/models_archive.py verify` checks the result.

Rules:
- An entry that finished is recorded in its folder's SOURCE.json (`baseline_done`) and is never fetched again, even
  if the repo changes upstream: this is a baseline, not a mirror. Delete the key to re-fetch.
- An interrupted file resumes (huggingface_hub keeps partial downloads); a failed entry is retried next night.
- A gated repo (needs "accept the licence" on the Hub) is reported, not fatal: accept it on huggingface.co with the
  account whose HF_TOKEN is in the vault, and the next run takes it.
- It stops before free space on MODELS_ROOT drops under reserve_free_gb.
The Hugging Face token is HF_TOKEN or HF_ID (the vault's name for it), from the environment, else from the vault .env
(VAULT_ENV, default G:/My Drive/vault/.env); it is never printed. Only gated repos need it.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIST = HERE / "models_baseline.json"
STATE = HERE.parent / "state" / "models-baseline.json"
GB = 1024 ** 3


def models_root() -> Path:
    return Path(os.environ.get("MODELS_ROOT", r"D:\models"))


def entry_key(e: dict) -> str:
    """One entry = one repo + its globs (the same repo can appear twice, e.g. two quants in two groups)."""
    raw = json.dumps({k: e.get(k) for k in ("repo", "url", "include", "ignore")}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def entry_name(e: dict) -> str:
    return e.get("repo") or e["dest"]


def dest_of(e: dict, root: Path) -> Path:
    if e.get("repo"):
        return root / "huggingface" / e["repo"].replace("/", "__")
    return root / e["dest"]


def load_list(path: Path = LIST) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for e in data["models"]:
        if bool(e.get("repo")) == bool(e.get("url")):
            raise ValueError(f"{e}: exactly one of repo or url")
        if e.get("url") and not e.get("dest"):
            raise ValueError(f"{e['url']}: a url needs a dest")
        if e.get("group") not in ("core", "extended"):
            raise ValueError(f"{entry_name(e)}: group must be core or extended")
    return data


def _source(folder: Path) -> dict:
    try:
        return json.loads((folder / "SOURCE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_done(e: dict, root: Path) -> bool:
    if e.get("url"):
        return dest_of(e, root).is_file()
    return entry_key(e) in (_source(dest_of(e, root)).get("baseline_done") or {})


def mark_done(e: dict, root: Path) -> None:
    folder = dest_of(e, root)
    info = _source(folder)
    done = info.get("baseline_done") or {}
    done[entry_key(e)] = {"include": e.get("include"), "ignore": e.get("ignore"), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    info["baseline_done"] = done
    (folder / "SOURCE.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


TOKEN_NAMES = ("HF_TOKEN", "HF_ID")      # the vault calls it HF_ID; huggingface_hub's own name is HF_TOKEN


def hf_token() -> str | None:
    for name in TOKEN_NAMES:
        if os.environ.get(name):
            return os.environ[name]
    vault = Path(os.environ.get("VAULT_ENV", "G:/My Drive/vault/.env"))
    try:
        lines = vault.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    found = {}
    for line in lines:
        key, _, value = line.strip().partition("=")
        if key in TOKEN_NAMES:
            found[key] = value.strip().strip('"').strip("'")
    return next((found[n] for n in TOKEN_NAMES if found.get(n)), None)


def ordered(data: dict, group: str | None) -> list[dict]:
    items = [e for e in data["models"] if group in (None, e["group"])]
    return sorted(items, key=lambda e: 0 if e["group"] == "core" else 1)   # stable: list order within a group


def free_gb(root: Path) -> float:
    probe = root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free / GB


def fetch_url(e: dict, root: Path) -> None:
    dest = dest_of(e, root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(e["url"], timeout=60) as r, open(part, "wb") as out:
        shutil.copyfileobj(r, out, 8 * 1024 * 1024)
    part.replace(dest)
    (dest.parent / (dest.name + ".SOURCE.json")).write_text(json.dumps(
        {"url": e["url"], "why": e.get("why"), "archived_at": time.strftime("%Y-%m-%d")}, indent=2), encoding="utf-8")


def fetch_repo(e: dict, root: Path, token: str | None) -> None:
    from huggingface_hub import snapshot_download     # skippy-pc has it (tools/models_archive.py uses it)
    import models_archive                               # the tree's SOURCE.json: revision + licence
    dest = dest_of(e, root)
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=e["repo"], local_dir=str(dest), allow_patterns=e.get("include"),
                      ignore_patterns=e.get("ignore"), token=token)
    if e.get("include") and not any(_matches(p, e["include"]) for p in _files(dest)):
        raise RuntimeError(f"{e['repo']}: no file matched {e['include']} (renamed upstream?)")
    keep = _source(dest).get("baseline_done")
    models_archive.write_source(dest, e["repo"])        # rewrites SOURCE.json: keep what earlier entries recorded
    if keep:
        info = _source(dest)
        info["baseline_done"] = keep
        (dest / "SOURCE.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


def _files(folder: Path):
    for p in folder.rglob("*"):
        if p.is_file() and ".cache" not in p.parts:
            yield p.relative_to(folder).as_posix()


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def _gated(exc: Exception) -> bool:
    name = type(exc).__name__
    return name in ("GatedRepoError",) or ("401" in str(exc) or "403" in str(exc)) and "huggingface" in str(exc).lower()


def run(data: dict, root: Path, group: str | None, log=print) -> dict:
    sys.path.insert(0, str(HERE))
    token = hf_token()
    reserve = float(data.get("reserve_free_gb", 200))
    summary = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "root": str(root), "token": bool(token),
               "done": [], "skipped": [], "gated": [], "failed": [], "stopped": None}
    root.mkdir(parents=True, exist_ok=True)
    for e in ordered(data, group):
        name = entry_name(e)
        if is_done(e, root):
            summary["skipped"].append(name)
            continue
        free = free_gb(root)
        if free - e.get("approx_gb", 0) < reserve:
            summary["stopped"] = f"disk: {free:.0f} GB free, {name} needs ~{e.get('approx_gb', 0)} GB, reserve {reserve:.0f} GB"
            log(summary["stopped"])
            break
        log(f"[{e['group']}] {name} (~{e.get('approx_gb', 0)} GB): {e.get('why', '')}")
        t0 = time.monotonic()
        try:
            if e.get("url"):
                fetch_url(e, root)
            else:
                fetch_repo(e, root, token)
                mark_done(e, root)
        except Exception as exc:  # noqa: BLE001 — one model's trouble never stops the others
            if _gated(exc):
                summary["gated"].append(name)
                log(f"  gated: accept the licence at https://huggingface.co/{e.get('repo')} (and put HF_ID in the vault)")
            else:
                summary["failed"].append({"name": name, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
                log(f"  FAILED: {type(exc).__name__}: {str(exc)[:300]}")
            continue
        summary["done"].append(name)
        log(f"  ok in {(time.monotonic() - t0) / 60:.1f} min")
    summary["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "run", "status"])
    ap.add_argument("--group", choices=["core", "extended"])
    ap.add_argument("--no-manifest", action="store_true", help="run: skip hashing the archive afterwards")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(STATE.read_text(encoding="utf-8") if STATE.exists() else "no run yet")
        return 0
    data = load_list()
    root = models_root()
    if a.cmd == "plan":
        total = 0
        for e in ordered(data, a.group):
            done = is_done(e, root)
            total += 0 if done else e.get("approx_gb", 0)
            print(f"{'done' if done else 'todo':4}  {e['group']:8} {e['kind']:6} ~{e.get('approx_gb', 0):3} GB  {entry_name(e)}")
        print(f"to download: ~{total} GB into {root} (keeps {data.get('reserve_free_gb', 200)} GB free)")
        return 0

    def log(msg: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    summary = run(data, root, a.group, log)
    if summary["done"] and not a.no_manifest:
        import models_archive
        log("hashing the archive (MANIFEST.json; unchanged files are not re-read)")
        models_archive.cmd_manifest(None)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log(f"done {len(summary['done'])}, already had {len(summary['skipped'])}, gated {len(summary['gated'])}, "
        f"failed {len(summary['failed'])}" + (f"; stopped: {summary['stopped']}" if summary["stopped"] else ""))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
