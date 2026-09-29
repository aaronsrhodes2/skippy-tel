#!/usr/bin/env python3
"""Archive the local AI models: one organized, hash-verifiable tree.

    D:\\models\\                       (override with MODELS_ROOT)
      ollama\\models\\{blobs,manifests}  every Ollama store on this PC, merged. Blobs are
                                        content-addressed (sha256-<hex>): the name IS the
                                        hash, so a blob whose bytes differ is corrupt and
                                        identical blobs from different stores collapse.
      huggingface\\<Org>__<Repo>\\       pinned snapshots (SOURCE.json: revision + license)
      finetunes\\<name>\\               the Captain's own models: not re-downloadable
      MANIFEST.json                     every file: size + sha256; every Ollama model and
                                        whether all of its blobs are present
      README.md                         how to restore

Commands (all idempotent; nothing is ever deleted or moved out of a source store):
    inventory                 what's where and how big
    ollama                    merge the host Ollama and every Docker ollama volume
    adopt SRC REPO            move an already-downloaded HF snapshot into the tree
    hf REPO [--ignore GLOB]   download a HF repo (pinned) into the tree
    finetune NAME SRC...      copy your own model directories in
    manifest                  (re)hash everything (unchanged files aren't re-read)
    verify [--quick]          re-check every file against the manifest; exit 1 on trouble
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(os.environ.get("MODELS_ROOT", r"D:\models"))
MANIFEST = "MANIFEST.json"
CHUNK = 8 * 1024 * 1024
HELPER_IMAGE = "ollama/ollama:latest"      # already on the machine; it only runs `cp`
BOOKKEEPING = {".cache"}                   # huggingface_hub's per-download notes: not model data

README = """# Local model archive

Built by SkippyTel `tools/models_archive.py`. `MANIFEST.json` lists every file with its
size and sha256, and every Ollama model with whether all of its blobs are present.
Check it any time:  `python tools/models_archive.py verify`   (`--quick` = sizes only).

- `ollama/models/`   an Ollama store. To restore: copy it over `%USERPROFILE%\\.ollama\\models`
                     (or into a Docker volume at `/root/.ollama/models`), then `ollama list`.
                     Blob names are their own sha256: `sha256-<hex>`.
- `huggingface/`     pinned snapshots; `SOURCE.json` has the exact revision and license.
                     Some repos carry custom Python (`custom_code`): read it before running.
- `finetunes/`       our own models. Not on any registry: this is the only copy.

Licenses travel with the weights (see each SOURCE.json): some are non-commercial.
This archive is for personal use; don't redistribute weights whose license forbids it.
"""


# ── hashing ───────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def files_under(root: Path):
    """Every archived file (skipping bookkeeping and the manifest itself), as (relpath, Path)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in BOOKKEEPING]
        for name in filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if rel in (MANIFEST, MANIFEST + ".sha256") or name.endswith("-partial"):
                continue
            yield rel, p


# ── Ollama stores ─────────────────────────────────────────────────────────────

def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def ollama_volumes() -> list[str]:
    out = _docker("volume", "ls", "--format", "{{.Name}}")
    return [n for n in out.stdout.split() if "ollama" in n] if out.returncode == 0 else []


COPY_IN_CONTAINER = r"""
src=/src; [ -d /src/models/blobs ] && src=/src/models
[ -d "$src/blobs" ] || { echo "no models here"; exit 0; }
mkdir -p /dst/blobs /dst/manifests; n=0
for f in "$src"/blobs/sha256-*; do
  b=$(basename "$f"); case "$b" in *-partial*) continue;; esac
  [ -e "/dst/blobs/$b" ] || { cp "$f" "/dst/blobs/$b" && n=$((n+1)); }
done
if [ -d "$src/manifests" ]; then (cd "$src/manifests" && find . -type f) | while read m; do
  if [ -e "/dst/manifests/$m" ]; then cmp -s "$src/manifests/$m" "/dst/manifests/$m" || echo "CONFLICT (kept the first) $m"
  else mkdir -p "/dst/manifests/$(dirname "$m")"; cp "$src/manifests/$m" "/dst/manifests/$m"; fi
done; fi
echo "copied $n new blobs"
"""


def copy_host_store(src: Path, dst: Path) -> str:
    if not (src / "blobs").is_dir():
        return "no models here"
    (dst / "blobs").mkdir(parents=True, exist_ok=True)
    n = 0
    for blob in (src / "blobs").glob("sha256-*"):
        if blob.name.endswith("-partial") or (dst / "blobs" / blob.name).exists():
            continue
        shutil.copy2(blob, dst / "blobs" / blob.name)
        n += 1
    conflicts = []
    for m in (src / "manifests").rglob("*"):
        if m.is_file():
            target = dst / "manifests" / m.relative_to(src / "manifests")
            if target.exists():
                if target.read_bytes() != m.read_bytes():
                    conflicts.append(m.name)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(m, target)
    return f"copied {n} new blobs" + (f"; CONFLICT (kept the first): {conflicts}" if conflicts else "")


def cmd_ollama(_args) -> int:
    dst = ROOT / "ollama" / "models"
    dst.mkdir(parents=True, exist_ok=True)
    host = Path.home() / ".ollama" / "models"
    print(f"host store {host}: {copy_host_store(host, dst)}", flush=True)
    for vol in ollama_volumes():
        r = _docker("run", "--rm", "--entrypoint", "sh", "-v", f"{vol}:/src:ro",
                    "-v", f"{dst.as_posix()}:/dst", HELPER_IMAGE, "-c", COPY_IN_CONTAINER)
        print(f"volume {vol}: {(r.stdout.strip().splitlines() or [r.stderr.strip()[:200]])[-1]}", flush=True)
    for name, info in ollama_models(dst).items():
        print(f"  {name:<40} {info['size'] / 1e9:6.1f} GB" + (f"  MISSING BLOBS {info['missing']}" if info["missing"] else ""))
    return 0


def ollama_models(store: Path) -> dict[str, dict]:
    """{'qwen3:8b': {'size': bytes, 'missing': [digest, ...]}} from the manifests in a store."""
    out: dict[str, dict] = {}
    base = store / "manifests" / "registry.ollama.ai"
    if not base.is_dir():
        return out
    for m in sorted(p for p in base.rglob("*") if p.is_file()):
        parts = m.relative_to(base).parts                     # library/qwen3/8b  or  hf.co/user/repo/tag
        name = ("/".join(parts[1:-1]) if parts[0] == "library" else "/".join(parts[:-1])) + ":" + parts[-1]
        try:
            doc = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[name] = {"size": 0, "missing": ["unreadable manifest"]}
            continue
        layers = [doc.get("config") or {}] + list(doc.get("layers") or [])
        missing = [d["digest"] for d in layers if d.get("digest")
                   and not (store / "blobs" / d["digest"].replace(":", "-")).exists()]
        out[name] = {"size": sum(int(d.get("size") or 0) for d in layers), "missing": missing}
    return out


# ── Hugging Face and our own models ───────────────────────────────────────────

def hf_revision(folder: Path) -> str | None:
    """The commit a local_dir snapshot came from (huggingface_hub notes it per file)."""
    for meta in (folder / ".cache" / "huggingface" / "download").rglob("*.metadata"):
        first = meta.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        if first:
            return first[0].strip()
    return None


def write_source(folder: Path, repo: str) -> None:
    info = {"repo": repo, "url": f"https://huggingface.co/{repo}", "revision": hf_revision(folder),
            "archived_at": time.strftime("%Y-%m-%d")}
    try:                                                     # the license, from the hub, best effort
        from huggingface_hub import HfApi
        card = HfApi().model_info(repo).card_data
        info["license"] = getattr(card, "license", None)
    except Exception as exc:  # noqa: BLE001 — offline is fine; the README in the snapshot has it too
        info["license_lookup_failed"] = type(exc).__name__
    (folder / "SOURCE.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


def cmd_adopt(args) -> int:
    src, dest = Path(args.src), ROOT / "huggingface" / args.repo.replace("/", "__")
    if dest.exists():
        print(f"{dest} already exists; nothing to do")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))                         # same disk: instant
    write_source(dest, args.repo)
    print(f"adopted {args.repo} -> {dest}")
    return 0


def cmd_hf(args) -> int:
    from huggingface_hub import snapshot_download
    dest = ROOT / "huggingface" / args.repo.replace("/", "__")
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=args.repo, local_dir=str(dest), ignore_patterns=args.ignore or None)
    write_source(dest, args.repo)
    print(f"downloaded {args.repo} -> {dest}")
    return 0


def cmd_finetune(args) -> int:
    dest = ROOT / "finetunes" / args.name
    copied = 0
    for src in map(Path, args.src):
        base = src if src.is_dir() else src.parent
        for rel, p in ([(f.relative_to(base).as_posix(), f) for f in base.rglob("*") if f.is_file()]
                       if src.is_dir() else [(src.name, src)]):
            target = dest / src.name / rel if src.is_dir() else dest / rel
            if target.exists() and target.stat().st_size == p.stat().st_size:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            copied += 1
    print(f"finetune {args.name}: copied {copied} files -> {dest}")
    return 0


# ── manifest + verify ─────────────────────────────────────────────────────────

def build_manifest(root: Path, previous: dict | None = None, workers: int = 2) -> dict:
    """Hash every file; a file whose size and mtime match the previous manifest isn't re-read."""
    old = (previous or {}).get("files", {})
    entries: dict[str, dict] = {}
    todo = []
    for rel, p in files_under(root):
        st = p.stat()
        prev = old.get(rel)
        if prev and prev["size"] == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns:
            entries[rel] = prev
        else:
            todo.append((rel, p, st))

    def hash_one(item):
        rel, p, st = item
        return rel, {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256_file(p)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (rel, entry) in enumerate(pool.map(hash_one, todo), 1):
            entries[rel] = entry
            print(f"  hashed {i}/{len(todo)}  {rel[:90]}", flush=True)
    return {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "root": str(root),
            "total_bytes": sum(e["size"] for e in entries.values()), "files": dict(sorted(entries.items())),
            "ollama_models": ollama_models(root / "ollama" / "models")}


def cmd_manifest(_args) -> int:
    path = ROOT / MANIFEST
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    (ROOT / "README.md").write_text(README, encoding="utf-8")
    manifest = build_manifest(ROOT, previous)
    path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (ROOT / (MANIFEST + ".sha256")).write_text(sha256_file(path) + "  " + MANIFEST + "\n", encoding="utf-8")
    bad = [n for n, i in manifest["ollama_models"].items() if i["missing"]]
    print(f"manifest: {len(manifest['files'])} files, {manifest['total_bytes'] / 1e9:.1f} GB; "
          f"{len(manifest['ollama_models'])} Ollama models" + (f"; INCOMPLETE: {bad}" if bad else ""))
    return 1 if bad else 0


def verify(root: Path, quick: bool = False) -> list[str]:
    """Problems found (empty = the archive matches its manifest)."""
    path = root / MANIFEST
    if not path.exists():
        return [f"no {MANIFEST}: run `manifest` first"]
    problems: list[str] = []
    sidecar = root / (MANIFEST + ".sha256")
    if sidecar.exists() and sidecar.read_text().split()[0] != sha256_file(path):
        problems.append(f"{MANIFEST} itself doesn't match {MANIFEST}.sha256")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    on_disk = dict(files_under(root))
    problems += [f"MISSING  {rel}" for rel in manifest["files"] if rel not in on_disk]
    problems += [f"EXTRA    {rel} (not in the manifest; run `manifest`)" for rel in on_disk if rel not in manifest["files"]]
    for rel, entry in manifest["files"].items():
        p = on_disk.get(rel)
        if p is None:
            continue
        if p.stat().st_size != entry["size"]:
            problems.append(f"SIZE     {rel}")
        elif not quick and sha256_file(p) != entry["sha256"]:
            problems.append(f"CORRUPT  {rel}")
        elif rel.startswith("ollama/models/blobs/sha256-") and entry["sha256"] != rel.rsplit("sha256-", 1)[1]:
            problems.append(f"CORRUPT  {rel} (bytes don't match the blob's own name)")
    problems += [f"INCOMPLETE Ollama model {n}: missing {i['missing']}"
                 for n, i in ollama_models(root / "ollama" / "models").items() if i["missing"]]
    return problems


def cmd_verify(args) -> int:
    problems = verify(ROOT, quick=args.quick)
    for p in problems:
        print(p)
    print("archive OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_inventory(_args) -> int:
    host = Path.home() / ".ollama" / "models"
    print(f"host Ollama       {host}: {sum(f.stat().st_size for f in host.rglob('*') if f.is_file()) / 1e9:.1f} GB" if host.exists() else "no host Ollama")
    for v in ollama_volumes():
        print(f"docker volume     {v}")
    print(f"archive root      {ROOT}: {'exists' if ROOT.exists() else 'not created yet'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory").set_defaults(fn=cmd_inventory)
    sub.add_parser("ollama").set_defaults(fn=cmd_ollama)
    p = sub.add_parser("adopt"); p.add_argument("src"); p.add_argument("repo"); p.set_defaults(fn=cmd_adopt)
    p = sub.add_parser("hf"); p.add_argument("repo"); p.add_argument("--ignore", action="append"); p.set_defaults(fn=cmd_hf)
    p = sub.add_parser("finetune"); p.add_argument("name"); p.add_argument("src", nargs="+"); p.set_defaults(fn=cmd_finetune)
    sub.add_parser("manifest").set_defaults(fn=cmd_manifest)
    p = sub.add_parser("verify"); p.add_argument("--quick", action="store_true"); p.set_defaults(fn=cmd_verify)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
