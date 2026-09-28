"""python -m memory — Skippy Memory command line.

  python -m memory init    --out "G:/My Drive/Skippy Memory"
  python -m memory ingest  <export.zip|folder|conversations.json> --out <dir> [--source claude] [--distill]
  python -m memory inbox   --out <dir> [--distill]     ingest every export in <dir>/inbox/
  python -m memory distill --out <dir> [--redo]        tag untagged conversations with the local LLM
  python -m memory reindex --out <dir>                 rebuild index.md and topics/
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from memory import distill as distill_mod
from memory import index, parsers
from memory.store import Archive

DEFAULT_OUT = os.environ.get("SKIPPY_MEMORY_DIR")


def _archive(args) -> Archive:
    if not args.out:
        sys.exit("error: --out is required (or set SKIPPY_MEMORY_DIR)")
    archive = Archive(Path(args.out))
    archive.init()
    return archive


def _ingest_one(archive: Archive, path: Path, source: str | None) -> None:
    result = archive.ingest(parsers.load(path, source))
    print(f"{path.name}: {result.new} new, {result.updated} updated, {result.skipped} unchanged")


def _finish(archive: Archive, args) -> None:
    if getattr(args, "distill", False):
        n = distill_mod.distill(archive, url=args.ollama_url, model=args.model)
        print(f"distilled {n} conversations")
    index.rebuild(archive)
    print(f"index rebuilt: {archive.root / 'index.md'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m memory", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--out", default=DEFAULT_OUT, help="archive root (Drive folder)")
        return p

    def add_llm(p):
        p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", distill_mod.DEFAULT_URL))
        p.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", distill_mod.DEFAULT_MODEL))

    add("init", "create the folder layout and README")
    p = add("ingest", "import one export")
    p.add_argument("export", type=Path)
    p.add_argument("--source", choices=sorted(parsers.PARSERS))
    p.add_argument("--distill", action="store_true", help="tag new conversations via Ollama")
    add_llm(p)
    p = add("inbox", "import every export dropped in <out>/inbox/")
    p.add_argument("--distill", action="store_true")
    add_llm(p)
    p = add("distill", "tag conversations via Ollama")
    p.add_argument("--redo", action="store_true", help="re-tag already tagged conversations")
    add_llm(p)
    add("reindex", "rebuild index.md and topics/")

    args = parser.parse_args(argv)
    archive = _archive(args)

    if args.cmd == "init":
        print(f"archive ready: {archive.root}")
    elif args.cmd == "ingest":
        _ingest_one(archive, args.export, args.source)
        _finish(archive, args)
    elif args.cmd == "inbox":
        exports = sorted(p for p in (archive.root / "inbox").iterdir()
                         if p.suffix in (".zip", ".json") or p.is_dir())
        if not exports:
            print("inbox is empty")
        for path in exports:
            try:
                _ingest_one(archive, path, None)
            except ValueError as exc:
                print(f"{path.name}: skipped ({exc})")
        _finish(archive, args)
    elif args.cmd == "distill":
        n = distill_mod.distill(archive, url=args.ollama_url, model=args.model, redo=args.redo)
        print(f"distilled {n} conversations")
        index.rebuild(archive)
    elif args.cmd == "reindex":
        index.rebuild(archive)
        print(f"index rebuilt: {archive.root / 'index.md'}")
    return 0
