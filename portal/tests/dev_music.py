"""DEV ONLY: run Bilby's Music Service on this machine, in a real browser, without skippy-pc, Tailscale or Google sign-in.

    python portal/tests/dev_music.py                  # a small generated library, portal on :8765, a stand-in gate on :8766
    python portal/tests/dev_music.py --fake 21000     # ... plus 21,000 made-up tracks in the catalog (their audio is a 404)
    python portal/tests/dev_music.py --etag           # the stand-in gate also answers a page's conditional requests

then open http://127.0.0.1:8765/dev-login (it signs the browser in as the Captain and lands on /music).

What it stands in for, and what it is not:
  * the portal is the real portal app, built the way the tests build it (FakeVm, a home that "reports" a music gate);
    the one dev-only change is Player.gate_url_re, so the gate may be http://127.0.0.1 instead of a *.ts.net address.
    Production never sets it, so a gate off the tailnet is still never signed for.
  * the gate is NOT services/music-gate: it is a small stand-in that implements the contract the player is built to
    (catalog fields y and m, lyrics.json, art ?w=). It does check the real passes with the real gate.valid(), so a bad,
    expired or missing pass is a 403 here too.
  * the library is generated with ffmpeg (sine tones, tagged, some with cover art), so it needs ffmpeg and Pillow, and is
    kept in the temp folder between runs.
Not collected by pytest (no test_ prefix); nothing here ships.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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


def first_year(value) -> int:
    m = re.match(r"\s*(\d{4})", str(value or ""))
    return int(m.group(1)) if m else 0


def make_catalog(root: Path, fake: int) -> tuple[list[str], list[list]]:
    import mutagen
    from library import AUDIO, read_tags
    fields = ["p", "t", "a", "al", "n", "d", "g", "s", "art", "y", "m"]
    rows = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in AUDIO or path.name.startswith("."):
            continue
        tags = read_tags(path)
        if not tags:
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        f = mutagen.File(path)
        raw = f.tags.get("\xa9day") if path.suffix.lower() == ".m4a" else (f.tags.get("TDRC") if f.tags else None)
        year = first_year(raw[0] if isinstance(raw, list) else raw)
        rows.append([rel, tags["t"] or path.stem, parts[0] if len(parts) > 1 else tags["a"], parts[1] if len(parts) > 2 else tags["al"],
                     tags["n"], tags["d"], tags["g"], tags["s"], 1 if tags["art"] else 0, year, int(path.stat().st_mtime)])
    import random
    rnd = random.Random(7)
    words = ["love", "night", "road", "fire", "river", "dream", "blue", "stone", "light", "dark", "heart", "wild", "gold", "rain", "glass", "paper", "silver", "winter"]
    now = int(time.time())
    for i in range(fake):
        artist = f"{rnd.choice(words).title()} {rnd.choice(words).title()}s {rnd.randrange(1, 600)}"
        album = f"{rnd.choice(words).title()} {rnd.choice(words).title()}"
        title = f"{rnd.choice(words).title()} {rnd.choice(words)} {i}"
        rows.append([f"{artist}/{album}/{i % 14 + 1:02d} - {title}.mp3", title, artist, album, i % 14 + 1, 1, rnd.choice(["Rock", "Pop", "Jazz", "Electronic", "Folk", "", "(13)"]),
                     rnd.randrange(120, 420), 1 if i % 4 == 0 else 0, 1970 + rnd.randrange(55), now - rnd.randrange(0, 86400 * 900)])
    return fields, rows


def make_gate(root: Path, fake: int, etag: bool):
    from flask import Flask, Response, abort, jsonify, request, send_file
    import gate as real_gate
    from library import AUDIO, read_art

    key = real_gate.gate_key(TOKEN)
    app = Flask("dev-gate")
    fields, rows = make_catalog(root, fake)
    body = gzip.compress(json.dumps({"v": 1, "fields": fields, "tracks": rows}, separators=(",", ":")).encode(), 6, mtime=0)
    tag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
    lyr = {r[0]: LYRIC[i % len(LYRIC)] for i, r in enumerate(rows) if i % 10 < 7}
    lyr_gz = gzip.compress(json.dumps({"v": 1, "by": lyr}, separators=(",", ":")).encode(), 6, mtime=0)
    thumbs: dict = {}

    @app.after_request
    def cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        expose = "Content-Length, Content-Range, Accept-Ranges" + (", ETag" if etag else "")
        resp.headers["Access-Control-Expose-Headers"] = expose
        if etag:
            resp.headers["Access-Control-Allow-Headers"] = "If-None-Match"
        return resp

    def check(token):
        if not real_gate.valid(key, token, time.time()):
            abort(403)

    @app.get("/health")
    def health():
        return jsonify(ok=True, ready=True, tracks=len(rows))

    @app.get("/t/<token>/catalog.json")
    def catalog(token):
        check(token)
        if etag and request.headers.get("If-None-Match") == tag:
            return Response(status=304, headers={"ETag": tag})
        return Response(body, mimetype="application/json", headers={"Content-Encoding": "gzip", "ETag": tag, "Cache-Control": "private, no-cache"})

    @app.get("/t/<token>/lyrics.json")
    def lyrics(token):
        check(token)
        return Response(lyr_gz, mimetype="application/json", headers={"Content-Encoding": "gzip", "Cache-Control": "private, no-cache"})

    def resolve(rel):
        full = (root / rel).resolve()
        try:
            full.relative_to(root.resolve())
        except ValueError:
            abort(404)
        if full.suffix.lower() not in AUDIO or not full.is_file():
            abort(404)
        return full

    @app.get("/t/<token>/a/<path:rel>")
    def audio(token, rel):
        check(token)
        full = resolve(rel)
        return send_file(full, mimetype=AUDIO[full.suffix.lower()], conditional=True, max_age=86400)

    @app.get("/t/<token>/art/<path:rel>")
    def art(token, rel):
        check(token)
        found = read_art(resolve(rel))
        if not found:
            abort(404)
        data, mime = found
        w = request.args.get("w", type=int)
        if w:
            w = min((x for x in (96, 192, 384, 768)), key=lambda x: abs(x - w))
            from PIL import Image
            k = (rel, w)
            if k not in thumbs:
                im = Image.open(io.BytesIO(data)).convert("RGB")
                im.thumbnail((w, w))
                out = io.BytesIO()
                im.save(out, "JPEG", quality=80)
                thumbs[k] = out.getvalue()
            data, mime = thumbs[k], "image/jpeg"
        return Response(data, mimetype=mime, headers={"Cache-Control": "private, max-age=604800"})

    return app


def make_portal(gate_port: int):
    import hashlib as _h
    import app as portal
    import home as home_mod
    import player as player_mod
    from flask import redirect, session
    from test_app import ADMIN, FakeSkippy, FakeVm, SETTINGS

    vm = FakeVm()
    vm.set_metadata(home_mod.KEY, json.dumps({"token_sha256": _h.sha256(TOKEN.encode()).hexdigest()}))
    home = home_mod.Home(vm)
    gate_url = f"http://127.0.0.1:{gate_port}"

    def report():
        while True:
            home.accept({"capabilities": {"music_gate": {"ready": True, "url": gate_url}}})
            time.sleep(10)

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

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lib", default=str(Path(tempfile.gettempdir()) / "bilby-dev-library"))
    ap.add_argument("--fake", type=int, default=0, help="extra made-up tracks in the catalog (their audio is a 404)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--gate-port", type=int, default=8766)
    ap.add_argument("--etag", action="store_true", help="the stand-in gate answers conditional requests from a page")
    args = ap.parse_args()
    root = Path(args.lib)
    if not root.is_dir() or not any(root.rglob("*.mp3")):
        build_library(root)
    from werkzeug.serving import make_server
    gate_app = make_gate(root, args.fake, args.etag)
    threading.Thread(target=make_server("127.0.0.1", args.gate_port, gate_app, threaded=True).serve_forever, daemon=True).start()
    portal_app = make_portal(args.gate_port)
    print(f"dev_music: gate on :{args.gate_port}, portal on :{args.port}; open http://127.0.0.1:{args.port}/dev-login")
    make_server("127.0.0.1", args.port, portal_app, threaded=True).serve_forever()


if __name__ == "__main__":
    main()
