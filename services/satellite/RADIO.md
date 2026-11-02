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

## Tests

`tests/test_sat_radio.py`: a hub restart doesn't touch playback, the radio's own restart resumes, the speaker set switched by place, the grammar
through the API, the Spotify fallback, ducking one room. `tests/test_sat_esphome.py`: a pod tuning in and out, a hub coming back to a pod that is
still playing, a pod as a Connect remote.
