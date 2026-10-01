"""The player's logic, run under Node (portal/static/bilby-player.js: the catalog, search, the queue, Bilby DJ, the autoplay state, the
virtual list's arithmetic), plus the contract with the REAL music gate.

Nothing here needs Node to be installed on a machine that runs the rest of the suite: without Node 18 or newer these tests are skipped
(CI's runners have it; a bare checkout may not). The parts that need a browser (the audio element, the lock screen, drag and drop, the
layout) are not run here at all: tests/dev_music.py runs the portal beside the real gate so they can be seen in a browser by hand."""
import gzip
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
JS_DIR = PORTAL / "tests" / "js"
CORE = PORTAL / "static" / "bilby-player.js"
GATE_DIR = PORTAL.parent / "services" / "music-gate"


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
def test_the_players_logic_passes_its_node_tests():
    files = sorted(str(p) for p in JS_DIR.glob("*.test.js"))
    assert files, "no *.test.js under portal/tests/js"
    run = subprocess.run([NODE, "--test", *files], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                         cwd=str(JS_DIR))
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
    ran = re.search(r"\btests\s+(\d+)", run.stdout)                # the summary line ("# tests 45" or "tests 45"): they really ran
    assert ran and int(ran.group(1)) >= 40 and re.search(r"\bfail\s+0\b", run.stdout), run.stdout[-1500:]


# ── the real gate's catalog and lyric lines, read by the player's own parser ─────────────────────────────────────────────

_PARSE = """
const fs = require("node:fs");
const C = require(process.argv[2]);
const lib = C.parseCatalog(JSON.parse(fs.readFileSync(process.argv[3], "utf8")));
const lines = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const matched = C.attachLyrics(lib, lines.by);
const pick = C.djPick(lib, 0, new Set([0]));
console.log(JSON.stringify({
  n: lib.T.length, hasYear: lib.hasYear, hasAdded: lib.hasAdded, matched, pick,
  tracks: lib.T.map(t => ({ p: t.p, t: t.t, a: t.a, al: t.al, g: t.g, y: t.y, m: t.m, n: t.n, d: t.d, art: t.art, ap: t.ap, ly: t.ly })),
  albums: lib.albums.map(a => [a.artist, a.name, a.year, a.added]),
  search: C.search(lib, "bjork").tracks.map(i => lib.T[i].t),
}));
"""


@needs_node
def test_the_player_reads_what_the_real_gate_serves(tmp_path):
    """services/music-gate's own Library builds the catalog from real tagged files; its Flask app serves catalog.json and lyrics.json
    behind a pass; this player's parser reads both. A field the gate renames, reorders or drops fails here, not in the Captain's car."""
    pytest.importorskip("mutagen")
    pytest.importorskip("PIL")
    pytest.importorskip("flask")
    sys.path[:0] = [str(GATE_DIR), str(GATE_DIR / "tests")]
    try:
        import gate
        from helpers import image_bytes, make_mp3
        from library import FIELDS, Library
        from lyrics import LyricsStore
    finally:
        del sys.path[:2]

    root = tmp_path / "library"
    help_dir, bjork_dir = root / "The Beatles" / "Help! (1965)", root / "Björk" / "Homogenic"
    for d in (help_dir, bjork_dir, root / "Loose"):
        d.mkdir(parents=True)
    cover = image_bytes((400, 400))
    make_mp3(help_dir / "01 Help!.mp3", cover=cover, TIT2="Help!", TPE1="The Beatles", TALB="Help!", TRCK="1/14", TPOS="1/1",
             TCON="(17)Rock", TDRC="1965-08-06")
    make_mp3(help_dir / "02 The Night Before.mp3", TIT2="The Night Before", TPE1="The Beatles", TALB="Help!", TRCK="2/14", TCON="Rock",
             TDRC="1965")
    make_mp3(bjork_dir / "01 Hunter.mp3", TIT2="Hunter", TPE1="Björk", TALB="Homogenic", TRCK="1", TCON="Electronic", TDRC="1997")
    make_mp3(root / "Loose" / "07 - Artist - Untitled Single.mp3")            # no tags at all: the title comes from the file's name

    library = Library(root, tmp_path / "state")
    assert library.scan() == 4
    by = {"The Beatles/Help! (1965)/01 Help!.mp3": "A man asks for help and means it.",
          "Björk/Homogenic/01 Hunter.mp3": "A hunter of her own heart.",
          "Gone/Away/Not Here.mp3": "x"}
    store = LyricsStore(None, url="http://lines.invalid/", fetch=lambda url, etag: (200, "e1", json.dumps({"v": 1, "by": by}).encode()))
    store.rebuild(library.identities)
    assert store.refresh()
    now = 1_800_000_000
    key = gate.gate_key("the-home-token")
    client = gate.create_app(library, key, clock=lambda: now, lyrics=store).test_client()
    token = gate.sign(key, now + 3600)

    cat = client.get(f"/t/{token}/catalog.json")
    lyr = client.get(f"/t/{token}/lyrics.json")
    assert cat.status_code == 200 and lyr.status_code == 200 and cat.headers["Content-Encoding"] == "gzip"
    catalog_json, lyrics_json = gzip.decompress(cat.data), gzip.decompress(lyr.data)
    assert json.loads(catalog_json)["fields"] == FIELDS
    (tmp_path / "catalog.json").write_bytes(catalog_json)
    (tmp_path / "lyrics.json").write_bytes(lyrics_json)
    (tmp_path / "parse.js").write_text(_PARSE, encoding="utf-8")

    run = subprocess.run([NODE, str(tmp_path / "parse.js"), str(CORE), str(tmp_path / "catalog.json"), str(tmp_path / "lyrics.json")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert run.returncode == 0, run.stderr[-2000:]
    got = json.loads(run.stdout)

    assert got["n"] == 4 and got["hasYear"] is True and got["hasAdded"] is True and got["matched"] == 2
    t = {x["p"]: x for x in got["tracks"]}
    help1 = t["The Beatles/Help! (1965)/01 Help!.mp3"]
    assert (help1["t"], help1["a"], help1["al"], help1["g"], help1["y"], help1["n"], help1["d"]) == ("Help!", "The Beatles", "Help!", "Rock", 1965, 1, 1)
    assert help1["art"] is True and help1["ly"] == "A man asks for help and means it."          # a lyric line, from the gate, on the track
    help2 = t["The Beatles/Help! (1965)/02 The Night Before.mp3"]
    assert help2["art"] is False and help2["ap"] == help1["p"] and help2["ly"] == ""             # no cover of its own: borrows the album's
    hunter = t["Björk/Homogenic/01 Hunter.mp3"]
    assert (hunter["y"], hunter["g"], hunter["ly"]) == (1997, "Electronic", "A hunter of her own heart.")
    loose = t["Loose/07 - Artist - Untitled Single.mp3"]
    assert loose["t"] == "Untitled Single" and loose["a"] == "Loose" and loose["y"] == 0         # the folder is the artist; no year known
    assert all(x["m"] > 1_600_000_000 for x in got["tracks"])                                    # "date added": the file's mtime
    assert ["The Beatles", "Help!", 1965] == got["albums"][[a[1] for a in got["albums"]].index("Help!")][:3]
    assert got["search"] == ["Hunter"]                                                           # "bjork" finds Björk
    assert got["pick"] in (1, 2, 3)                                                              # Bilby DJ: never the track it was given (0)
