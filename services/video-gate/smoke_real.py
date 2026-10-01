"""READ-ONLY smoke of the video-gate against a real Jellyfin (not part of the image, not run by CI).

    JELLYFIN_API_KEY=... JELLYFIN_URL=http://127.0.0.1:8096 python services/video-gate/smoke_real.py

It runs the gate in-process (Flask's test client, a throwaway pass key) and checks, against the real server: the catalog
builds, a poster, the first KB of a direct stream, a subtitle, and one short HLS probe (master, media playlist, the first
segment: then the transcode is stopped). It never writes watch state (the progress route is not called), never changes a
Jellyfin setting and never prints the key. The only side effect on Jellyfin is that one short transcode.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gate                                  # noqa: E402
import hls                                   # noqa: E402
from catalog import Library                  # noqa: E402
from images import ImageCache                # noqa: E402
from jellyfin import Client                  # noqa: E402


def main() -> int:
    key = os.environ.get("JELLYFIN_API_KEY", "").strip()
    if not key:
        print("JELLYFIN_API_KEY is not set: nothing to smoke against")
        return 2
    client = Client(os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096"), key)
    library = Library(client)
    t0 = time.time()
    assert library.refresh(), f"catalog did not build ({library.error})"
    print(f"catalog: {library.count} items built in {time.time() - t0:.1f}s")

    pkey = gate.gate_key("smoke-throwaway-token")
    sessions = hls.Sessions(client.stop_encoding)
    with tempfile.TemporaryDirectory() as tmp:
        app = gate.create_app(library, client, pkey, sessions=sessions, images=ImageCache(tmp), forbidden=(key,))
        web = app.test_client()
        tok = gate.sign(pkey, int(time.time()) + 3600)
        base = f"/t/{tok}"

        r = web.get(f"{base}/catalog.json")
        data = json.loads(gzip.decompress(r.data))
        items = data["items"]
        kinds = {}
        for i in items:
            kinds[i["type"]] = kinds.get(i["type"], 0) + 1
        direct = [i for i in items if i["play"] and i["play"]["direct"]]
        other = [i for i in items if i["play"] and not i["play"]["direct"]]
        print(f"GET catalog.json: {r.status_code}, {len(r.data)} bytes gzipped, {len(gzip.decompress(r.data))} bytes json, "
              f"etag {bool(r.headers.get('ETag'))}, 304 on repeat: "
              f"{web.get(f'{base}/catalog.json', headers={'If-None-Match': r.headers['ETag']}).status_code}")
        print(f"types {kinds}; collections {len(data['collections'])}; direct {len(direct)}; needs hls {len(other)}; "
              f"posters {sum(i['hasPoster'] for i in items)}; backdrops {sum(i['hasBackdrop'] for i in items)}; "
              f"with subtitles {sum(1 for i in items if i['play'] and i['play']['subs'])}; resumable {sum(1 for i in items if i['resume'])}; "
              f"played {sum(i['played'] for i in items)}")
        why = {}
        for i in other:
            p = i["play"]
            tag = f"{p['container'] or '?'}/{p['vcodec'] or '?'}/{p['acodec'] or 'none'}"
            why[tag] = why.get(tag, 0) + 1
        print("not direct, by container/video/first-audio:", dict(sorted(why.items(), key=lambda kv: -kv[1])))

        poster = next(i for i in items if i["hasPoster"])
        r = web.get(f"{base}/img/{poster['id']}/poster?w=300")
        print(f"poster (w=300 -> nearest 320): {r.status_code} {r.mimetype} {len(r.data)} bytes, cache-control {r.headers.get('Cache-Control')}")
        coll = next((c for c in data["collections"] if c["hasPoster"]), None)
        if coll:
            r = web.get(f"{base}/img/{coll['id']}/poster?w=160")
            print(f"collection poster: {r.status_code} {r.mimetype} {len(r.data)} bytes")

        d = direct[0]
        r = web.get(f"{base}/v/{d['id']}/direct", headers={"Range": "bytes=0-1023"})
        print(f"direct stream first KB: {r.status_code} {len(r.data)} bytes, {r.headers.get('Content-Range')}, {r.mimetype}, "
              f"ftyp box: {r.data[4:8] == b'ftyp'}")
        r = web.get(f"{base}/v/{d['id']}/direct", headers={"Range": "bytes=999999999999-"})
        print(f"direct stream beyond end: {r.status_code}")
        r = web.get(f"{base}/v/{other[0]['id']}/direct")
        print(f"direct on a non-direct item: {r.status_code} {r.get_json()}")

        withsub = next((i for i in items if i["play"] and any(s["vtt"] for s in i["play"]["subs"])), None)
        if withsub:
            idx = next(s["i"] for s in withsub["play"]["subs"] if s["vtt"])
            r = web.get(f"{base}/sub/{withsub['id']}/{idx}.vtt")
            print(f"subtitle {withsub['title']!r} #{idx}: {r.status_code} {r.content_type} {len(r.data)} bytes, starts {r.data[:12]!r}")
            r = web.get(f"{base}/sub/{withsub['id']}/99.vtt")
            print(f"subtitle bad index: {r.status_code}")

        probe = withsub if withsub and not withsub["play"]["direct"] else other[0]
        print(f"HLS probe on {probe['title']!r} ({probe['play']['container']}/{probe['play']['vcodec']}/{probe['play']['acodec']}), "
              f"transcodes active before: {sessions.active_count()}")
        try:
            r = web.get(f"{base}/v/{probe['id']}/hls/master.m3u8?h=720&start=60")
            master = r.get_data(as_text=True)
            print(f"master: {r.status_code} {r.mimetype}, {len(master)} bytes; "
                  f"leaks: key={key in master} apikey={bool(re.search('api[_-]?key', master, re.I))} http={'http' in master}")
            print("\n".join("   " + ln for ln in master.splitlines()))
            variant = next(ln for ln in master.splitlines() if ln and not ln.startswith("#"))
            r = web.get(f"{base}/v/{probe['id']}/hls/{variant}")
            media = r.get_data(as_text=True)
            segs = [ln for ln in media.splitlines() if ln and not ln.startswith("#")]
            print(f"media playlist: {r.status_code} {len(media)} bytes, {len(segs)} segments, first {segs[0]}, "
                  f"leaks: key={key in media} apikey={bool(re.search('api[_-]?key', media, re.I))}, "
                  f"has EXT-X-START: {'#EXT-X-START' in media}")
            r = web.get(f"{base}/v/{probe['id']}/hls/{segs[0]}")
            print(f"first segment: {r.status_code} {r.mimetype} {len(r.data)} bytes, sync byte 0x47: {r.data[:1] == b'G'}")
            r = web.get(f"{base}/v/{probe['id']}/hls/hls1/main/0.ts?s={'0' * 32}")
            print(f"segment with a made-up session: {r.status_code}")
        finally:
            n = sessions.stop_for(tok, probe["id"])
            print(f"stopped {n} transcode(s); active now {sessions.active_count()}")
    print("health:", json.dumps(web.get("/health").get_json()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
