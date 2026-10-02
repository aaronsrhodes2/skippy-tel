"""Nagatha's Video Service: the player's logic under Node (portal/static/nagatha-core.js, tests in portal/tests/js/video/), plus the
contract with the REAL video gate: services/video-gate builds its catalog from its fake Jellyfin, serves it behind a pass, and this
player's own parser reads it. A field the gate renames or drops fails here, not on the Captain's TV.

Without Node 18+ these are skipped (CI's runners have it). The gate runs in its own Python process: it has a catalog.py of its own,
and the portal's is already imported in this one."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
JS_DIR = PORTAL / "tests" / "js" / "video"
CORE = PORTAL / "static" / "nagatha-core.js"
VIDEO_GATE = PORTAL.parent / "services" / "video-gate"


def _node() -> str | None:
    exe = shutil.which("node")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        return exe if int(out.lstrip("v").split(".")[0]) >= 18 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


NODE = _node()
needs_node = pytest.mark.skipif(NODE is None, reason="Node 18+ isn't installed here: the player's logic tests run wherever it is")


@needs_node
def test_the_video_players_logic_passes_its_node_tests():
    files = sorted(str(p) for p in JS_DIR.glob("*.test.js"))
    assert files, "no *.test.js under portal/tests/js/video"
    run = subprocess.run([NODE, "--test", *files], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                         cwd=str(JS_DIR))
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
    ran = re.search(r"\btests\s+(\d+)", run.stdout)
    assert ran and int(ran.group(1)) >= 15 and re.search(r"\bfail\s+0\b", run.stdout), run.stdout[-1500:]


# The gate's side, run in services/video-gate with its test fake: the catalog exactly as a browser downloads it.
_GATE = r"""
import gzip, json, sys
sys.path[:0] = [".", "tests"]
import fake_jellyfin as fj, gate
from catalog import Library
from images import ImageCache
from jellyfin import Client
from helpers import KEY, NOW
fake = fj.FakeJellyfin()
fake.url = fake.start()
try:
    client = Client(fake.url, fj.KEY)
    lib = Library(client, "", clock=lambda: NOW)
    assert lib.refresh()
    app = gate.create_app(lib, client, KEY, clock=lambda: NOW, images=ImageCache(sys.argv[1]))
    r = app.test_client().get(f"/t/{gate.sign(KEY, NOW + 3600)}/catalog.json")
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip"
    sys.stdout.write(gzip.decompress(r.data).decode())
finally:
    fake.stop()
"""

_PARSE = """
const fs = require("node:fs");
const C = require(process.argv[2]);
const L = C.parseCatalog(JSON.parse(fs.readFileSync(process.argv[3], "utf8")));
const env = { native: false, mse: true, webkit: false };
console.log(JSON.stringify({
  n: L.items.length, movies: L.movies.length,
  items: L.items.map(i => ({ title: i.title, type: i.type, year: i.year, runtime: i.runtime, resume: C.resumePoint(i), played: i.played,
                            poster: i.hasPoster, mode: C.chooseSource(i, { env }).mode, subs: i.play ? i.play.subs.filter(s => s.vtt).map(s => s.i) : [],
                            audio: i.play ? i.play.audio.map(a => a.i) : [], colls: i.collectionIds.length, genres: i.genres, rating: i.rating })),
  collections: L.collections.map(c => ({ title: c.title, n: c.itemIds.length })),
  cont: C.continueWatching(L, {}).map(i => i.title),
  added: C.recentlyAdded(L, 3).length,
  search: C.search(L, "alpha").items.map(i => i.title),
}));
"""


@needs_node
def test_the_player_reads_what_the_real_gate_serves(tmp_path):
    pytest.importorskip("flask")
    pytest.importorskip("requests")
    gate_run = subprocess.run([sys.executable, "-c", _GATE, str(tmp_path / "img")], cwd=str(VIDEO_GATE), capture_output=True,
                              text=True, encoding="utf-8", timeout=120)
    assert gate_run.returncode == 0, gate_run.stderr[-3000:]
    (tmp_path / "catalog.json").write_text(gate_run.stdout, encoding="utf-8")
    (tmp_path / "parse.js").write_text(_PARSE, encoding="utf-8")
    run = subprocess.run([NODE, str(tmp_path / "parse.js"), str(CORE), str(tmp_path / "catalog.json")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert run.returncode == 0, run.stderr[-2000:]
    got = json.loads(run.stdout)

    by = {i["title"]: i for i in got["items"]}
    assert got["n"] == len(json.loads(gate_run.stdout)["items"]) and got["movies"] == 9          # every row kept; the series and episode aren't movies
    alpha = by["Alpha"]
    assert (alpha["year"], alpha["runtime"], alpha["resume"], alpha["mode"]) == (1994, 5400, 1200, "direct")
    assert alpha["genres"] == ["Horror", "Thriller"] and alpha["rating"] == 7.5 and alpha["colls"] == 1
    assert by["Bravo"]["mode"] == "hls" and by["Bravo"]["subs"] == [3] and by["Bravo"]["audio"] == [1, 2]   # the PGS track isn't offered
    assert by["Charlie"]["mode"] == "hls" and by["Delta"]["mode"] == "hls" and by["Echo"]["mode"] == "hls"
    assert by["Golf"]["mode"] == "direct" and by["Golf"]["poster"] is False
    assert by["Hotel"]["played"] is True and by["Juliet Show"]["mode"] == "none"                  # a series is a shelf
    # by sort name; the member the catalog doesn't have (the fake's OTHER_ID) is left out
    assert got["collections"] == [{"title": "Empty Box", "n": 0}, {"title": "The Alpha Collection", "n": 2}]
    assert got["cont"] == ["Alpha"] and got["added"] == 3 and got["search"][0] == "Alpha"
