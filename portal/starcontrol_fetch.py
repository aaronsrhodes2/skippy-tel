"""Build time only: fetch the pinned Star Control Zero web build and unpack it into the image.

    python starcontrol_fetch.py <release tag> <sha256 of scz-web.tar.gz> <destination folder>

Run by portal/Dockerfile. The archive is a GitHub Release asset of star-control-precursors (made by that
repo's tools/build_web.py --tarball): one top-level folder, scz-web/, holding what portal/starcontrol.py
serves. It is about half a gigabyte, so it is streamed to disk, checked and unpacked in ONE build step and
deleted in the same step: the archive never becomes an image layer of its own.

The checksum is the point of pinning: a release asset can be replaced after the fact, a hash cannot. A
mismatch stops the build (nothing deploys) rather than shipping a game nobody chose.

An empty tag means "build the portal without the game": the page then says it isn't bundled.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ASSET_URL = "https://github.com/aaronsrhodes2/star-control-precursors/releases/download/{tag}/scz-web.tar.gz"
TOP = "scz-web"                      # the archive's one top-level folder
NEEDED = ("index.html", "game.html", "version.json")


class FetchError(Exception):
    pass


def fetch(url: str, sha256: str, dest: Path, opener=urllib.request.urlopen) -> int:
    """Download `url`, check it against `sha256`, unpack its scz-web/ folder as `dest`. Returns the file count."""
    dest = Path(dest)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "scz-web.tar.gz"
        digest = hashlib.sha256()
        with opener(url) as resp, open(archive, "wb") as out:
            while chunk := resp.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha256.strip().lower():
            raise FetchError(f"checksum mismatch for {url}: expected {sha256}, got {digest.hexdigest()}")
        unpacked = Path(tmp) / "unpacked"
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(unpacked, filter="data")      # "data": no absolute paths, no links out, no devices
        root = unpacked / TOP
        missing = [name for name in NEEDED if not (root / name).is_file()]
        if missing:
            raise FetchError(f"{url} is not a Star Control Zero web build: no {', '.join(missing)} under {TOP}/")
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(dest))
    return sum(1 for f in dest.rglob("*") if f.is_file())


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    tag, sha256, dest = argv[1], argv[2], argv[3]
    if not tag:
        print("starcontrol: no release pinned (STARCONTROL_REF is empty); building without the game")
        return 0
    try:
        count = fetch(ASSET_URL.format(tag=tag), sha256, Path(dest))
    except FetchError as exc:
        print(f"starcontrol: {exc}", file=sys.stderr)
        return 1
    print(f"starcontrol: {tag} unpacked to {dest} ({count} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
