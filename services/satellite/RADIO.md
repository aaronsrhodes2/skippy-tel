# Bilby's house radio (`radio.py`)

ONE station on skippy-pc. Every speaker in the house that is on plays the same tune, like a radio station. The room hubs are only its
remotes: they change the station or add to it, and they never play it. In the Captain's words, 2026-11-01: *"Bilby on SkippyTel should be
the DJ, not those hubs."*

```
            hub.py (every room)         the portal's Bilby Connect, the car
                    │  POST /tool, /control, /duck           │  check-ins as "House stereo"
                    ▼                                        ▼
            ┌───────────────────── radio.py (127.0.0.1:47850) ─────────────────────┐
            │ station.py: the queue, discovery, favorites, filters, Spotify         │
            │ stereo.StereoMusic + dj.DjPlayer (the beat-matched mixing)            │
            │ its own router.Router (the hub's outputs, the hub's delays)           │
            └──────┬─────────────────────────┬─────────────────────────┬─────────────┘
                   │ hdmi (WASAPI)           │ enclave (tcp :4954)     │ pods (the tap)
                   ▼                         ▼                         ▼
          the Sony, downstairs     skippy-core snapserver `radio`    GET /live/<secret>.flac (:47851)
          (Windows mixes the hub's  → snapclient enclave-radio        → each pod's media player
           voice in)                → PipeWire (mixes the hub's)     (handed the URL by its hub)
                                    → the Enclave, upstairs
```

## Running it

```bash
bash tools/install_radio_startup.sh --apply      # once: the SkippyRadio task runs tools/radio_run.sh at sign-in
MSYS_NO_PATHCONV=1 schtasks.exe /Run /TN SkippyRadio
tail -f state/radio.log
curl -s 127.0.0.1:47850/state
```

`tools/radio_run.sh` keeps it running (one copy only, 3 s backing off to 60 s). It is NOT the hub, so restarting the hub (by its PIDs, quiet
log, `tools/hub_run.sh` brings it back) never touches the music. Restarting the radio itself has a short gap: it resumes the same track at the
same place on the same speakers.

## What it owns

* **The queue:** discovery, favorites, the grammar's filters (mixes.resolve), a search by name, "queue ... after this". `station.py` runs
  every command; it is the old hub music tools, moved here whole, minus the pods' own players.
* **The DJ:** `dj.DjPlayer` (DJ mode on: beat-matched crossings on a phrase; off: gapless cuts). `dj_mode` on/off.
* **Spotify:** local first (a Spotify row he owns plays from Bilby's file), the official app captured from the VB-Cable into the router, and
  an instant fallback: a row Spotify refuses is skipped to his next song at once, never silence.
* **The state**, in `state/radio/station.json` every 3 s: the queue, where it is, whether it plays, the speakers that are on, the volume.
* **Which speakers play:** `hdmi`, `enclave`, and `pod:<room>` for a pod's own speaker. Places come from `house_layout.json`:
  everywhere / upstairs / downstairs / a room; "here" is the asking room's place (the living room is the Enclave), or its own pod when the
  room has nothing else.

## The API (127.0.0.1 only; no door to the network)

| call | what |
|---|---|
| `GET /state` | playing, track, title, artist, pos, idx, queue_len, speakers, open, closed, where, level, dj, live |
| `POST /tool {name, args, room}` | the hubs' music tools: `play_music` (his grammar), `music`, `speakers`, `dj_mode`, `spotify`. Returns `{said}` |
| `POST /control {action}` | pause / resume / next / previous |
| `POST /shuffle` | the whole library (the morning routine) |
| `POST /duck {kinds, on, hold_s}` | Skippy speaks in a room: those speakers duck the station, the others play on |
| `POST /take {state, room}` | Connect's "Play on" a pod: that player's queue, on that room's speakers |

The live stream for pods is on `0.0.0.0:47851` (`/live/<secret>.flac`: endless 16 kHz mono FLAC, the secret derived from the home token).

## Sync

The radio's router gets each output's delay exactly as the hub's `router.shared()` does: the one-voice cushion plus `sync.saved_align`
(`sync_outputs.py`'s measured latencies, in `state/audio-sync.json`), so the Sony and the Enclave stay together. Re-measure with
`sync_outputs.py` when a speaker changes, then restart the radio. A pod's own speaker buffers its stream on its own; it is not lined up with
the PC outputs yet.

## Settings (the vault)

`RADIO_PORT` (47850), `RADIO_LIVE_PORT` (47851), `RADIO_MEDIA_PORT` (10801: the radio's own media server, for a DJ track not on this PC),
`RADIO_OUT_ENCLAVE` (`tcp://skippy-core:4954`), `RADIO_URL` (the hub's address for it), and the station's own: `SKIPPY_STEREO_OUT` (its first
speakers), `BILBY_DJ`, `BILBY_BLEND`, `SKIPPY_SPOTIFY`, `SPOTIFY_XFADE_S`, `SKIPPY_STEREO_NAME` (its Connect name).

skippy-core's side is `core/install/enclave.sh` (the `radio` source, the `enclave-radio` snapclient, the tailnet door :4954) and
`core/enclave/keep.sh` (starts both snapclients and keeps the radio's on its own stream).

## Next: the voice channel (one speaker service, two channels)

The Captain, 2026-11-01: *"Doesn't the Skippy Voice output function the same way? One voice stream for all."* The radio's router becomes THE
house's speaker service, with two channels on the same outputs:

1. **the station** (done): every enabled speaker plays it;
2. **voice on top:** a clip plus a target set of speakers. A reply goes to the room he spoke in, and an announcement, alarm or timer to a place
   or everywhere. Each clip ducks the station on exactly its speakers and restores them when it ends.

The hubs still MAKE the voice (TTS, fillers, lips) and only send `clip + targets`. Ducking lives in one place, so a hub restart can't leave a
speaker ducked. Even today a `/duck` expires by itself after `hold_s`.

Why it isn't in this pass: the hub's router does more than play a clip, and each part has to move with it:
- `Sink.realign` lines each clip up against the pod;
- `Play.marks` and `speaker_out.sound_at` time the lips on the pod's face;
- `sync.Calibrator` and the PC mic check every reply was heard (one voice falls back to the pod);
- `STALE_VOICE_S` drops a late clip instead of playing it late.

Moving them is a design pass of its own.

The steps:
1. `POST /voice` on the radio: the WAV/PCM body, `targets`, `layer=voice`, `id`. It returns as soon as the clip is queued, with each output's
   mark `(clock, queued_ahead, latency)`, which the hub needs for the lips. A `GET /voice/<id>` waits for the clip to finish.
2. Per-output voice ducking in `Router.mix_once`: media is ducked only on a voice clip's own targets (`kind_duck` from the voice plays,
   replacing today's global `layer_gain["media"]`).
3. The callers move to a `speaker_out.RadioOut`, a drop-in for `RoutedOut` whose `play()` posts to `/voice` and returns the marks:
   - `speaker_out.RoutedOut` (a room's voice, `Room._out`);
   - `announce_outputs` and the `announce` tool;
   - `alarm_clock.AlarmClock`'s ring, which keeps ringing with every pod away; it moves last, and keeps a local fallback if the radio is down.
4. The calibrator moves into the radio, since it owns the outputs the PC mic hears. The hub drops `router.shared()`'s sinks: one sender per
   output, which also frees `sync_outputs.py` from "run it while the hub isn't sending".
5. Then the Enclave needs only the radio's snapserver source, and the hub's `:4953` client retires.

## Next: the audiobook as a second source (one current book)

The Captain, 2026-11-01: *"the audiobook should similarly be a single-source tap for audiobooking, and it keeps the book and position of the
last played."* The radio plays either the station or the current book on the chosen speakers.

**Today:** the house's book still plays through the hub's `audiobooks.BookPlayer` on the pod and the PC outputs, so a hub restart stops it. It
was left there on purpose for the first night (no half-migrated house). The shared store is already in place: `audiobook-gate`'s
`places.json`, merged by `audiobooks.Store` from `state/audiobooks-inbox`. Since 044861e each book there carries `src` and `playing`, and the
car writes `src: "car", playing: true` every 5 s while it plays.

The steps:
1. **Source:** a `BookSource` in the radio: the current book from the Store at its position, through `book_out.pcm_args` (speed via
   `tempo_filter`), on the router's `media` layer (Skippy's voice ducks it like music). It writes its position back every 5 s with
   `src: "house"`.
2. **One player at a time:** the radio plays the station or the book, never both. Starting the book pauses the station, and the reverse.
3. **API:** `POST /book {action: play|pause|resume|skip|speed, ...}`. The hub's `book_tools` and `book_voice.pre_route` forward to it, the way
   `music_tools` does. "Skippy, play my book" resumes the current book where it stopped, wherever it last played.
4. **The car hand-over:** a book in the store with `src` = car and `playing` = true pauses the house's copy at once. "Play my book" at home
   resumes at the car's position.
5. **Connect:** the station's check-in carries `book` state (`audiobooks.book_state`), so the portal's Audiobooks "Play on" reaches the radio.
   The pods' `book:` commands forward to it like their music commands.
6. **Retire:** the hub's `BookPlayer` and `book_out.PcBookOut`, after a day of the radio's book playing.

## Tests

`tests/test_sat_radio.py`: a hub restart doesn't touch playback, the radio's own restart resumes, the speaker set switched by place, the grammar
through the API, the Spotify fallback, ducking one room. `tests/test_sat_esphome.py`: a pod tuning in and out, a hub coming back to a pod that is
still playing, a pod as a Connect remote.
