# Skippy at home (the Alexa replacement)

An always-on listener for a room. It hears through a microphone, wakes on **"Skippy"** (a small openWakeWord model trained on his name, `wakeword/`; until
it is trained, the home Whisper hears the name), thinks with a small fast model in Skippy's character, and answers in Skippy's own voice (local TTS via
the voice gate: Kokoro + F5-TTS). First home: skippy-pc (`ear.py`); every other room through the Wyoming hub (`hub.py`, below).

    python -m venv .venv && .venv/Scripts/pip install -r requirements.txt     # once
    ./run.sh --devices        # which microphone and speaker numbers
    ./run.sh --calibrate      # levels, to set SATELLITE_FLOOR
    ./run.sh --text "set a timer for ten seconds"                              # the brain and the voice, no microphone
    ./run.sh                  # listen

Settings (the vault `.env`, or the environment): `SKIPPY_HOME_TOKEN`, `SKIPPY_CORE_URL`, `SKIPPY_CORE_API_KEY` (required: the brain is Skippy Core, local, never a paid model); `SATELLITE_MIC`, `SATELLITE_SPEAKER`, `SATELLITE_GAIN`,
`SATELLITE_FLOOR`, `SATELLITE_GATE` (default the voice gate on :3007), `SATELLITE_MODEL` (default skippy-chat, Core's resident chat model); `SATELLITE_WAKE_MODEL` (default
`state/wakeword/skippy.onnx`), `SATELLITE_WAKE_THRESHOLD` (default 0.5; `wakectl.py test` picks it); `SATELLITE_ROOMS` (the hub); `MATTER_URL` (skippy-core's Matter controller); `HOMEKIT_KEEP_WARM`
(seconds between keep-warm pings, default 30; 0 turns it off).

## The wake word

`wakeword/README.md`: record him (`wakectl.py record`), train on the GPU (`bash wakeword/train.sh`), test and pick the threshold (`wakectl.py test`).
With a model, room talk is never sent anywhere: only what follows "Skippy" reaches Whisper.

## Every room: the hub

    ./.venv/Scripts/python hub.py                                          # every room in SATELLITE_ROOMS
    ./.venv/Scripts/python hub.py --say kitchen "Dinner is ready, monkeys."

skippy-pc does all the hearing, thinking and talking; a room needs only a box with a microphone and a speaker. Two kinds, in `SATELLITE_ROOMS`:

* `kitchen=esphome://skippy-kitchen.local`: **the room boxes** (Waveshare ESP32-S3 86 boxes flashed with `rooms/skippy-room.yaml`; everything about
  them, flashing included, is in `rooms/README.md`). The hub is the box's Home Assistant (`esphome_link.py`, aioesphomeapi); Skippy's voice and
  any music it plays comes straight from the house radio (below); to Bilby Connect each box is a remote of the radio (`radio_client.PodTuner`).
* `den=192.168.1.41[:10700]`: **a Wyoming satellite** (`wyoming-satellite` on a Raspberry Pi), either waking on "Skippy" itself (its own
  `wyoming-openwakeword` with our model) or streaming everything for the hub to hear its name.

The hub dials each box (as Home Assistant would) and dials it again if it drops. Each room has its own short memory and its own timers
(announced where they were set); the house (lights, plugs) is shared. A `Room` (hub.py) never knows which kind of box it has: that is its link
(`WyomingLink`, `EsphomeLink`). Settings: `SATELLITE_ESPHOME_KEY` (the boxes' API key), `SATELLITE_ROOM_NAMES` (`kitchen=Kitchen (Skippy),...`:
their names in Bilby), `SATELLITE_MEDIA_PORT` (10800), `SATELLITE_MEDIA_HOST` (this PC's address as the boxes see it; found by itself),
`SATELLITE_MUSIC_GATE` (`http://127.0.0.1:3005`), `PORTAL_URL`. Tests: `tests/test_sat_hub.py` (a fake Wyoming box over a real socket, the real
protocol through the `wyoming` package) and `tests/test_sat_esphome.py` (a fake ESPHome box, Home Assistant's event order).

**Testing the wake loop for real: through the pod's own speaker** (the Captain, 2026-11-01: the standard loop test). `wakeloop.py` plays a
"Hey Skippy" and then a command on the pod's media player as media (an announcement would stop its microphone), so the pod's own microphone hears
them like a voice in the room; it reads `state/hub.log` for `woke`, `heard:` and the watchdog, and prints one line per trial (exit 0 when all pass).
The synthesized "Hey Skippy" wakes the model about half the time (`woke=False` with the clip played is the model, not the loop); it waits for
the room to be quiet first, but the Captain talking to Skippy can still land inside a trial.
The hub keeps running; the pod plays it aloud. Not the projector's HDMI: it is off at night and too quiet at the pod.

    ./.venv/Scripts/python wakeloop.py                       # 3 trials: "Hey Skippy", 3 s, a question (a new one each trial)
    ./.venv/Scripts/python wakeloop.py --command ""          # the name alone: the room must keep listening (no "no audio from the box")
    ./.venv/Scripts/python wakeloop.py --wake clips          # his own recordings (state/wakeword/real/test), served by the hub (GET /w/<pass>/)

**The box's microphone stream** (2026-11-01): a box streams the whole time it rests, so the hub hears its name. The hub stops it (STT_VAD_END)
only once a transcript has something to answer, never for the name alone; the 8 s no-audio watchdog counts from when a listening window opens;
and a box that sends nothing for 20 s at rest is asked to start again (its turn ended, then its Microphone switch off and on; never when the
Captain switched it off), logged as `(no audio from the box ...: ...)` and `(the box's audio is back, N s after asking)`.

**One voice when the projector is off** (`sync.py`): with `SATELLITE_ONE_VOICE` the room speaks from the PC output alone; the PC's microphone
checks every reply. Four long replies in a row it never heard and the output is taken for off: the pod speaks. Every 10 min one reply goes to the
output alone as a probe; heard, it is the room's voice again, not heard, the pod says it right after.

## The house, natively (no Alexa, no cloud, no hub)

Skippy talks to the lights and switches itself, over the local network, with open-source libraries. His Leviton Decora Smart Wi-Fi dimmers and switches
announce themselves as **unpaired HomeKit accessories**, so `aiohomekit` (the library Home Assistant uses) pairs with them directly with the 8-digit setup code
printed on each device (the label on its side, and in the Leviton app). After that Skippy reads and sets On and Brightness locally; Alexa is no longer in the path.

    ./.venv/Scripts/python homectl.py scan                                       # what is on the network, and what can be paired here
    ./.venv/Scripts/python homectl.py pair <id> 123-45-678 "Living room dimmer" --area "Living room"
    ./.venv/Scripts/python homectl.py list                                       # everything Skippy can control, with its state
    ./.venv/Scripts/python homectl.py say "living room dimmer" on               # try the same code the voice uses

The pairing keys and the names/rooms are in `state/home/home.json` (git-ignored; `SKIPPY_HOME_STATE` moves it). The setup code is used once and never stored.
Once any device is paired the listener uses them automatically: the brain gains `home_devices`, `home_state` and `home_control`.

**Commands land the first time.** Every HomeKit write is read back from the device; a Wi-Fi plug that dozed off and ignored it (the reason he tells Alexa
twice) is asked again on a fresh connection, once, and Skippy says so plainly if it still did not change. Keep-warm reads every switch every 30 s so
its radio stays awake. The proof, on the real plug (it switches it, then leaves it as it was):

    ./.venv/Scripts/python homectl.py prove "theater fan" --rounds 5          # each on/off timed, second tries counted

**Matter** (the open standard: the Nest, the Amazon Basics Matter plug, most new gear) goes through skippy-core's controller
(`core/compose.matter.yml`; set up there with `core/install/matter.sh`). Put `MATTER_URL=ws://skippy-core:5580/ws` in the vault, then:

    ./.venv/Scripts/python homectl.py matter-info                                       # is the controller there, Bluetooth ready
    ./.venv/Scripts/python homectl.py matter-pair 34970112332 "Theater Fan" --area "Theater"   # a NEW device: its 11-digit code, set
                                                                                         # up over Bluetooth with ROOM_WIFI_SSID/PASSWORD
    ./.venv/Scripts/python homectl.py matter-pair <code> "Thermostat" --network          # the Nest: Google Home app > Settings >
                                                                                         # Linked Matter apps & services > Link apps
    ./.venv/Scripts/python homectl.py list                                              # HomeKit and Matter together
    ./.venv/Scripts/python homectl.py identify "kitchen light"                           # make it flash: which one on the wall is it

**Every Leviton on his account, with nothing paired** (2026-10-25, `leviton.py`): with `LEVITON_EMAIL` and `LEVITON_PASSWORD` in the vault,
the hub reaches every Decora Smart Wi-Fi switch through the My Leviton cloud, the same account Google Home and Alexa use (`decora_wifi`,
Home Assistant's library): dimmers become lights, the rest switches, names straight from the app. It goes through Leviton's servers
(about 0.4 s a command, read back), so it is the last backend: a switch also paired locally (HomeKit, Matter) is driven locally.

**Switches already on the Wi-Fi, many at once** (the Leviton D2MSD / D215S, nothing unscrewed): in the My Leviton app update each
to its Matter firmware (that drops its HomeKit link: a switch is paired one way, HomeKit or Matter, never both), then copy each
one's code from Device > Device Settings > Matter Pairing Code into a text file, one switch a line:

    34970112332, Kitchen Light, Kitchen        # code, name, room (commas, tabs or | between; # starts a comment)
    1234-567-8901, Hall Light, Hall

    ./.venv/Scripts/python homectl.py matter-import levitons.txt                          # pairs each over the network (no Bluetooth)

Each line says how it went; one that fails doesn't stop the rest, and running it again skips the ones already done (matter.json
keeps a one-way mark of each code used, never the code). A code times out about 15 minutes after the app shows it: a fresh one
from the app for a failed line. `--new` is for brand-new boxes instead (Bluetooth, given ROOM_WIFI_SSID/PASSWORD). Then
`identify` each name to check it flashes the switch you expect, and `matter-label` to rename one that doesn't. `identify` also
takes a HomeKit id from `scan`, paired or not.

`matter.py` turns each node into lights, switches, a thermostat and, for a switch with a motion sensor (the D2MSD), a read-only
`binary_sensor.<name>_motion` ("Skippy, is anyone in the kitchen?"); the thermostat (spoken to in the unit the thermostat shows: a Nest in Fahrenheit
hears 70). Every on/off is read back from the device and tried once more if it didn't change, as with HomeKit. HomeKit, Matter and Home
Assistant together are one house (`house.Combined`): one set of names, one set of guard rails, and one backend that is down never hides
another's things. Tests: `tests/test_sat_matter.py`, against a fake controller speaking python-matter-server's real message format.

Guard rails (`house.py`, the same for every backend; tests in `tests/test_sat_house*.py`... see `test_sat_hass.py` and `test_sat_homekit.py`):

* only lights, switches, fans, covers, climate, media players, scenes, scripts, vacuums, humidifiers and input booleans can change; sensors can be read;
* locks, alarm panels and garage doors/gates are invisible unless `HASS_SENSITIVE=1`, and then still need a spoken yes (`confirm`); an alarm panel is never driven;
* a spoken name must match one entity (the room counts as part of the name); several matches change nothing and Skippy asks which;
* no raw service calls from the model; pairing keys and tokens are never returned or printed.
* the garage door is never closed while the opener reports an obstruction (the safety beam): Skippy says so instead (`house.control`, and again in `garage.py`).

**The garage door** (`garage.py`, `rooms/garage.yaml`): a ratgdo32 on the LiftMaster's wall-button terminals, reached over ESPHome's API like
the pods (no MyQ, no cloud). It is off until the vault has `GARAGE_ESPHOME=esphome://<ip>`; then it is `cover.garage_door` ("Garage door"),
`light.garage_light`, `binary_sensor.garage_motion`, `binary_sensor.garage_obstruction` and the remotes lockout `lock.garage_remotes`, all in
the garage. It closes itself when the car drives away with it open (`autoclose.py`, `GARAGE_AUTOCLOSE`; the one, close-only exception to the spoken yes). Wiring, flashing and the "when it arrives" checklist: `rooms/README.md` → The garage door. Tests: `tests/test_sat_garage.py`.

Other backends plug into the same `House`: Home Assistant (`hass.py`, if he ever runs one: `HASS_URL` + `HASS_TOKEN` in the vault). Later: the LG webOS TV
(`aiowebostv`), other rooms' satellites over the Wyoming protocol.

## The house's layout (`house_layout.json`, `house_layout.py`)

ONE source of truth for floors, rooms, the names he says for them, and what is in each room (lights, plugs, pods, speakers and their router
outputs, the projector, the computers). Every part of Skippy at home resolves a place through it:
- **Lights:** `house.House` asks the layout for each thing's room (the layout first, then the backend's own room, then a room in its name), so
  "turn on the upstairs lights", "the kitchen lights" or "all the lights" switch every one of them (on, off, toggle, dim); a thing's own name
  still means that one thing ("kitchen under 1", "Bedroom"). `home_devices` takes a floor or "everywhere" as its area.
- **Outputs:** `speaker_out.place_kinds` reads `layout.outputs(...)`: "upstairs" / "living room" / "enclave" is `enclave`, "downstairs" /
  "theater" / "sony" is `hdmi`, "everywhere" is both.
- **Skippy:** `brain.py` puts `layout.primer(room)` in his prompt: the floors, rooms, speakers, and which room he is hearing from.

A new light, room or pod is one edit to `house_layout.json` (re-read when it changes; no restart). `"confirm": true` marks a guess the Captain
has not checked. `SKIPPY_HOUSE_LAYOUT` names another file. Tests: `tests/test_sat_house_layout.py`.

## The news, and things Skippy brings up himself (`news.py`, `starters.py`, `tools/starter_refresh.py`)

"Skippy, what's the news?" "News about what?" (said at once, no model) "Politics." The answer goes straight to the `news` tool: the house's SearXNG
(`http://skippy-core:7360`, news category, the last day, the week when the day is quiet); Core talks him through two or three stories from the snippets
alone. "More on the second one" (or words from its headline) searches that story again; the headlines stay in his head for 15 minutes. "Near me" uses the
town in the vault's `HOME_ADDRESS`.

**The weather** (`weather.py`): "What's the weather like?", "Do I need an umbrella tomorrow?", "How's the surf?" go straight to the `weather` tool
(Core left to itself made up a forecast): the National Weather Service's forecast in words and any alert for home, Open-Meteo's temperature now, and
for the coast NOAA's tide times at the nearest tide station (which way it is going now) and the nearest NDBC buoy that reports waves (wave height and
period, sea temperature, wind; metres and Celsius converted with Mentat's factors). Home is `SKIPPY_HOME_LATLON`, else where the car spends the night
(`state/daily/state.json`); the stations found are kept in `state/weather.json`. Skippy gives the takeaway, a figure or two at most, never a data card.

**Conversation starters:** the task "Skippy starters refresh" (06:40 daily, `tools/starter_refresh.sh`, log `state/starter-refresh.log`) searches each
interest in `filler.TOPICS` and a few world subjects (elections, laws, his town and state), lets Core pick one real new development per subject and word
an opener with no figures (PLATINUM 21), and keeps it only when a strict second reading of the reports supports it. They wait in
`state/starters/candidates.json`. The hub offers one when the room is idle, a voice was heard there in the last 20 minutes but not in the last 45 s, he
hasn't spoken to Skippy for 3 minutes, no music or book plays, 9:00 to 21:00 (and, once the house log has 5 days, his usual hours): 3 a day at most,
2.5 h apart, house-wide. "No" / "not now" ends them for the day, "never" mutes that subject for a month, two unanswered end the day; anything else
takes it up. Every offer and answer: `state/starters/offers.json` and the house log. `SATELLITE_STARTERS=off` stops them.

## Audiobooks, by voice (`audiobooks.py`, `book_voice.py`, `book_out.py`)

"Skippy, read my book." He plays his own **DRM-free** audiobook files; nothing here opens, decrypts or logs in to Audible (an `.aax`/`.aaxc`/`.aa` file in the
folder is noticed and skipped, and Skippy says so when it is all that is there). Syncing an Audible listening position would be a separate step he authenticates
himself; there is no hook built for it yet.

* **Where the files go:** `D:\Aaronudiobooks` (or `SATELLITE_AUDIOBOOKS` in the vault; `off` switches it off). One folder or file per book: m4b, m4a, mp3, ogg,
  opus, flac, aac. Chapters come from the file's chapter markers (ffprobe); a folder of one file per chapter is one chapter per file (natural order, `2` before `10`);
  a file with no markers is cut into quarter-hour parts so "next chapter" still works. Title and author come from the tags, else `Author - Title` in the name, else
  `Author/Title/` folders. The library is rescanned at most every five minutes (cache: `state/audiobooks-library.json`).
* **The place:** `state/audiobooks.json` (per book: place in book seconds, chapter, speed, last played, finished), written atomically at most every 5 s while a book plays
  and at every pause or skip, so a hub restart loses a few seconds. After a restart "read my book" continues from there; a pause of 30 s or more resumes 3 s earlier.
* **What he says:** read my book / continue / play the dragon one (loose title or author), stop / pause, resume, next or previous chapter, back (or forward) thirty seconds,
  where was I, stop in twenty minutes (or at the end of the chapter), read faster / normal speed. These plain commands are caught by `book_voice.pre_route` BEFORE the model
  (Skippy Core's chat model answers "stop" with a joke and never calls a tool): the `audiobook` tool runs first and the model only words the answer in character, with no tools,
  and is told never to read or invent the book. Anything else goes to the model, which has the same `audiobook` tool. "Stop"/"pause" and "resume" mean the book only while a
  book is playing or was the last thing played; otherwise they are Bilby's.
* **Where it plays:** the pod's speaker (the hub's media server turns the file into 16 kHz FLAC from any second at any speed: `/b/<secret>/<token>.flac?s=&t=`, the box never sees a
  path), and skippy-pc's own outputs named in `SATELLITE_OUT` (HDMI, the "Enclave Audio" Bluetooth stereo when it is on), streamed by ffmpeg into sounddevice (`book_out.py`),
  started with the same `SATELLITE_OUT_DELAYS` as his voice. The clock is the player's, so the outputs only ever start, stop and start again from a second.
  Starting a book pauses Bilby's music in that room and the other way round.
* **With his voice:** on the PC outputs the book ducks to 20 % when the name is heard and while Skippy talks, and comes back after the conversation window. The pod's own
  speaker ducks its media under an announcement itself, as it does for music. The outputs line up by the configured start delays, not by measurement (so there can be a
  fraction of a second between the pod and a PC output; tune `SATELLITE_OUT_DELAYS` by ear).
* **The portal's Audiobooks app** (`/audiobooks`, docs/portal/README.md): the browser saves its place into `state/audiobooks-inbox/` (through `services/audiobook-gate`), which `Store` merges
  (newest wins), and the box's Connect check-in carries `state.book`; a `book:` command from the page (`play:<id>[:<s>]`, `pause`, `resume`, `skip:<s>`, `chapter:<±1>`, `speed:<x>`) is run by
  `audiobooks.run_command` on the room's `BookPlayer`.
* Tests: `tests/test_sat_audiobooks.py` (real ffmpeg builds a synthetic multi-chapter m4b and a track-per-chapter folder).

## Alarms and timers: the house's alarm clock (`alarm_clock.py`, `alarms.py`, `timers.py`; the portal's `/alarms`)

One clock for the house, ticking on its own (an alarm rings with every pod off the Wi-Fi). The alarms are one list, `state/alarms/house.json`
(the old per-room files were merged into it once), set by voice in any room or on the portal's **Alarms** tile (the Captain only).
- **Where:** where he said ("upstairs", "everywhere", "in the bedroom", from `house_layout.json`); not said, a 03:00-11:59 alarm rings
  `SATELLITE_WAKE_PLACE` (default `upstairs`: the Enclave, and the bedroom pod once it is in `SATELLITE_ROOMS` and the layout's pods).
- **Ringing:** a chime, then "Good morning, it's 7:00 ...", once a minute, louder each time, until answered; after 3 min (and 10) his phone
  gets a Discord DM through `/home/alert` with the Alarms page; after 30 min it stops and says so.
- **Answering:** any word to any pod ("stop", "snooze": 9 min), or Stop / Snooze on the Alarms page. The first answer wins everywhere.
- **The backup:** the hub syncs everything to the portal (`POST /home/alarms`, every 15 s, every second while ringing). The portal
  (`portal/alarms.py`) messages his phone itself when an alarm's time passes without the hub saying it rang, when the hub goes quiet
  mid-ring, or the evening before when the hub has been silent 15 min. At 21:30 the hub checks the alarm's speaker; the bedtime routine
  says "Your alarm is set for 6:30." or "No alarm is set for tomorrow."
- **Timers** stay per room (the brain sets them) and are rung by the clock too: one whose pod is away is still said upstairs.

## Bilby's house radio: one station, the hubs are its remotes (`radio.py`, `station.py`, `radio_client.py`)

The Captain, 2026-11-01: *"There is one tap for music everywhere, and it's playing the same tune for everyone, like a radio station. The hubs
can change it, or add to it, but they are not the players."* Bilby's music no longer lives in hub.py (every hub restart killed it: 25 on
2026-11-01). It is its own process, `radio.py`, kept running by `tools/radio_run.sh` (the SkippyRadio task: `bash tools/install_radio_startup.sh
--apply`), log `state/radio.log`. Full description: [RADIO.md](RADIO.md).

* **The station** owns the queue, discovery, favorites and the grammar's filters (`station.py`: the old hub music tools, moved whole), the DJ
  (`dj.DjPlayer`), Spotify (local first, an instant fallback), and its state (`state/radio/station.json`): its own restart resumes the same
  track at the same place on the same speakers.
* **One stream, every speaker tuned in:** the radio has its own router with the hub's outputs and the hub's delays (sync.py's alignment): the
  Sony on the HDMI (Windows mixes it with the hub's voice), the Enclave on skippy-core's snapserver source `radio` (:4954, its own snapclient
  `enclave-radio`; PipeWire mixes it with the hub's stream), and pods through the `pods` tap (one live FLAC stream, `/live/<secret>.flac` on
  :47851, that a pod's media player plays from the radio, never the hub). "everywhere / upstairs / downstairs / here / a room" switches which
  speakers play the one station (`"pod:<room>"` for a pod's own speaker); they never play different songs.
* **The hub is a remote:** `hub.music_tools` hands every music tool to the radio (`POST /tool`, with the asking room, so "here" is that room's
  speakers); it keeps only the room's audiobook and where Skippy's voice comes from. The routines' shuffle and stop, Connect commands to a pod
  ("Play on" a pod puts the station in its room) go to the radio too. The station itself checks in to Connect as "House stereo", so the
  portal's players and the car steer it.
* **Ducking:** when Skippy speaks in a room (`Room._woke`, `_say`), that room's speakers duck the station (`POST /duck`, `Router.duck_kinds`),
  the other floors play on.
* **A hub restart never stops or skips the music:** nothing the hub holds plays; a pod tuned in keeps the radio's stream, and a hub that comes
  back finds it playing and leaves it alone. Tests: `tests/test_sat_radio.py`, `tests/test_sat_esphome.py` (the pod as tuner and remote).

## The Enclave on skippy-core, and several outputs in one room (`router.py`, `sync_outputs.py`)

The Enclave Audio speakers hang off **skippy-core's** Bluetooth (core/README.md → The Enclave speakers), not skippy-pc's. To the hub they are
one more router output, `enclave`, whose part is a URL instead of a Windows device name:

    SATELLITE_OUT_ENCLAVE=tcp://skippy-core:4953     (the default)
    SATELLITE_OUT=theater=pod+enclave+hdmi             the Enclave and the Sony together (how they were synced, in the theater)
    SATELLITE_OUT=living=pod+enclave                  today (2026-11-01): the only pod moved up to the living room beside the Enclave, so
                                                      the alarms can be answered there; SATELLITE_ROOM_ALIASES=theater=living keeps what was
                                                      set as "theater" finding it. The Sony stays "downstairs" for music on command: the router
                                                      opens every output SKIPPY_STEREO_OUT and SKIPPY_PLACES name too (router.used_kinds).
                                                      SATELLITE_SYNC_HEARS=hdmi: the sync mic is in the theater and can't hear the Enclave.
    SATELLITE_OUT=theater=pod+enclave>hdmi             `a>b`: a preference: the HDMI plays only while the Enclave isn't open

* **`router.NetStream`** sends the sink's blocks as 48 kHz 16-bit stereo PCM to skippy-core's snapserver, paced by this PC's clock (snapcast keeps
  the speaker on its own clock and absorbs the drift). **`NetWatch`** asks snapserver every 3 s whether a client plays (the speaker is on);
  the output is open only while it does, and a stream that breaks or whose speaker stops goes dead, so the voice moves to the other outputs
  and the next scan reopens it. A network output never makes the router re-read PortAudio's device list.
* **Lining up several outputs in one room:** sync.py lines a PC output up with the pod, or (one voice) learns its latency; it can't tell two
  PC outputs apart. `python services/satellite/sync_outputs.py enclave hdmi` plays a chirp on each alone (4 rounds), times it with the
  sync mic, and saves `align` in `state/audio-sync.json`: each output waits for the slowest. `router.shared()` adds that wait to the
  one-voice cushion for the outputs in use (from each one's measured latency: an output alone waits for nothing, several wait
  for the slowest of them, room or house); restart the hub to use it. Measured 2026-11-01 in the theater: Enclave 297 ms, Sony (HDMI) 150 ms, so the HDMI waits
  147 ms, and a clip on both is heard as one copy. The numbers are each output's own latency, so they stay right when the Enclave moves
  to another room (a whole-house sound lines up with them); measure again when a speaker or its connection changes. Run it while the hub
  isn't sending to the Enclave (snapserver takes one sender), e.g. right after stopping the hub.
* The pod's own speaker isn't in it (its sound goes through ESPHome's media player, not the router). The house radio's music reaches a pod
  through its own `pods` tap instead (above).
* **Places on command** (2026-11-01): "everywhere" (both), "upstairs" / "living room" / "Enclave" (the Enclave), "downstairs" / "theater" /
  "Sony" (skippy-pc's HDMI). `play_music` takes `speaker: everywhere|downstairs|upstairs`, and the `speakers` tool moves the music (what plays now
  included: `Router.retarget`) or Skippy's own voice. The words come from `house_layout.json` when it is there (else `SKIPPY_PLACES`,
  default `downstairs=hdmi;upstairs=enclave`). Skippy says only where the sound really goes: a place whose speaker is off is named as off.
* **His music grammar** (2026-11-01): wake + function (music / Bilby) + mode + filters + location, every part but the function optional:
  "bilby discovery everywhere", "play favorite music upstairs", "tell bilby to shuffle new darkwave songs", "play goth songs downstairs on
  discovery mode". `brain.music_grammar` parses it straight to `play_music` (mode, genre, playlist, artist, album, song, new, speaker), no
  model in between and never a confirmation question; `mixes.resolve` queues it: discovery is the car's Bilby Shuffle (`drive-pure.js
  buildQueue`: everything, favorites about one in four, Spotify suggestions), favorites is his favorites + Spotify liked, "new" the most
  recently added; genres by family from the files' tags (goth covers gothic rock, deathrock, post-punk ...); playlists are Traktor's
  (collection.nml) or Spotify's, and a Spotify row he owns plays from Bilby. A filter that matches nothing is said in one line and the
  closest plays.
  **Every place said is taken out of the search words** (2026-11-04: "play heavy metal music upstairs on the enclave" took out only
  "upstairs", searched the library for "heavy metal on enclave", found nothing and played a random shuffle while Skippy said the library
  was empty of metal): `brain._take_places` removes each place phrase, with "stereo / speakers" beside it ("the upstairs stereo"); two
  different places are everywhere; "on the stereo" is where it plays now; "house" alone is a genre. Also dropped: lead-ins ("make sure you",
  "I want to hear", "can you"), time and "for the maid" tails ("today", "tonight"), and a second sentence that only explains ("The maid is
  coming over ..."; `brain.lead_clause`). A bare genre ("play heavy metal") needs no "music" after it. `station.clean_what` does the same for
  a free `what` the model sends ("heavy metal on enclave": place and time out, the place becomes the speaker) after a plain search finds nothing.
* **"Play it on the upstairs stereo"** (2026-11-04): "it", "this", "that" (or "the music" with move / send / switch ...) plus a place is the
  `speakers` tool (`brain.speakers_route`), run before the model answers; with play / put and nothing playing, the library starts there
  (`play` flag, `station.run`). Before this the model heard it, said "Playing ... *turns an imaginary dial*" and called no tool.
* **A claim with no tool** (2026-11-04): when he asked for music (a command, not "do you like music?") and the reply says it played, moved
  or turned something but no music tool ran, the model is asked again with the tools (`brain.MUSIC_CLAIMED`, `ASK_AGAIN_MUSIC`); still
  nothing, and he hears that it did not happen. **To tell what really ran, read `state/radio.log`**: every music tool call the hubs make is
  there as `[radio] <room> <tool> {args} -> <answer>`; a spoken "Playing ..." with no such line was a claim, not an action.
* **Announcements upstairs** (`SATELLITE_ANNOUNCE_OUT=enclave`, 2026-11-01): outputs in a place with no pod and no microphone play every
  room's announcements (the morning and bedtime routines, timers, messages typed in the portal's Home chat), never a conversation's
  replies. sync.py never measures them (no mic hears them), so they are never taken for off; they get the one-voice cushion and, when
  other outputs play too, the house-wide wait for the slowest. `speaker_out.announce_outputs`, `hub.Room._out`.

## Bilby's DJ mode on the house radio (`dj.py`)

His library mixed like a DJ set, from his own Traktor work, all on skippy-pc. The house radio's player (`stereo.StereoMusic`, made by
radio.py) is a `dj.DjPlayer`: the queue plays as one gapless chain on the router's `media` layer (Skippy still ducks it).

* **Analysis:** BPM, the beat grid's anchor and the key come from Traktor's `collection.nml` (music-collection's `corrected_traktor/`; 98% of the
  library, indexed once into `state/dj/traktor-index.json`); a track Traktor never saw is analysed once with librosa (`state/dj/analysis.json`).
* **The mix:** the outgoing track leaves on a 32-beat phrase (else 16, else 8) that still has music after it; the incoming one comes in on its first
  beat, time-stretched to the outgoing tempo with ffmpeg's rubberband (pitch kept), both grids checked against the kick drum (moved only when the
  audio is clear, up to 150 ms: Traktor's autocue anchors are sometimes ~70 ms out on live drums), 16 beats together with a bass swap at halfway,
  then a 16-beat glide back to the incoming track's own tempo. The crossing is rendered (about 2 s) while the outgoing track plays.
* **A plain cut** (one ends, the next starts, still gapless) when the keys clash (Camelot distance 3+), the tempos are more than 6% apart (half and
  double time count), a track isn't on this PC, or the next item is Spotify (never mixed).
* **Order:** a shuffle in DJ mode is `dj.walk`: each next track mixes with the last, his higher-rated tracks a little more often.
* **Control:** `BILBY_DJ=on|off` in the vault; "Skippy, DJ mode on/off" (`dj_mode` tool) overrides it in `state/dj.json`, from the next change of
  track. "Skippy, play Depeche Mode on the stereo" (`play_music` with `speaker: stereo`) puts Bilby on the Sony.
* **By hand:** `python dj.py plan "<catalog path A>" "<B>"` says what the mix would do; `python dj.py demo "<A>" "<B>"` plays the last bars of A
  into B on the HDMI output (the hub may keep running).
* The browser player is unchanged. Tests: `tests/test_sat_dj.py` (synthetic click tracks: B's beats land within 8 ms of A's grid).

Tests: `python -m pytest tests` from this folder (its own session: other services have a `gate.py` too).
