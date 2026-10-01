# video-gate

Nagatha's Video Service, the back half: the Captain's Jellyfin library for the browser player (`/video` on the portal, built next).
Docker service `video-gate` in `docker-compose.override.yml`, published on 127.0.0.1:3006 only; Tailscale Funnel
(`https://skippy-pc.<tailnet>.ts.net:8443`) is the way in from outside. The sibling of `services/music-gate` and built the same way:
the portal never proxies video, it renders the page and signs short-lived **passes**; this container holds the Jellyfin API key (ADMIN
level, from the vault's `JELLYFIN_API_KEY`) and shows the internet a tight whitelist of what that key can do. The code's top docstring
(`gate.py`) is the authority on routes; this page is the why and the how-to.

```
browser (portal /video) --https--> Tailscale Funnel :8443 --> video-gate (127.0.0.1:3006) --> Jellyfin (the host's :8096)
```

Jellyfin is a Windows service on skippy-pc, not a container: the gate reaches it as `http://host.docker.internal:8096`
(`extra_hosts: host.docker.internal:host-gateway`, like the home reporter reaches the bridge). Jellyfin listens on 0.0.0.0:8096, and
this was checked from inside a running container: it answers. Nothing in Jellyfin is changed by any of this.

## Start it, publish it, stop it (nothing is started by the commit that adds these files)

```bash
cd /d/Aaron/development/SkippyTel
docker compose stop video-gate; docker compose rm -f video-gate; docker compose up --build -d video-gate
curl -s http://127.0.0.1:3006/health          # {"items":285,"jellyfin":true,"ok":true,"ready":true}  (ready once the first catalog is built: seconds)
tailscale funnel --https=8443 --bg 3006        # the internet door. Funnel offers 443, 8443 and 10000; 443 is the music gate's
tailscale funnel status                         # shows both: :443 -> 3005 and :8443 -> 3006
# take it off the internet again (the container can keep running):
tailscale funnel --https=8443 off
```

A plain `docker compose up -d --build` also starts it (the override file is merged automatically). If `JELLYFIN_API_KEY` is missing
from the vault the container exits at once with `video-gate cannot start: JELLYFIN_API_KEY missing or malformed` (and never prints the
key); if `SKIPPY_HOME_TOKEN` is missing it starts but refuses every pass, like the music gate.

## The pass

The same scheme as the music gate's, with its own label and its own signed text, so **a music pass can never open this gate and a video
pass can never open the music gate**. Every path but `/health` starts with `/t/<exp>.<sig>/`:

```
key  = HMAC-SHA256(key = sha256_hex(SKIPPY_HOME_TOKEN), msg = b"video-gate-v1")
sig  = HMAC-SHA256(key, b"video:<exp>").hexdigest()[:32]          pass = "<exp>.<sig>"      exp = epoch seconds, at most 2 days ahead
```

Test vector (the portal's signing must reproduce it; `tests/test_pass.py` pins it): token `test-home-token` (sha256
`d9b49845ccf7825b16d8a6026f24f283a700ec94ad6e750e0ff344db365934a0`), exp `1800003600` gives
`1800003600.d8383a1cefb72b136b01fb22ff9e87fb`. The portal only holds the token's sha256, so it starts from the hex, exactly as
`portal/player.py` does for music.

## Routes

| route | what |
|---|---|
| `GET /health` | no pass: `{ok, ready, jellyfin, items}`; `ready` = a catalog exists AND Jellyfin answers (cached 5 s). The reporter's probe |
| `GET /t/<pass>/catalog.json` | `{"v":1,"items":[...],"collections":[...]}`, gzipped, ETag/304. Built from Jellyfin at start, refreshed every 5 min and, when someone asks, at most once a minute; if Jellyfin is down the last good one is served |
| `GET /t/<pass>/img/<id>/<poster\|backdrop\|logo\|thumb>?w=` | `w` snaps to 160, 320, 640 (default) or 1280; a non-number or 0 or > 4096 is a 400. Cached on disk (500 MB, oldest evicted), `Cache-Control: private, max-age=604800` + ETag. 404 when the item has no such picture |
| `GET /t/<pass>/v/<id>/direct` | the original file, **streamed** (never buffered), Range/206/416, `Content-Type: video/mp4`. `409 {"use":"hls"}` when the catalog says a browser can't play it as it is |
| `GET /t/<pass>/v/<id>/hls/master.m3u8?audio=<i>&h=<px>&start=<sec>` | a transcode session (see below). `audio` must be one of the film's audio stream indexes, `h` 144-2160 (default 1080), `start` 0..runtime seconds |
| `GET /t/<pass>/v/<id>/hls/<file>?s=<session>` | the variant playlist, segments and init segment that this session's playlists advertised, and nothing else |
| `GET /t/<pass>/sub/<id>/<index>.vtt` | a text subtitle as WebVTT (cached 10 min); 404 for an index the catalog doesn't list as `vtt` |
| `POST /t/<pass>/progress` | `{"id","pos"(seconds),"played"?(bool),"state":"start\|progress\|stop"}`, body at most 1 KB |

Every `<id>` is 32 lower-case hex and must be in the catalog (anything else is a 404 and Jellyfin is never asked). Query parameters are
whitelisted and validated, never forwarded; client headers are never forwarded (only a validated single `Range`). Errors are one of a few
fixed JSON bodies (`{"error": "not found"}`...), never upstream text.

CORS: any origin may read (the pass is the credential, never a cookie). `Content-Length, Content-Range, Accept-Ranges, ETag` are exposed;
`Range`, `If-None-Match` and `Content-Type` are allowed; the preflight is answered without a pass.

### Catalog item

`id, type ("movie"|"series"|"episode"), title, sort, year, runtime (seconds), overview (<= 400 chars), genres[], rating (0-10 or null),
official ("PG-13"...), added (epoch), collectionIds[], hasPoster, hasBackdrop, resume (seconds, 0 = none), played, play` and, for an
episode, `seriesId, season, ep`. `play` (null for a series: it is a shelf, not a file):
`{container, vcodec, acodec, height, direct, audio:[{i,lang,codec,ch}], subs:[{i,lang,codec,external,vtt}]}`.
A collection is `{id, title, sort, itemIds[] (by year), hasPoster, hasBackdrop}`. Fields only ever grow at the end of the objects.

**`direct`** is the conservative "every mainstream browser plays this file as it is": MP4/MOV/M4V container, 8-bit 4:2:0 H.264 (no High 10,
4:2:2, level above 5.2, or height above 2160) and the FIRST audio stream AAC-LC/HE-AAC or stereo MP3 (or no audio). Anything else
(MKV, AVI, HEVC, AC3, DTS, MP2, FLAC, a file Jellyfin hasn't probed yet) is `false`: play it through HLS. The player picks per item:
`direct ? /direct : /hls/master.m3u8`.

### HLS (the transcoded fallback)

What Jellyfin 10.11.8 really sends was measured, and it is why the gate **parses and rewrites** every playlist instead of passing it on:
`master.m3u8` carries an `#EXT-X-IMAGE-STREAM-INF` line whose URI holds the real **API key**; `main.m3u8` is a whole-film VOD list (800 segments
for a two-hour film) whose segment URLs repeat every parameter and add `runtimeTicks`/`actualSegmentLengthTicks` that Jellyfin needs to find
the segment again; and `StartTimeTicks` does not shift the playlist (it makes every segment fail). So the gate writes new playlists out of
validated pieces only (a known tag, a file path in an allowed shape, `?s=<session>`), keeps Jellyfin's parameters and tick numbers in a
per-session table on its side, and hands the player RELATIVE urls under the same `/t/<pass>/v/<id>/hls/` prefix. A start offset is given as
`#EXT-X-START:TIME-OFFSET=<s>,PRECISE=YES` (hls.js and Safari honour it; a player may also seek itself).

* Output is H.264 + stereo AAC in MPEG-TS (Jellyfin copies the video stream when it already fits, and only re-encodes what it must), a
  single variant. Software ffmpeg: Jellyfin has no hardware acceleration set and nothing here changes its settings.
* **At most 2 transcodes at once** (`HLS_MAX_TRANSCODES`); a third viewer gets `429` + `Retry-After: 10` and nobody else's film is touched.
  One transcode per pass: starting another film with the same pass stops the first (`DELETE /Videos/ActiveEncodings`). A progress report with
  `"state":"stop"` stops it at once; a session nobody has asked anything of for 2 minutes (`HLS_IDLE_S`) is stopped in Jellyfin but remembered,
  so a paused film resumes (the next segment request wakes Jellyfin's transcoder again); sessions are forgotten after 6 hours.
* A film's HLS needs a `master.m3u8` first: a request for a file with an unknown session is a 404.

### Progress (Continue Watching)

The gate writes the watch state to Jellyfin as the configured user (`POST /UserItems/<id>/UserData`, which 10.11 has: checked in its own
OpenAPI document), so Jellyfin's own apps' "Continue Watching" agrees with the browser, and the next catalog download already shows it.
`start` writes nothing; `progress` writes the resume point (under 30 s in is not worth resuming: stored as 0); `stop` at 90 % or more of the
runtime marks the film watched (`Played`, `PlayCount` 1 the first time) and clears the resume point; `"played": true|false` overrides. Reports
for one film are throttled to one write per 5 s (`{"ok":true,"throttled":true}`, no upstream call), a `stop` to one per second and never held
back by a `progress`. `navigator.sendBeacon` with a JSON string (`text/plain`) works for the "tab is closing" report.

### Notes for whoever builds the player

* A poster can answer `503` + `Retry-After: 1` when a burst of uncached pictures is already being fetched (at most 4 at once and 8 waiting;
  the gate has 48 threads and a film that streams holds one). Retry the `<img>` after a second or so; a cached poster never gets one. The
  catalog's `hasPoster`/`hasBackdrop` say which pictures exist (`logo` and `thumb` are tried, and 404 when absent).
* Streams: at most 32 at a time (`MAX_STREAMS`); the 33rd gets `429` + `Retry-After: 2`. A browser may hold two or three range connections for
  one film, and a seek makes a new one.
* If Jellyfin goes away mid-film the connection is cut (the player sees a network error and retries); it is never ended "cleanly" short.
* A subtitle file starts with a byte-order mark and `WEBVTT`; both `<track>` and hls.js are fine with it.

## Measured on the real library (2026-09-30, read-only, `smoke_real.py`)

285 movies, 11 collections, no series yet. The catalog builds in 0.3 s and is 49 KB gzipped (208 KB of JSON). 285 have a poster, 188 a
backdrop, 1 has a text subtitle (Tetsuo II). **252 play direct, 33 need HLS**: 22 MP4s whose first audio is MP2, 7 that Jellyfin hasn't probed
(no container yet), 2 MKV (AC3, DTS), 2 AVI (MPEG-4). A direct stream of the first KB, a 416 beyond the end, a poster, a collection poster, the
subtitle as WebVTT and one short HLS probe (master, media playlist of 794 segments, the first segment: MPEG-TS, then the transcode stopped)
all came back right, with no key in any playlist.

## Settings (environment)

`SKIPPY_HOME_TOKEN` (the pass key is derived from it), `JELLYFIN_API_KEY` (vault), `JELLYFIN_URL` (`http://host.docker.internal:8096`),
`JELLYFIN_USER` (a name or id; default the first administrator, resolved when Jellyfin first answers), `STATE_DIR` (`/state`),
`CATALOG_REFRESH_S` (300), `HLS_MAX_TRANSCODES` (2), `HLS_IDLE_S` (120), `MAX_STREAMS` (32), `IMG_CACHE_MB` (500). A typo in a number
falls back to the default with a warning instead of stopping the video. `/state` holds only `img/` (the poster cache, bounded).

## Safety model, in one place

* Nothing is reachable without a pass; the unknown-path-under-`/t/` is a 403 too; no route redirects; no `/static`.
* The API key is only ever in an `Authorization` header from `jellyfin.py`: never in a URL (so not in a log, a Referer or a playlist), never
  returned, never logged (the client's `repr` hides it; the playlist rewriter refuses any playlist that contains it, as a last line of defence).
* `jellyfin.py` is the only way out: every upstream URL is a template plus pieces re-validated there (32-hex ids, small integers, four image
  kinds, three file shapes), redirects are not followed, the environment's proxies and cookies are ignored, every call has timeouts, and an
  upstream failure is reduced to `down|missing|refused|bad` with no upstream text.
* Streams hold a slot each and give it back when the client leaves, when the file ends or when Jellyfin dies; the upstream connection is closed with it.
* No access log (the pass is in every URL); the error log carries a class name and a file:line, never a request, a pass or a key.

## Tests

`python -m pytest services/video-gate/tests -q` needs only flask, requests and pytest (no Pillow, no Jellyfin): a fake Jellyfin 10.11 runs in
the test process (`tests/fake_jellyfin.py`, behaviours copied from the measurements above). Own pytest session: the music gate has a
`gate.py` too. **Not in `.github/workflows/test.yml` yet**, like the music gate's: to add them, a step `python -m pytest services/video-gate/tests -q`
in both `test.yml` and `deploy.yml` (their existing `pip install` already has flask and requests).

`python services/video-gate/smoke_real.py [--no-hls]` is the read-only smoke against a real Jellyfin (key from `JELLYFIN_API_KEY` in the
environment, never printed; it writes no watch state and changes no setting; the HLS probe starts one short transcode and stops it).
