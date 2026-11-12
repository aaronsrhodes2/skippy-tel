# Bilby's house radio (`radio.py`)

ONE station on skippy-pc. Every speaker in the house that is on plays the same tune, like a radio station. The room hubs are only its
remotes: they change the station or add to it, and they never play it. In the Captain's words, 2026-11-01: *"Bilby on SkippyTel should be
the DJ, not those hubs."* And, 2026-11-05: *"Bilby plays all the audio, even Audible books ... a single audio stream going out and the devices
decide if they are outputting that stream or not."* So a song, a Spotify track and an audiobook are items of one queue and come out as one stream; each
speaker has a switch; with no speaker listening the stream waits; Skippy's voice ducks all of it.

```
            hub.py (every room)         the portal's Bilby Connect, the car
                    │  POST /tool, /control, /duck           │  check-ins as "House stereo"
                    ▼                                        ▼
            ┌───────────────────── radio.py (127.0.0.1:47850) ─────────────────────┐
            │ station.py: the queue, discovery, favorites, filters, Spotify, books  │
            │ stereo.StereoMusic + dj.DjPlayer (the beat-matched mixing)            │
            │ audiobooks.BookPlayer (a book = the queue item "book:<id>")           │
            │ outputs.Outputs (each speaker's switch) + HouseRadio.listen_tick      │
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
* **Which speakers play:** `hdmi` (the Sony), `enclave`, and `pod:<room>` for a pod's own speaker, each with a SWITCH (see "Outputs" below). Places come from
  `house_layout.json`: everywhere / upstairs / downstairs / a room are shortcuts that set the switches; "here" is a place only when it is said.

## The API (127.0.0.1 only; no door to the network)

| call | what |
|---|---|
| `GET /state` | playing, track (never a book), item, kind (song / spotify / book), title, artist, pos, idx, queue_len, speakers, open, closed, where, level, dj, book, outputs, listening, held, live |
| `POST /tool {name, args, room}` | the hubs' music tools: `play_music` (his grammar), `music`, `speakers` (place, or device + on), `dj_mode`, `spotify`, `audiobook`. Returns `{said}` |
| `POST /control {action}` | pause / resume / next / previous |
| `POST /shuffle` | the whole library (the morning routine) |
| `POST /duck {holder, on, hold_s}` | Skippy speaks somewhere: the WHOLE stream ducks on every speaker while any holder (`room:<name>`, `router`) is speaking; each expires after `hold_s` by itself. (`{kinds}` from a hub not restarted yet still works, as a holder of its own) |
| `POST /take {state, room}` | Connect's "Play on" a pod: that player's queue, on that room's speakers |
| `POST /book/find {query}` | the audiobook shelf: `{best, many}` for the hub's plain spoken commands (`book_voice.pre_route`) |
| `POST /book/command {action, room}` | one of the portal's `book:` commands; a pod forwards its own (then that room's speakers play it) |

The live stream for pods is on `0.0.0.0:47851` (`/live/<secret>.flac`: endless 16 kHz mono FLAC, the secret derived from the home token).

## Outputs: the devices decide (`outputs.py`)

The stream is ONE; each speaker that could play it has a switch kept in `state/radio/outputs.json` (atomic; a missing or torn file means every stereo on, never silence).

| speaker | switch |
|---|---|
| a stereo: `hdmi` (the Sony), `enclave`, `bt` | `on` / `off`, on by default |
| a room's box: `pod:<room>` | `auto` (default: silent), `on`, `off` |

* `Outputs.effective()` is the set the stream is sent to: every stereo that is on (there or not: an Enclave that drops comes back to the same stream), every box that is on, and a box
  on `auto` whose floor's stereos are ALL on but gone for 20 s (`FALLBACK_S`; the Enclave drops ~30 s on every Tesla charge command). A stereo switched OFF is respected: no box covers.
  `HouseRadio.apply()` (every second, and at every change) retargets what plays: the same play, the same song, never restarted.
* `Outputs.listening()` is the subset that hears it now: a stereo that is on and really there (the Enclave has a snapclient on the snapserver, `router.NetWatch`; the Sony's output is open),
  a box that holds the live stream open (`Tap.listeners`, labelled with its room: the pod's URL carries `?room=<room>`).
* The places ("upstairs", "downstairs", "everywhere", a room, "here") are shortcuts: `HouseRadio.place(kinds)` sets exactly those on, the other stereos off, the other boxes back to
  `auto`. A play that names no place leaves the switches alone. `speakers` with `device` + `on` flips ONE ("turn the Sony off"); Connect's `out:<id>:on|off` (the portal's toggles) too.
* The first start after the switches arrived keeps where the stream was playing (the saved speakers seed the file); after that the file rules.

### No listener, no stream (`HouseRadio.listen_tick`)

* The last speaker that is on is switched off: the stream is held at once. It is on but drops out: 30 s to come back or for a box to cover (`RADIO_HOLD_S`; 0 turns the rule off), then it is
  held and the place is put back to where the sound was lost (a song's second, a book's place). A play with every speaker off is held at once and the reply says so.
* Held = the radio's own pause (`held` in `/state`, Connect and `station.json`): never counted as his stop (`RadioClient.pauses`), the queue and the place kept, a restart keeps it.
* It carries on by itself when a speaker is back within 10 minutes (`RADIO_RESUME_S`) and no command came since (`BoxMusic.epoch` counts start / pause / resume). After that, or after any command, it is an
  ordinary pause: last night's music does not start under tomorrow's TV.
* The car: a book the car is playing (`audiobooks.Store.car_playing`) makes the house let go of its copy without writing its place over the car's (`HouseRadio.car_tick`).

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

## The audiobook is an item of the queue (done 2026-11-05)

The Captain, 2026-11-01: *"the audiobook should similarly be a single-source tap for audiobooking, and it keeps the book and position of the last played."*
2026-11-05: *"Bilby plays all the audio, even Audible books."* So the book is not a second source: it is the queue item `book:<id>`.

* The engine (`audiobooks.BookPlayer`: the place per book in `state/audiobooks.json`, the chapters, the speed, the sleep timer) runs in the radio, on `stereo.StationBookOut`: its file, from a
  second, at its speed, as a play on the router's `media` layer on the station's speakers at the station's gain. The radio is the only writer of the store; the car's places arrive through
  `state/audiobooks-inbox` (`services/audiobook-gate`).
* `BoxMusic` plays it like any item (`player.play_book`): `pos()` is the engine's clock, `next` / `previous` are chapters (the last chapter goes on to the next item), `pause` is the one pause,
  a book that ends advances the queue, the sleep timer pauses the station. Music started over a book replaces it and the place is kept; `audiobook play` with no title resumes the loaded book.
* `DjPlayer` ends its chain at a book; Connect's state never shows a book as a `track` (the car's hand-over would play `book:<id>` as a song) and carries `book` and `kind`.
* The hub holds no book player: `audiobook` and the plain spoken commands go to the radio like every music tool, so a hub restart cannot stop a book. Retire after a day of the radio playing books:
  `book_out.PcBookOut` / `RoutedBookOut`, `media.book_url`, and the pod / PC outputs inside `BookPlayer`.

## Tests

`tests/test_sat_radio.py`: a hub restart doesn't touch playback, the radio's own restart resumes, the speaker set switched by place, the grammar
through the API, the Spotify fallback, ducking the whole stream (holders). `tests/test_sat_book_item.py`: the book as the queue's item (transport, chapters, end, sleep timer, restart, Connect, the
car). `tests/test_sat_outputs.py`: the switches, the shortcuts, the fallback box, the hold and its resume. `tests/test_sat_esphome.py`: a pod tuning in and out, a hub coming back to a pod that is
still playing, a pod as a Connect remote.
