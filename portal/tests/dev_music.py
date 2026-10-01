"""DEV ONLY: run Bilby's Audio Service on this machine, in a real browser, without skippy-pc, Tailscale or Google sign-in.

    python portal/tests/dev_music.py                  # a small generated library, portal on :8765, the real music-gate on :8766
    python portal/tests/dev_music.py --fake 21000     # ... plus 21,000 made-up tracks in the catalog (their audio is an empty file)

then open http://127.0.0.1:8765/dev-login (it signs the browser in as the Captain and lands on /music).

What is real and what stands in:
  * the portal is the real portal app, built the way the tests build it (FakeVm, a home that "reports" a music gate); the one
    dev-only change is Player.gate_url_re, so the gate may be http://127.0.0.1 instead of a *.ts.net address. Production never
    sets it, so a gate off the tailnet is still never signed for.
  * the gate IS services/music-gate: its own Flask app (gate.create_app) over its own Library, LyricsStore and ThumbCache, on a
    temp library and a temp state folder. Passes are the real ones (the same key from the same dev home token).
  * the only stand-in on the gate's side is the source of the lyric summaries, which production fetches from skippy-core's
    flask-api (GET /music/lyric-summaries): a few lines of Flask on :8767 answer the same shape.
  * the library is generated with ffmpeg (sine tones, tagged, some with cover art), so it needs ffmpeg and Pillow, and is kept in
    the temp folder between runs. --fake adds empty stub files whose tags come from their names (a reader handed to the real Library).

Dev-only switches, to see how the player behaves when the gate goes away (the real routes, not part of the app):
    /dev/gate-down   stop the gate's server: the player's "skippy-pc isn't reachable" state
    /dev/gate-up     start it again
    /dev/home-off    the home reports no music gate (the portal answers "its music gate isn't answering")
    /dev/home-on     and back

Not collected by pytest (no test_ prefix); nothing here ships.
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
import threading
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent), str(ROOT / "services" / "music-gate")]

TOKEN = "dev-token-not-a-secret"

# (artist, album, year, genre, discs, [titles])
LIBRARY = [
    ("The Beatles", "Help!", 1965, "Rock", 1, ["Help!", "The Night Before", "You've Got to Hide Your Love Away", "I Need You", "Another Girl"]),
    ("The Beatles", "Abbey Road", 1969, "(17)Rock", 1, ["Come Together", "Something", "Maxwell's Silver Hammer", "Octopus's Garden", "Here Comes the Sun"]),
    ("Björk", "Homogenic", 1997, "Electronic", 1, ["Hunter", "Jóga", "Unravel", "Bachelorette"]),
    ("Sigur Rós", "( )", 2002, "Post-Rock", 1, ["Untitled 1", "Untitled 2", "Untitled 3"]),
    ("AC-DC", "Back in Black", 1980, "Hard Rock", 1, ["Hells Bells", "Shoot to Thrill", "Back in Black", "You Shook Me All Night Long"]),
    ("Beyoncé", "Lemonade", 2016, "Pop", 1, ["Pray You Catch Me", "Hold Up", "Sorry", "Formation"]),
    ("Miles Davis", "Kind of Blue", 1959, "Jazz", 1, ["So What", "Freddie Freeloader", "Blue in Green", "All Blues"]),
    ("Pink Floyd", "The Wall", 1979, "Rock", 2, ["In the Flesh?", "Another Brick in the Wall", "Comfortably Numb", "Run Like Hell"]),
    ("Daft Punk", "Discovery", 2001, "Electronic", 1, ["One More Time", "Aerodynamic", "Digital Love", "Harder, Better, Faster, Stronger"]),
    ("Nina Simone", "I Put a Spell on You", 1965, "Jazz", 1, ["I Put a Spell on You", "Feeling Good", "Ain't Got No"]),
    ("Fleetwood Mac", "Rumours", 1977, "Rock", 1, ["Second Hand News", "Dreams", "Go Your Own Way", "The Chain"]),
    ("Fleetwood Mac", "Tusk", 1979, "Rock", 1, ["Over & Over", "The Ledge", "Tusk"]),
    ("Kendrick Lamar", "good kid, m.A.A.d city", 2012, "Hip-Hop", 1, ["Sherane a.k.a Master Splinter's Daughter", "Bitch, Don't Kill My Vibe", "Swimming Pools"]),
    ("Ólafur Arnalds", "re member", 2018, "Classical", 1, ["Saman", "Unfold", "Undone"]),
]
SCALE = [262, 294, 330, 392, 440, 523, 587, 659]
LENGTHS = [18, 26, 34, 45, 22, 38]
LYRIC = ["A song about leaving home and finding it again somewhere else.", "Love, loss and a long drive at night.",
         "An instrumental that sounds like weather.", "A confession set to a driving beat.", "Nostalgia for a summer that never quite happened."]
FAKE_WORDS = ["love", "night", "road", "fire", "river", "dream", "blue", "stone", "light", "dark", "heart", "wild", "gold", "rain", "glass",
              "paper", "silver", "winter"]
STUBS = ".dev-stubs"                       # names the stub files this script made, so they can be removed again


def need(tool: str) -> str:
    found = shutil.which(tool)
    if not found:
        sys.exit(f"dev_music: {tool} is needed to generate the dev library and was not found on PATH")
    return found


def cover(path: Path, label: str, seed: int) -> None:
    from PIL import Image, ImageDraw, ImageFont
    import colorsys
    r, g, b = (int(255 * c) for c in colorsys.hsv_to_rgb((seed * 0.137) % 1, 0.55, 0.85))
    im = Image.new("RGB", (600, 600), (r, g, b))
    d = ImageDraw.Draw(im)
    for k in range(6):
        inset = 40 + k * 40
        d.ellipse([inset, inset, 600 - inset, 600 - inset], outline=(255 - r, 255 - g, 255 - b), width=6)
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default()
    d.text((300, 300), label[:2].upper(), fill=(255, 255, 255), anchor="mm", font=font)
    im.save(path, "JPEG", quality=85)


def build_library(root: Path) -> None:
    ffmpeg = need("ffmpeg")
    root.mkdir(parents=True, exist_ok=True)
    n = 0
    for ai, (artist, album, year, genre, discs, titles) in enumerate(LIBRARY):
        folder = root / artist / album
        folder.mkdir(parents=True, exist_ok=True)
        art = folder / "_cover.jpg"
        has_art = ai % 3 != 2                                   # every third album has none
        if has_art:
            cover(art, album, ai)
        for ti, title in enumerate(titles):
            n += 1
            ext = ".m4a" if (ai + ti) % 5 == 4 else ".mp3"
            out = folder / f"{ti + 1:02d} - {title.replace('?', '').replace('/', '-')}{ext}"
            if out.exists():
                continue
            disc = 1 if discs == 1 or ti < len(titles) // 2 else 2
            dur = LENGTHS[n % len(LENGTHS)]
            cmd = [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={SCALE[n % len(SCALE)]}:duration={dur}"]
            if has_art:
                cmd += ["-i", str(art)]
            cmd += ["-map", "0:a"] + (["-map", "1:v"] if has_art else [])
            if ext == ".mp3":
                cmd += ["-c:a", "libmp3lame", "-b:a", "48k", "-ar", "44100", "-ac", "1"] + (["-c:v", "mjpeg", "-id3v2_version", "3"] if has_art else [])
            else:
                cmd += ["-c:a", "aac", "-b:a", "48k", "-ar", "44100", "-ac", "1"] + (["-c:v", "copy", "-disposition:v", "attached_pic"] if has_art else [])
            tags = {"title": title, "artist": artist, "album": album, "track": f"{ti + 1}/{len(titles)}", "date": str(year),
                    "genre": genre, "disc": f"{disc}/{discs}"}
            for k, v in tags.items():
                cmd += ["-metadata", f"{k}={v}"]
            cmd.append(str(out))
            subprocess.run(cmd, check=True)
        if art.exists():
            art.unlink()
    print(f"dev_music: generated {n} tracks in {root}")


def make_stubs(root: Path, count: int) -> None:
    """`count` empty .mp3 stubs (their tags are made up from the name by stub_reader), after removing the last run's."""
    listing = root / STUBS
    if listing.exists():
        for rel in listing.read_text(encoding="utf-8").splitlines():
            try:
                (root / rel).unlink()
            except OSError:
                pass
        listing.unlink()
    if not count:
        return
    import random
    rnd = random.Random(7)
    made = []
    for i in range(count):
        artist = f"{rnd.choice(FAKE_WORDS).title()} {rnd.choice(FAKE_WORDS).title()}s {rnd.randrange(1, 600)}"
        album = f"{rnd.choice(FAKE_WORDS).title()} {rnd.choice(FAKE_WORDS).title()} ({1970 + rnd.randrange(55)})"
        rel = f"{artist}/{album}/{i % 14 + 1:02d} - {rnd.choice(FAKE_WORDS).title()} {rnd.choice(FAKE_WORDS)} {i}.mp3"
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"")
        made.append(rel)
    listing.write_text("\n".join(made), encoding="utf-8")
    print(f"dev_music: {count} empty stub tracks added (audio for those fails on purpose; --fake 0 removes them)")


def stub_reader(real_read):
    """The Library's tag reader: the real one, except for an empty stub file, whose tags are invented from its name."""
    def read(path: Path):
        if path.stat().st_size:
            return real_read(path)
        seed = zlib.crc32(path.as_posix().encode())
        genres = ["Rock", "Pop", "Jazz", "Electronic", "Folk", "", "(13)"]
        m = re.match(r"(\d+) - (.+)\.mp3$", path.name)
        year = re.search(r"\((\d{4})\)\s*$", path.parent.name)
        return {"t": m.group(2) if m else path.stem, "a": "", "al": "", "n": int(m.group(1)) if m else 0, "d": 1,
                "g": genres[seed % len(genres)], "art": False, "y": int(year.group(1)) if year else 0, "s": 120 + seed % 300}
    return read


def lyric_source(root: Path):
    """What flask-api's GET /music/lyric-summaries answers (library-relative paths, a line for most files)."""
    from flask import Flask, jsonify
    from library import AUDIO
    rels = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.suffix.lower() in AUDIO)
    by = {rel: LYRIC[i % len(LYRIC)] for i, rel in enumerate(rels) if i % 10 < 7}
    app = Flask("dev-lyric-source")

    @app.get("/music/lyric-summaries")
    def lines():
        return jsonify(v=1, by=by, songs={})

    return app


def make_gate(root: Path, state: Path, lyrics_url: str):
    """The real music-gate over `root`: the same Flask app production runs, built the way gate.from_env builds it (minus the
    background threads: the scan and the first lyric fetch run here, once)."""
    import gate as real_gate
    from art import ThumbCache
    from library import Library, read_tags
    from lyrics import LyricsStore

    library = Library(root, state, reader=stub_reader(read_tags))
    t0 = time.time()
    library.scan()
    print(f"dev_music: the gate read {library.count} tracks in {time.time() - t0:.1f} s")
    lyrics = LyricsStore(state, lyrics_url)
    lyrics.rebuild(library.identities)
    library.on_build.append(lyrics.rebuild)
    lyrics.refresh()
    thumbs = ThumbCache(state / "art-cache")
    return real_gate.create_app(library, real_gate.gate_key(TOKEN), lyrics=lyrics, thumbs=thumbs)


class Server:
    """A werkzeug server in a thread that can be stopped and started again on the same port (the gate-down switch)."""
    def __init__(self, app, port):
        self.app, self.port, self.srv, self.thread = app, port, None, None

    def start(self):
        from werkzeug.serving import make_server
        if self.srv:
            return
        self.srv = make_server("127.0.0.1", self.port, self.app, threaded=True)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None


def make_portal(gate_port: int):
    import app as portal
    import home as home_mod
    from flask import redirect, session
    from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS

    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}))
    home = home_mod.Home(vm)
    gate_url = f"http://127.0.0.1:{gate_port}"
    caps = {"music_gate": {"ready": True, "url": gate_url}}
    on = {"value": True}

    def report():
        while True:
            home.accept({"capabilities": caps if on["value"] else {}})
            time.sleep(5)

    threading.Thread(target=report, daemon=True).start()
    time.sleep(0.2)
    settings = dict(SETTINGS)
    settings["TELEMETRY_ENABLED"] = False
    app = portal.create_app(settings, vm=vm, skippy=FakeSkippy(), home=home)
    app.extensions["player"].gate_url_re = re.compile(r"http://127\.0\.0\.1:\d+")

    @app.get("/dev-login")
    def dev_login():
        session["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
        return redirect("/music")

    @app.get("/dev/home-off")
    def home_off():
        on["value"] = False
        home.accept({"capabilities": {}})
        return "home reports no music gate"

    @app.get("/dev/home-on")
    def home_on():
        on["value"] = True
        home.accept({"capabilities": caps})
        return "home reports the music gate"

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lib", default=str(Path(tempfile.gettempdir()) / "bilby-dev-library"))
    ap.add_argument("--fake", type=int, default=0, help="extra empty stub tracks in the library (made-up tags; their audio fails)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--gate-port", type=int, default=8766)
    ap.add_argument("--lyrics-port", type=int, default=8767)
    args = ap.parse_args()
    root = Path(args.lib)
    if not root.is_dir() or not any(root.rglob("*.mp3")):
        build_library(root)
    make_stubs(root, args.fake)
    state = Path(tempfile.gettempdir()) / "bilby-dev-state"
    shutil.rmtree(state, ignore_errors=True)                   # a clean tag cache each run: the library may have changed under it
    state.mkdir(parents=True, exist_ok=True)

    lyrics_srv = Server(lyric_source(root), args.lyrics_port)
    lyrics_srv.start()
    gate_srv = Server(make_gate(root, state, f"http://127.0.0.1:{args.lyrics_port}/music/lyric-summaries"), args.gate_port)
    gate_srv.start()
    portal_app = make_portal(args.gate_port)

    @portal_app.get("/dev/gate-down")
    def gate_down():
        gate_srv.stop()
        return "the gate is stopped"

    @portal_app.get("/dev/gate-up")
    def gate_up():
        gate_srv.start()
        return "the gate is running"

    print(f"dev_music: the real music-gate on :{args.gate_port}, the portal on :{args.port}; open http://127.0.0.1:{args.port}/dev-login")
    Server(portal_app, args.port).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
