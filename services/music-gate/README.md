# music-gate

The one piece of skippy-pc on the public internet, and all it does is hand out the Captain's music to the browser player
(Bilby's Music Service, `/music` on the portal). Docker service `music-gate` in `docker-compose.override.yml`, published on
127.0.0.1:3005 only; Tailscale Funnel (`https://skippy-pc.<tailnet>.ts.net`) is the way in. The code's top docstring
(`gate.py`) is the authority on routes; this page is the why.

Run its tests with `python -m pytest services/music-gate/tests -q` (needs flask, mutagen and Pillow; they are **not** in
`.github/workflows/test.yml` yet. To add them: `pip install mutagen` and a separate `python -m pytest services/music-gate/tests -q`
step, its own session like flask-api's, since the module names `gate`, `library`, `art` and `lyrics` are generic).

## Routes (all behind `/t/<pass>/`; the portal signs the pass, nothing here works without one)

| route | what |
|---|---|
| `GET /health` | no pass: `{ok, ready, tracks, scanning, lyrics}`, the reporter's probe |
| `catalog.json` | the library, gzipped, ETag. `fields` lists the columns: `p,t,a,al,n,d,g,s,art,y,m` (path, title, artist, album, track, disc, genre, seconds, has art, **year** (0 unknown), **mtime**). New columns only ever go on the end |
| `lyrics.json` | `{"v":1,"by":{<catalog path>: "one sentence about the song"}}`, gzipped, ETag. `by` is empty until flask-api has answered once |
| `a/<path>` | the track, with Range |
| `art/<path>` | the cover as it is in the file |
| `art/<path>?w=192` | a JPEG thumbnail; `w` snaps to 96, 192, 384 or 768 (a non-number or 0 or > 4096 is a 400); `Cache-Control: private, max-age=604800` + ETag |

CORS: any origin may read (the pass is the credential). `ETag` is exposed and `If-None-Match` is allowed, so the player can cache
the catalog and the lyrics by ETag; the browser's preflight is answered.

## Notes for whoever builds the player

* **`m` ("date added") is the file's mtime.** In the real library that is almost no information: all 21,451 files date from 7
  days in April 2026 (12,092 from 2026-04-18, 8,304 from 2026-04-17, when the library was rebuilt on the Mac). "Recently added"
  will only be meaningful for files that arrive after that; break ties by path.
* **Thumbnails are cached per track path**, not per album. For an album grid ask for one representative track per album (the first
  with `art = 1`), not one per track, or the cache (600 MB, oldest evicted) holds a dozen copies of one cover. (Measured: all
  13,417 covers at all four sizes would be ~1 GB, so the grid should stick to 192 and Now Playing to 384/768.)
* **A thumbnail can answer `503` + `Retry-After: 1`** when a burst of uncached covers is already being made (at most 4 at once
  and 8 waiting; the gate has 32 threads and a streaming track holds one). Retry the `<img>` once or twice after a second or so;
  a cached thumbnail never gets a 503. `art = 1` in the catalog means the file really holds image data; a track with `art = 0`
  is a 404 for `art/`.
* `y` comes from the tag (TDRC/TYER, MP4 ©day): 19,896 of 21,451 tracks have one; a `(YYYY)` in the album folder fills in when
  the tag has none (it agrees with the tag in all but one of the files that have both).

## Lyric lines: where they come from and how they find their track

The line under a song's title ("what it is about") is Bilby's, written on the Mac by a local model. skippy-core's flask-api
serves both of Bilby's stores at `GET /music/lyric-summaries` (read-only, from `/drive-state`): `lyrics_index.json` (one entry per
file, Mac paths as keys) and `lyrics_dedup.json` (one per song, `artist<TAB>title`, the store the Mac itself writes its Traktor
comments from). flask-api turns the Mac paths into library-relative ones and leaves out what can't be shown (other folders,
FLAC/OGG, content flags, garbled summaries); **the gate matches the lines to the catalog**, because only it knows what is in the
library: it fetches at start and every 3 hours (retrying every 30 s to 10 min while flask-api is down), keeps a copy in
`/state/lyrics.json`, and matches again whenever a scan rebuilds the catalog. flask-api being down never costs playback or the
catalog; the lines just stay as they were.

Matching, strictest first (`lyrics.py` has the details and tests): the entry's path **is** a catalog path; the same path ignoring
Unicode form and case (only when one catalog file fits); and for everything left, the **song**: same artist (folder) and title (tag),
the title cut the way the Mac cuts it (no trailing "(Radio Edit)" / "[Remaster]"), unless the title is generic ("Intro", "Track 13").

Measured on the real stores and the real library (2026-09-30), 21,451 catalog tracks:

| | tracks with a line |
|---|---|
| per-file store only (`LYRICS_MATCH=path`) | 16,476 (76.8 %): 16,214 exact + 262 case/Unicode |
| + per-song store (default) | **20,863 (97.3 %)** (+4,387 by song) |

Of the 18,943 per-file entries: 242 are outside the library (Traktor factory sounds, other folders), 121 are FLAC/OGG, 72 have a
garbled summary (the model slipped into Chinese mid-sentence), 20 are duplicates after straightening, 2,012 name files that are no
longer in the library (the Mac's `_3` duplicate copies, removed albums), and 16,476 land on a track. Hunting for those 2,012 in the
folders (same folder and title, or same artist and title) was tried and gains 4 tracks: they are songs whose other files already
have the line. The 4,387 tracks the per-song store adds are
mostly ones the Mac had no lyrics for; their lines are the Mac's own (some are the STT pass's "Instrumental — no vocals detected.",
and a speech-to-text guess on an instrumental can read oddly). That is what the Mac's DJ view shows for them too.

## Settings (environment)

`SKIPPY_HOME_TOKEN` (the pass key is derived from it), `MUSIC_ROOT` (`/music`), `STATE_DIR` (`/state`), `LYRICS_URL` (default
`http://flask-api:5000/music/lyric-summaries`; empty = no lyric lines), `LYRICS_MATCH` (`song` default | `path`), `ART_CACHE_MB`
(600). `/state` holds `tags.json` (tag cache: a restart doesn't re-read 21k files), `lyrics.json` (the copy of the lines) and
`art-cache/`.

First start after this version: the tag cache is re-read once for the year (about 3 minutes on a local disk, longer over Docker
Desktop's bind mount). The previous cache keeps building the catalog meanwhile, so the player isn't left waiting (years come from
the album folders until each file has been read).
