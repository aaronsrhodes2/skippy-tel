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

**Commands after the name need no shouting (the Captain, 2026-11-05: "Skippy hears his wake word well, but the following commands I have to shout"):** the
spotter fires on a soft voice, but the voice detector's bar (`vad.Vad`) is an absolute loudness, a multiple of the room's quiet. When the name is heard,
`Vad.arm(voice)` lowers the bar for the first words of the command to 35% of how loud the name was (never below 1.6x the room's quiet or 1.2x
`SATELLITE_FLOOR`, never above the normal bar); a dip between syllables keeps half of what was counted; while armed the room's quiet is learned downward
only (a soft voice under the bar cannot raise it; the Enclave ducking under the name lowers it). A buffer seeded when the name was heard no longer keeps the
window open for ever when the detector never hears him begin (`Room._tick`). Every command and every window that closes empty now writes one line to
`state/hub.log` with the numbers: `(mic: a voice at 0.081, the bar was 0.024, the room 0.015)` / `(mic: nothing said; loudest 0.018, the bar was 0.024 ...)`.
If he still has to shout, those lines say by how much. What Whisper writes for noise ("Thanks for watching!") is never taken as a command.

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

**The box's own speaker is the last resort** (the Captain, 2026-11-04: "we never want to output to the hub speakers unless that house level's stereo is
unavailable"). A room's stereo (the Enclave upstairs, the Sony downstairs) is its voice; the box is the microphone, the face and the lips, and its speaker
sounds only when none of its room's stereos is on. So `SATELLITE_OUT=living=pod+enclave` does not mean both: `pod` there says the box may be the fallback.
Replies, the filler, timers, routines and Home-chat words (`Room._out`), alarms and reminders (`AlarmClock.ring`), and the announce tool to other rooms all
follow it (`Room.stereo_kinds()` is the one test: the room's own outputs from the vault that are open and not taken for off, a look that spends no probe).
An alarm: the stereo rings every time it is on; the box stays silent for two rings and joins it at ring `POD_JOINS_AT_RING` (a reminder rings twice, so its
box joins on the second), because the software cannot see the stereo's own switch, knob or input. A silent box shows the words but stays busy and deaf until
the stereo's ring is over plus `LISTEN_AFTER_S` (its microphone would hear "Say stop" and stop the alarm itself), then opens the "stop" window as after any
ring; if the stereo's ring failed (nothing sounded, an error, over `RING_WAIT_S`) it rings after all (`Room.ring(quiet=, wait=)`). Music: "here" is the room's
place (`house_layout.json`: the living room is the Enclave), and a pod is a station speaker (`pod:<room>`) only in a room with no stereo; with the stereo off
the station says "Upstairs isn't on, so not there" and does not move to the box.
The firmware plays no sound of its own (`rooms/skippy-room.yaml`). The vault's room name and the layout's `pods` must agree (`SATELLITE_ROOMS=living=...`,
`pods: ["living"]`): with the vault saying `theater` while the box stood upstairs (2026-11-02 to 11-04), "here" and the voice went to the Sony.

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
* locks, alarm panels and garage doors/gates are invisible unless `HASS_SENSITIVE=1`, and then still need a spoken yes (`confirm`) that only he gives: `Brain.run_tool`
  drops the model's own `confirm` from every `home_control` call (routes, second tries and calls written as text all come through it) unless his words now are a plain yes
  (`says_yes`) to the house's own "needs him to confirm out loud first" (`CONFIRM_ASKED`) about the same thing and action, within `CONFIRM_FOR_S` = 120 s of that exchange;
  one yes opens one door, once. A yes is up to ten words, all of them yes-words ("yes", "yeah, go ahead", "do it"), the fillers Whisper writes for a hesitation (uh, um,
  er, erm, hmm, well, oh) or the words of the thing asked about; "yeah right", "yeah, sure" and "sure, sure" are not one (usually sarcasm: a false no costs one more word,
  a false yes opens the garage). The call must name the thing he was asked about with all of its words (`_same_thing`: "garage door" for "garage" counts, "door" for
  "front door" does not, because the house settles a vague name by itself and his yes was for what it asked), so a model that shortens the name costs him one more yes
  (none of 13 real two-turn flows did). Only the HOUSE's question counts: if the model asks in its own words ("Shall I open the garage?") and he says yes, that yes opens
  nothing and the house asks again. On the real Core and a stub house that shows the garage and the locks, the model wrote `confirm=true` itself on its first call for 11
  of 12 one-utterance orders; now 0 of 18 reach the house, and the two-turn flow (order, the house asks, "yes") opens it 6 of 6; an alarm panel is never driven;
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
- **Skippy:** `brain.py` puts the floors, rooms and speakers (`layout.primer("")`) in his system prompt, one static text in every room (`Brain.system()`); the room he is
  hearing from rides in `Brain.context()`, a note at the start of his newest message that the memory never keeps (see Snappy like an Echo, below).

A new light, room or pod is one edit to `house_layout.json` (re-read when it changes; no restart). `"confirm": true` marks a guess the Captain
has not checked. `SKIPPY_HOUSE_LAYOUT` names another file. Tests: `tests/test_sat_house_layout.py`.

## Snappy like an Echo (2026-11-05)

The Captain, 2026-11-05: *"streamline it so it becomes snappy like Amazon Echo."* That day a turn took a median 8 s (90th percentile 24 s) from the end of his voice to
Skippy's voice being ready, mostly Skippy Core re-reading its prompt, plus the voice studio speaking each reply from scratch. Core's chat model (llama.cpp, a hybrid)
reuses a cached prefix only from a checkpoint about 500 tokens before the end of the last prompt, so a word that changed in the middle (the minute, the room, what
plays) made every turn read all of it again. The house prompt with its tools is a 10.5k-token prefix (5.6k without the tools; Core keeps a cache per shape; about 800 tokens fewer since the persona was cut): 0.6 to 2.5
s to read from the cache, 20 to 33 s from scratch. Everything below keeps that prefix identical, warm and short, or skips the model.

* **One prompt all day: `Brain.system()` and `Brain.context()`.** `system()` is the same text at every minute in every room: the persona, `SPOKEN`, the layout's floors,
  rooms and speakers (not where he is), `BILBY`, `ABILITIES` (the real tool names) and the news, weather, car and Mentat prompts. What changes rides in `context()`: ONE
  bracketed note at the start of his newest message (`_user_turn`) with the clock, the room he is talking from, `Brain.live()` (what the house audio plays and where),
  the headlines and the starter he was just given. The prompt says the note is not something he said, `Brain._spoken` cuts a copy of it out of a reply (`NOTE_ECHO`),
  and the memory never keeps it, so consecutive turns share everything before it.
  The persona is the file shared with flask-api minus what the house cannot use (`house_persona()`): the examples whose answer says "I cannot" (they taught the denial)
  and, by the Captain's call of 2026-11-06, the sections that teach `[SKIPPY_LEARN: ...]` and `[TTS_ADJUST: ...]` (found by heading, not by line: the file is 13,392
  characters, 13,003 without the examples, 9,851 without the sections, about 800 tokens fewer in every prompt). Of 18 provoked replies on the real Core ("remember that
  the throne light is the bedroom lamp", "your voice is too fast" ...) 11 carried an annotation and 6 were nothing else, so he heard "Hm. I had nothing to say, which is
  unlike me."; now 0 and 0. With those sections gone a request about his own voice had no outlet, and on the real Core 1 of 9 "your voice is too fast, slow it down" called the
  audiobook tool's `speed` and changed the book player: `SPOKEN` now says that the house's own voice (speed, pitch, loudness) is not something a tool changes (say so in a few words, call no tool) and the tool's `speed` says it is
  the BOOK's. About half of the provoked replies still promise to remember (see Left undone).

* **`Brain.warm()`** reads that prefix into Core's cache before a turn needs it: the model, the system text, the tools, the memory as it stands (`_prefix()`, the very
  messages the next reply sends), a placeholder for his words (`WARM_WORDS`) and ONE token of answer; then the same again without the tools, the shape of a routed
  command's wording call. It returns the seconds it took, or None (a reply or another warm is running, or the model is away), never raises, and counts for nothing (not
  in `actions`, `timing`, `history` or the quiet clock). A hot prompt warms in 1.5 to 2.5 s, a cold one in 13 to 30 s. A reply that begins while a warm runs waits for
  it (`WARM_WAIT_S`, 45 s) rather than race it (racing made both read the prompt at half speed: 40 s against 21 s alone); the wait is logged as `warm_wait_s`.

* **The hub's `SpeedKeeper`** runs it: at the hub's start (ten tries, 5 s apart doubling to a minute: `WARM_START_TRIES`, `WARM_START_BACKOFF_S`), then for any brain
  nobody has used or warmed for `WARM_IDLE_S` (240 s, looked at every `WARM_CHECK_S` = 15 s; other clients of Core's chat slot evict the prefix), never during a turn,
  while another warm runs, or while Core is taken for away (`WARM_AWAY_S`, 60 s after a failed warm or turn). It also renders the brain's fixed phrases (below) ahead,
  one at a time and only in a quiet house, into the `ClipCache` that `Room._voice` reads first. `SATELLITE_SPEED=off` (or 0, no, false) in the vault switches the keeper
  off: nothing warmed or rendered ahead, no `prompt` or `cached` in the log. **No warm when the name is heard:** it was built, measured and taken out. With the prompt
  hot it gained 0.07 s of a 2.2 s turn (15 turns each way); with the prompt cold, or evicted since the keeper last saw it hot, it doubled the turn (20.7 s alone, 40 s
  with a warm started 3 s before: llama.cpp does not share a prefill that is in flight).

* **A closed bank of confirmations: a routed tool that plainly worked costs no second model call.** The reply is a phrase of `ACKS` ("Done.", "Got it.", "Done, monkey."
  ...), `TRANSPORT_ACKS` (pause, resume, next, previous), `CANCEL_ACKS`, `volume_ack(n)` ("Volume seven.") or `timer_ack(seconds)` ("Timer set for five minutes."),
  taken in turn, never at random (`Brain._ack`). `Brain._confirmation` accepts only a tool's exact plain success (`set_timer`, `cancel_timers`, `home_control` on or
  off, `music` pause / resume / next / previous and louder / quieter / volume); a failure, a partial success or a note goes to the model as before, and a phrase says
  "done", never what was done. `fixed_phrases()` lists the bank (97 phrases, 26 without the 71 timer lengths in `COMMON_TIMERS`) for the keeper to render ahead; a timer
  of another length is rendered the first time it is said.

* **Routes before the model** (`Brain._reply`, after the audiobook, news, car, Mentat and thermostat routes). `timer_route` sets ("set a pasta timer for ten minutes",
  "a ten minute timer") and cancels ("cancel all timers", "cancel the pasta timer", "cancel the five minute timer": a spoken length becomes the label an unnamed timer
  gets, `timers.length_label`, "5 minutes"); a bare "stop the timer" may be the ringing one and stays the model's. `light_route` switches a light, plug or fan on or off
  by name ("kitchen under 1"), by place and kind ("the kitchen lights"), "all the lights", or a bare "turn on the lights" in the room he is in; `Brain._light_pre_route`
  lets it stand only when the house itself would settle on the name (`_light_resolves`), and "turn off the sony" stays a speaker's switch. **It never touches
  `RISKY_THING`:** what `RISKY` covers (locks, doors and gates, the garage, alarm systems, ovens, burners, fireplaces, grills, irons, dryers), heaters and kettles, what
  keeps a house running (routers and servers, UPS and breakers, fridges and freezers, pumps, cameras, medical gear), and a plug or fan with such a name (a light is a
  light whatever it is called). Dimming, music, timer and alarm words, the thermostat, groups of plugs or fans, a name that fits two things and idle talk are the
  model's, and so is anything that goes wrong inside a route. The tool runs first; the model only words its answer, with no tools on offer (`WORDING_TOKENS`), unless
  the bank answers.

* **The thermostat route** (`house.thermostat_route`, before the weather route). "Set the thermostat to 70", "set the temperature to seventy two", "make the thermostat
  70" and "make it 70 degrees in here" go straight to `home_control` `set_temperature` (`SET_THERMO`; `SET_TEMPERATURE` and `SET_IT` when no device is named; `DEGREES` is
  two digits or twenty to ninety-nine in words, and "make it N in here" counts only from 55 to 85, so a volume is never a setting). A setting that names no device stands
  only as a plain command: a question or condition word, a clock time or a second command (`NOT_NOW`), a choice ("68 or 70") or a change ("up 10 degrees") is the model's.
  A water heater and a space heater are no thermostat, and "what's the house temperature" reads the thermostat (`ASK_THERMO`). `weather.pre_route` leaves a command or a
  plain statement about a temperature ("set the oven temperature to 350", "the water temperature is 60", "turn the temperature up") to the model instead of answering with
  the forecast (`TEMPERATURE_COMMAND`, `TEMPERATURE_STATEMENT`; a statement that also has another weather word in it (rain, a forecast) stays the weather's). A table of
  80 phrases, half thermostat commands and questions and half weather questions and unrelated talk, through the real brain: 45 routed right before, 80 now (every weather
  question still goes to the weather: 20 of 20).

* **The memory is whole exchanges with the real tool calls in them** (`_remember`, `_window`, `_keep`). A model shown only words in its history answers the next command
  in words: "it said Done and did nothing". Each exchange now keeps the call that ran and what it answered (`tool_use` / `tool_result`: the last `TRACE_CALLS` = 4
  calls, answers cut to `TRACE_CHARS` = 300), `MEMORY_TURNS` = 8 exchanges at most, all forgotten after `FORGET_AFTER_S` = 600 s of quiet. Every call that offers tools
  starts with `EXAMPLE` (while the house tool is here): one constant exchange of a tool doing what he asked ("turn the kitchen fan on", `home_control`, "Done."), part
  of the cached prefix and the model's own reading of how a command is done. `Brain.history` is the plain-words view of the same exchanges (`plain_words`) for
  everything that reads the memory (musicguard, the news route). The window slides in blocks, not by one (`MEMORY_BLOCK` = 4; 1 slides by one again): the 9th exchange
  drops the oldest four, so 5 to 8 are held and between slides each turn's prompt is the last one's plus the new exchange (a model turn in a 14-turn talk: median 1.72 s
  and 122 fresh tokens, against 2.78 s and 667 sliding by one).

* **A call that offers no tools is shown plain words** (`plain_words`, applied by `Brain._shape` in `_create` for every call and in `warm()`'s second request): no
  `tool_use` / `tool_result` block, no `EXAMPLE`. Shown them with no tool to call, Skippy Core answers in its own tool-call XML (`<tool_call><function=weather>...`)
  instead of words: 32 of 56 trials on the real Core (after four routed commands, 6 of 8 weather answers and 6 of 6 "play some jazz"), 0 of 117 with the plain view. A
  routed answer left with no words says the tool's own short answer (`own_answer`: one line, at most 160 characters, no brackets), else `UNWORDED` / `TRIED`.

* **What a model writes that is not words is cut, and never spoken or kept** (`strip_model_markup(words, names)`, which `Brain._spoken` runs on every reply of the brain):
  Core's tool-call XML (nothing runs it) and the `[IMAGE: ...]` sentinel (`TOOL_MARKUP`), its reasoning when the server leaves it in (`THINK`, `THINK_CLOSE`,
  `THINK_OPEN`: a block, a lone `</think>` with all before it, an unclosed `<think>` with all after it), a copy of the clock note, whole or cut off (`NOTE_ECHO`,
  `NOTE_CUT`), and the persona's `[SKIPPY_LEARN ...]` / `[TTS_ADJUST ...]` annotations (`ANNOTATION`: whole, with no JSON, or cut off by the token limit, from its bracket
  to the end). A reply the token limit cut inside the name of a tag (`[SKIPPY_LE`, `<func`) or at the start of the clock note (`[Right no`) loses that stub (`CUT_TAGS`,
  `CUT_XML`, `NOTE_STUB`; so does one that really ends in `<p`). It runs BEFORE the cut-back to a whole sentence (`whole_sentences`), which used to land inside a cut
  annotation and speak the rest of it; 8 of 89 worded replies from the real Core carried one of these, and none reaches speech now. Every pattern ends in an alternative
  for the end of the text, so unclosed tags cost no more than closed ones (`'<think>x'` twenty thousand times took 12 s, now 3 ms). The routines' words get the same
  cleaning (`Daily.word`, which has no tool names to pass; its `WORDING` also tells the model not to write the two annotations). Core's other ways of writing a call as
  words (a code fence, `[call: ...]`, `[Calling ...]`) are not markup it knows: see the text-call bullet.

* **A call written inside the reasoning is put back** (`_reasoned_calls`, in `strip_model_markup`). For "turn the music down a notch" the real Core wrote `[music]` and a
  line `music(action='quieter')` inside its reasoning (1 of 16), where the text-call rescue below could not see it. Now a call found in the cut reasoning is put back in
  front of the words, one per line, and the rescue reads it as if it stood there. Only a bracket that starts with the name of a tool the brain has is put back ("[sighs]"
  and "[speak]" are not), with, when nothing is inside it, the rest of its line and up to two lines after it that look like arguments ("action: next", one bare action
  word), never words; each call once, and none that the visible words hold themselves; and never one that undoes or announces (`RETRY_NEVER`, a list's `remove` /
  `clear`), because a call the model only CONSIDERED must not cancel an alarm, clear a list or speak in every room. `Brain._spoken` passes the tool names only while no
  tool has run in the reply, so the wording of a routed answer, Mentat's and a call that ran get no calls back. Replayed offline, the real reasoning-wrapped replies of
  the reviews ran the call in 1 of 7 cases before and 7 of 7 now, once each (no live reply carried reasoning on the day it was fixed: 0 of 51).
  **The words after the reasoning are the model's last word** (review4): nothing is put back when they hold a call of their own (the reasoning's was a draft), deny, take it back
  (`_RETRACTS`) or only ask about it (every sentence of them is a question or a retraction, `_takes_back`: "Which speaker, Captain?", "Sorry, I did not do anything."; a tag question
  or a dig that comes with a claim, "There. Quieter. Happy now?", "Sorry, my magnificence was too loud. Quieter now.", stands by the call: 12 of 13 such replies dropped it
  before the fifth review), and no call that a negation just before it rules out, the typographic apostrophe too (`_NOT_MEANT`: "nothing is playing, so I should not call [music action=next]"); a
  `home_control` call on a `RISKY_THING` target or a group of plugs is never put back (a lock, a door and the garage still are: the house asks for the yes aloud), and three
  calls at most. `Brain._spoken(resp, restore=...)` puts a call back only where the text-call rescue goes on to read these words: the first reply and the second tries that
  read text calls (a denial's, the backstop's, a claim's, `TEXT_AGAIN`), not the music re-ask and not the wording calls, where the lines of such a call ("action: next") would be spoken.

* **The bare-acknowledgement backstop** (`Brain._acknowledges`): a reply that is only "Done." with no tool called is asked again, behind a gate. It fires only when his
  words are an ORDER in shape (`order_shaped`: a verb first, can / could / would you + a verb, I want / I need, please, the curt "lights off in the bedroom") for something
  a tool does (`ORDER_CUES`), not a question (`NOT_AN_ORDER`) or an ability question (`ASKS_ABILITY`), and about nothing that can hurt or has to keep running
  (`RISKY_THING`). A statement that only sounds like an order is none: it defers or describes (`ORDER_NEGATED`: "someday", "usually", "makes"), wishes for what no tool does
  (`ORDER_TO_OTHER`, `_wish_off_topic`: "we want a new stereo"), asks whether it can (`_asks_ability`: "can you set timers"), puts the verb of a statement right after its
  first words (`_statement_after`: "turn signals are on the left", "play time is over"), or is an idiom ("cut costs"). Statements that fired it fell from 32 of 60 to 0 of
  60 (a list written before the rules and used to tune them) and from 26 of 40 to 2 of 40 (a second list, written after the rules and partly tuned on). The spoken reply
  (stage directions out) is at most `BARE_WORDS` = 14 words, or `BARE_NAMING_WORDS` = 25 when it names the thing he ordered; it asks nothing, takes nothing back (`HONEST`),
  and claims what no tool did: an acknowledgement (`BARE_ACK`: "Done.", or the order's own verb said as done, "Added, Captain.") as its first or last sentence, a sentence
  of the closed bank (`bank_phrase`: "Paused.", "Volume five."), a plain claim of what the order's own tool does (`DONE_CLAIMS`: the lights' `STATE_CLAIM`, "Setting a timer
  for ten minutes."; an offer, "I can set a timer", is none) or "you're welcome" (`ACK_TAIL`); all 32 false acknowledgements of the review get a second try, 27 did before.
  For an order for the lights `musicguard.lights_order` stops the music guard adding "I have not played, moved or changed any music." The house log shows `guard reask ack`.

* **What a second try may do** (`retry_may`, `retry_tools`, `Brain._run_call`). Four second tries are gated alike: after a bare acknowledgement (above), after a denial
  ("I cannot ..." with no tool: `Brain._second_try_denied`), after a claim with no tool ("Alarm set for seven." and nothing set: `ASK_AGAIN`) and after a tool call
  written as text that could not be read (`TEXT_AGAIN`, `Brain._asked_again`; it takes the denial's test for an order, `not NOT_AN_ORDER`, because "a ninety second tea
  timer" has no verb and is one). Before, `[cancel_alarm] Noted.` was followed by a call of `cancel_alarm` that cancelled both alarms, and 230 of 21,600 random replies
  broke the rules below, every one on this path; now none. Each is shown the WHOLE tool list, the one the turn's own calls used (Core caches per tool list: the denial's
  cut menu was read from scratch, a median 43.6 s against 3.3 s for the whole list on the real Core, about 0.5 s more when the cut menu happens to be cached), and what it
  may DO is decided where a call runs. It may always look (`RETRY_READS`, `RETRY_READ_ACTIONS`). It may change something only when his words are no question
  (`NOT_AN_ORDER`) and, after a claim, an order in shape too (before, a claimed "Alarm cancelled." to "I do not need the alarm on sunday" let the second try call
  `cancel_alarm` with nothing named, which cancels both alarms). It never cancels, clears or announces on its own (`RETRY_NEVER`, `RETRY_NEVER_ACTIONS`), never uses the
  model's `confirm`, and never switches a `RISKY_THING` target the house does not guard itself (the router, the fridge, a pump, the oven; it sees only the name, so a
  light called "security light" is refused too) or a whole set of plugs (`RETRY_GROUPS`). A lock, a door or the garage runs without `confirm`, so the house asks him for
  the yes. A call it writes as text passes the same gate, a refused one is answered `NOT_DONE`, and the nudges of the first two name only the tools it may use. A denial
  to words that name anything in `RISKY_THING` stands: no second try. The second try for a music claim (`REASK_MUSIC`) is the one that has no such gate (Left undone).
  **What a second try SAYS is judged too** (`Brain._second_try`, `_unsound`, `_honest`, `announces`). Its words first lose any call written as words (`_wipe_calls`: a
  code fence, `[call: ...]`, `[Calling ...]`, `music(action='quieter')`), and nothing left is the fixed line (`UNCLAIM`). If a tool that CHANGES something ran, the words
  stand. If only looks ran, or a change was refused, they must not claim it was done: right after a refusal (`NOT_DONE`) with no look since, they must own it (`_honest`:
  a retraction, a denial or a question, and no promise), and after a look they must not promise or claim in the first person (a read answer is full of "the alarm is
  set"). So a refusal, a look and "The Sunday alarm is at 09:00. I've cancelled it for you." (1 of 8 samples on the real Core), or "Right, my mistake. Let me cancel that
  timer for you." (an apology that promises, `announces`: 3 of about 35 real second tries) get the fixed line (after a denial, the first reply stands), and a look that
  nobody worded no longer makes the first claim true. Of 40 dishonest replies written for the rules 4 were replaced before and all 40 are now, and all 40 honest ones are
  still spoken; of 6,000 random second tries 894 spoke a lie or a read answer right after a refusal, now none. A question promises and claims nothing ("Which alarm should I
  cancel?" is an honest second try; before, 24 of 109 honest questions were thrown away for the fixed line; but a question that OPENS with the claim or the promise still claims,
  "Done, happy now?", "I've cancelled it, okay?", `_CLAIM_OPENS`: 6 of 6 such second tries were spoken with nothing changed), "all set" claims only when it opens a sentence ("You're all
  set." claims nothing), and a sentence that is only "Cancelled." claims like "Done." ("Right. Cancelled. What alarm was it?" was spoken with both alarms still set).

* **A tool call written as text** (`Brain._text_calls`): `[list action="read" list="shopping"]`, `[music with action=quieter]`, `[music]next[/music]` or `[music]`
  followed by a line `action: next` is read (`TEXT_CALL`, `_bare_action`, `_call_lines`) and run as the call it meant (inside a second try, behind its gate), and the
  model words the answer. Only the arguments the tool really has are kept (`Brain._tool_props`: `[music mode=weird]` is a call with none). A call that needs arguments and
  has none (`_needs_arguments`; `cancel_alarm`, `cancel_timers` and `announce` always) is never run empty; a pile of brackets runs nothing; the same call twice runs once;
  one that cannot be read is asked again with the nudge joined to his message (`TEXT_AGAIN`: shown its own bracket as its last turn the model wrote it again, 9 of 9; that
  second try is behind the gate of the others). So is a bracket that starts with a TOOL's name and that `TEXT_CALL` cannot read at all (`[set_timer 90 tea] Ninety
  seconds.`: `Brain._tool_brackets`, `Brain._asked_again`), because the words with it claim what nothing did. A name that is also an English word counts only when a
  colon, `=`, a parenthesis or a digit follows it inside the bracket, or when nothing follows it (a name with an underscore, `[play_music jazz]`, always counts): `[music
  swells]`, `[announce proudly]`, `[speakers hum]` and `[list of reasons]` are stage directions, wiped, with no second try (before, `[music swells] It is a quarter to
  twelve.` cost a second model call and the fixed line replaced the answer). A bracket with one of that tool's own action words in it, or the word `action`, is a call
  written in plain words and counts too (`[music louder]`, `[music with quieter]`, `[music action quieter]`, `[list add milk]`: 6 of 43 real replies to "turn the music down a
  notch" were of this kind, and with the colon-or-digit rule alone they were wiped as directions and the volume stayed: `Brain._says_an_action`). An action word alone (`[pause]`, `[volume 5]`) is only wiped too, and inside a second try the
  calls are dropped and the words stand. A bracket that starts with a tool or action name is never spoken (`_wipe_calls`), and nor is any other way Core writes a call as
  words: a code fence, `[call: ...]`, `[Calling ...]`, `[tool_call ...]` (`CALL_DIALECT`, `strip_call_dialects`; the real Core wrote the fence for "turn everything off")
  and `music(action='quieter')`. Where no tools are offered (a routed answer's wording, Mentat's) `Brain._phantom` cuts a bracket of ANY tool the brain has
  (`phantom_pattern`; `PHANTOM` itself knows only `ask_mentat` and `car_status`), and both then run `_wipe_calls`, so they cut the dialects too. A wiper takes a bracket together
  with the argument lines under it (`_cut_calls`, `_call_extent`: `[music]` + `action: next`, + `next`, + `/music`; a plain word goes only when it is one of the tools' actions,
  so "Okay." stays), because a call put back from the reasoning left those lines to be spoken and kept. `_phantom` takes them too (`_cut_calls(..., gone="")`): it used to remove the
  bracket first, and `action: next` under it was spoken and kept (16 of 275 real replies through the routed wording, 1 now).

* **Short replies.** The prompt asks for one or two short sentences, a few words once a tool has done it. `SPEAK_TOKENS` = 300 is the room of a round that may call a
  tool, `WORDING_TOKENS` = 160 of the model wording a tool's answer (`WORDING_ROOM`: the book's place 200, the headlines 300), and a reply that ran out of room is cut
  back to its last whole sentence (`whole_sentences`). `strip_stage_directions` deletes `*sighs*` and `[laughs]` (the voice reads them out; four of seven baseline
  replies had one) but keeps a stressed word (`Do *not* open it`) and a title (`Now playing *Dancing Queen*`: `_titled`, two or more capitalised words, the first one too,
  so `*the Rolling Beat*` is a direction and `*The Rolling Beat*` a title). A span of two or more words whose first word ends in -s, -ing or -ed is an action unless that
  word only looks like a verb (`_NOT_ACTIONS`: this, is, plus, always, morning, nothing ...). Its two gaps are under Left undone.

* **The filler rule** (`Room._cover_the_wait`: the recorded quips that cover a slow answer). With Core's prompt believed hot (`SpeedKeeper.core_state`: a warm or a turn
  worked in the last `WARM_HOT_S` = 300 s) a model turn takes 1.5 to 4.4 s (median 2.1 s; cold, 15 to 20 s) and a quip at 1 s only sat in front of the answer (the
  recorded quips last 0.5 to 7.8 s, median 2.1 s). So the first quip waits `max(SATELLITE_FILLER_S, HOT_FILLER_S)` = 4 s (8.8 s for a household command: the old factor
  2.2); a cold, warming or unknown prompt keeps `SATELLITE_FILLER_S` (1 s). After the first quip the wait goes on the same way whatever the belief: 5 s more, then a
  second quip if the answer is still not there (the belief goes stale when another client evicts the cache, and that turn takes 20 s).

* **Whole-reply voice.** The hub asks the voice gate for `"whole": true` on every line (`voicegate.Gate.tts(text, whole=True)`; the gate passes it to tts-studio): a
  reply that fits one F5 batch (192 characters for this reference) is ONE run instead of one per sentence, 0.8 to 1.1 s faster for 2 to 3 sentences in a busy stretch
  (0.35 s in a quiet one), the same length of audio (`services/tts-studio/README.md`). It moves where the pauses between sentences fall, so the Captain's ear decides.
  The way back is one line: `whole: bool = True` -> `False` in `Gate.tts`; the request then leaves `"whole"` out and the studio speaks sentence by sentence, its default
  (`TTS_WHOLE=1` in the voice gate's environment asks for it on every line from every client).

**Reading a turn in the log.** Every spoken turn writes one line to `state/hub.log` when its voice is ready, as `[room] (timing: ...)` (the same numbers are the
`timing` of the command record in the house log, `skippy-home/log/<date>.jsonl`). The shapes, with made-up numbers: a model turn on a hot prompt, a command the bank
answered, a reply that waited behind a warm.

    (timing: wait 0.7s, hear 0.9s, think 1.4s [route none; model 1.2s; tools 0.0s; cache_read 10605; uncached 114; prompt hot], voice 0.9s = 3.9s)
    (timing: wait 0.7s, hear 0.8s, think 0.3s [route home_control; tools 0.2s; template; prompt hot], voice 0.0s cached = 1.8s)
    (timing: wait 0.7s, hear 0.8s, think 31.5s [route none; model 1.5s; tools 0.0s; warm_wait_s 29.9; cache_read 10605; uncached 120; prompt warming], voice 1.1s = 34.1s)

`wait` is the silence the voice detector waited, `hear` Whisper, `think` the brain, `voice` the reply turned into audio. `route none`: the model chose the tool;
`route home_control`: a route ran it first. `model` lists each model call (none when the bank answered: `template`). `prompt hot | warming | cold` is what the keeper
believed as the thinking began; `cache_read` and `uncached` are the first model call's prompt tokens read from Core's cache and read fresh (hot: about 10.6k and a few
hundred; cold: 0 and about 10.5k, and 20 s; all about 800 tokens fewer since the persona was cut). `voice 0.0s cached`: a clip rendered ahead, no TTS call. A slow turn reads off the line: `prompt cold` with `cache_read 0`
is a lost cache (another client of Core's chat slot, a Core restart; the 240 s loop brings it back), `warm_wait_s` time spent behind a warm, a long `model` on a hot
prompt Core busy, a long `voice` the studio. `(speed: ...)` lines in the hub log say when the prompt was warmed and how many phrases were rendered ahead; a `[brain]`
line says when a guard asked again or a text call was run.

**Numbers** (real Core and persona, the reviews of 2026-11-05). An 8-turn run (a timer, a lamp, the time, the date, the weather, a joke, a pause, 2 + 2; Core hot, stub
tools) took 116.4 s with the old brain and 5.3 s now (4.5 s with two rooms): the timer, the lamp and the pause make no model call, the rest about a second each. A reply
right after a warm read 99.4% of its prompt from the cache, in 0.7 s. The right tool for eight phrasings no route takes (model alone, three repeats each, in four
conditions: a fresh conversation, after four routed commands, after one earlier turn, after two chat turns): where the memory held words only, 0 to 3 of 24 (the old
brain after four routed commands: 2 of 24); 21 to 24 of 24 in all four now (24, 24, 23 and 24 on the final tree).
On the third review's tree: 24, 24, 23 and 22 of 24; the three misses are the stub audio's honest "nothing is playing" to "could you skip to the next track".
On the fourth pass the battery ran twice on the working tree of the moment, both times with the shorter persona: 24, 24, 23 and 23 of 24 (the two misses: "could you skip
to the next track", answered by the stub audio's honest "nothing is playing"), then 23, 23, 24 and 18, whose six misses in the last condition were first calls that named
no tool ("would you mind switching the kitchen lights on", "light up the kitchen for me", "could you skip to the next track"), made before any guard runs; the third
review's code scored 24 in that condition on the same Core fifteen minutes later, so it reads as the model's draw. Neither run was on the final tree.
On the final tree (every patch of the fourth review in, 2026-11-06 05:10) the battery scored 23, 24, 24 and 23 of 24; the misses were "could you skip to the next track" answered by
starting the library (the stub audio plays nothing) and one claim with no tool that the backstop does not know ("Kitchen's bright again."). The text-call rescue on the real Core
(something playing, two chat turns first): "could you skip to the next track" 12 of 12 and "turn the music down a notch" 11 of 12, all 24 first replies written as text, no empty
call, no markup spoken or kept; the one miss was a bare `[music]` and prose, whose second try did not call, so the fixed line was spoken. Second tries on the claim path: 9 of 9 left
both alarms, the list and the announcements alone. The spoken yes: 6 of 6 flows did what was said only after the yes, a lone "yes" and a yes after 121 s did nothing, and no
one-utterance order for a lock or the garage ran. The model's own first call still switched off the router or the fridge in 3 of 4 phrasings (Left undone).
With the fifth review's fixes in (2026-11-06 06:30): the battery 24, 24, 21 and 23 of 24 (the misses: first calls that named no tool, whose second try did not call one either, and "could you
skip to the next track" with nothing playing); the text-call rescue 12 of 12 and 10 of 12; the claim path 9 of 9 untouched, with no promise and no false claim spoken; the spoken yes 5 of 6
flows (in the sixth the model refused the unlock in words, so nothing ran); 30,000 + 10,800 scripted turns and 8,000 confirm scenarios with no violation. A plain turn now
reads a median 9,855 tokens from Core's cache (10,660 before the persona was cut), 71 fresh, in a median model call of 2.2 s.

**Tests:** `tests/test_sat_speed_{brain,hub}.py` pin the first build, `tests/test_sat_fix_{brain_core,brain_routes,hub}.py` the first review's fixes,
`tests/test_sat_fix2_{brain_view,brain_guards,small}.py` the second review's, `tests/test_sat_fix3_{text,guards,small}.py` the third's (the cleaning of what a model
writes; the second tries' gate, the spoken yes and the order test; the stage-direction rules and a zero length of time), `tests/test_sat_fix4_{text,guards,small}.py` the
fourth's (1,087 tests: a call written inside the reasoning and the cost of the cleaning, run through the real brain with a scripted model; the fourth second try's gate,
what a second try may say, the yes vocabulary and the call dialects; the thermostat route and the persona's cut sections), `tests/test_sat_review4_*.py` the fourth
review's findings (the seams between the fixes: the lines of a call put back, a call the model only considered, a question taken for a promise, a call in plain words in its
bracket, the dialects in the wording paths; a voice request); beside them, each in its own pytest session,
`services/voice-gate/tests/test_sat_{speed,fix,fix2}_gate.py` and `services/tts-studio/tests/test_sat_{speed,fix,fix3}_tts.py`. The loops in `test_sat_fix_hub.py`,
`test_sat_speed_hub.py` and `test_sat_fix2_gate.py` are bounded, so a mutant fails with a message instead of hanging (`Runaway` after 10,000 sleeps of the fake clock;
`tries()` refuses a `DOOMED_TRIES` that is not a few); the drain loop in `test_sat_dj.py` (`while not ch.done`) is the one left unbounded in this folder.
`test_dj_mode_hands_over_to_spotify_on_a_phrase` is timing-sensitive: it can fail while the machine is busy and passes alone. Run the satellite suite as the last line of
this file says. CI (`core/ci/ci.py`) runs no `services/satellite/tests`, so run them by hand before a push; the voice-gate and tts-studio files sit in suites its
`services` job does run. That job installs no numpy, so the 19 tts-studio tests that make audio (`needs_numpy`) skip there and the other 24 of the three
`test_sat_*_tts.py` run (the folder: 43 passed and 19 skipped in a `python:3.12-slim` image; before, all three files skipped whole and 19 passed).

**Left undone** (nothing here is applied):

* The voice studio's remaining speed, measured but speed only, quality untested, so the Captain's ear decides (`services/tts-studio/README.md`, "Where a line's time goes"):
  a lower `F5_NFE` (12 steps: -21 to -25% per run), a 5.4 s reference clip instead of the 9 s one (-16 to -20%), and CUDA graphs for the flow steps (-31 to -35% on a 1 to
  2 s line once its length is captured).
* The persona (the file shared with flask-api, 13,392 characters; the house voice gets 9,851): its sarcasm doctrine and in-character examples probably drive the long
  replies and are a large part of the system text, and trimming them for the house voice is the next lever, untried. Two things are the Captain's to decide. (1) "Remember
  that ..." no longer comes back as an annotation alone, but nothing keeps what was said past the 8-exchange memory, and about half of 10 replies still promise to ("Noted
  for future reference, Captain."): a house notes file that the prompt reads would make that true. (2) The "Image Generation" section still teaches `[IMAGE: ...]`, which
  is cut, so "show me a sunset" is answered "Here's your sunset" and nothing is shown (2 of 2 on the real Core); cutting that section too is a small change in
  `house_persona()`.
* The model's own FIRST call is not gated, only the routes and the second tries are: a spoken order that reaches the model can switch off the router, the modem, the
  fridge, the freezer, a sump pump or a CPAP through `home_control` (`RISKY_THING` is the list the second tries refuse), a call it writes as text in its first reply runs
  the same way, and so does one put back from its reasoning. Whether such names need the spoken yes that a lock and the garage need is the Captain's decision.
* The second try for a music claim with no music tool (`REASK_MUSIC`) is the one second try with no `retry_may` gate: it is offered only the music tools, and `run_tool`
  drops the model's own `confirm` unless his yes is pending. A gate there (refusing a call outside its menu, or `retry_may`) would also refuse "play jazz and set a
  timer", so it was left.
* A call written as words in one of Core's other dialects (`CALL_DIALECT`: a code fence, `[call: ...]`, `[Calling ...]`, `[tool_call ...]`) is cut by `_wipe_calls`, not
  by `strip_model_markup`, so the one path that does not run it still speaks it: a routine's words (`Daily.word`); the wording of a routed answer and Mentat's
  (`_ask_mentat_now`) run it since the fourth review. The real Core wrote them in a first and a second reply; the wording paths were probed with scripted replies only.
  Calling `strip_call_dialects` from `_model_markup` (with its early return widened) would close the routines too.
* Two odd shapes of an annotation are cut only as far as the first `]`: one with `}]` inside a JSON string (`[SKIPPY_LEARN: {"note": "a }] b"}]` speaks `b"}]`) and a bare
  tag with its JSON on the next line (the tag goes, the JSON is spoken). Neither was ever seen from Core, and a JSON branch that knew about quotes would lose real words
  when an annotation has an odd number of quotes and another follows.
* The music re-ask (`REASK_MUSIC`, a first reply that claimed music with no music tool) is answered with a text call the house cannot read in about half its replies on the real Core
  (`[music with quieter]`, `[music] volume lowered.`, `[music tool: quieter]`, `[music with next]`; 6 of 12 forced re-asks were done, a stronger nudge made no difference, and `tool_choice: any`
  is ignored by Core). The reply is then the fixed line (`UNCLAIM_MUSIC`), or, when its words are not recognised as a claim ("Quieter now. Try not to strain your ears."), the claim itself.
  It needs a first reply that claims in words only (2 of 24 trials of "turn the music down a notch") for a phrasing the routes miss. Reading those brackets as calls was left: a bracket
  that merely contains an action word ("[music next to him]") would then skip a track.
* A call put back from the reasoning is judged by its own line and the words after it, not by the whole thought: a negation AFTER the call ("[set_alarm time=07:00] but no.") or on the
  line before it is not read, two different calls in one thought ("could be [music action=next] or [music action=quieter]") both run, and `_undoes` finds `remove` / `clear`
  anywhere in a call, so a meant `list add "clear tape"` is not put back. All seen in scripted replies only: no live reply carried reasoning on the day it was fixed. Two costs of the
  plain-words rule: a stage direction with one of a tool's action words in it (`[music volume dips]`, `[music next to him]`) is taken for a call and costs a second model call and the
  fixed line instead of the answer, and a stage direction in the VISIBLE words that starts with a tool's name counts as a call of their own, so the call in the reasoning is dropped.
* The thermostat route's older pattern, the one that names a device (`SET_THERMO`), is as it was and still takes some questions and compound requests: "why is the
  thermostat at 60" and "the thermostat is at 70 but it feels cold" set the number named, "set the thermostat to 68 or 70" sets 68, "turn the heat on at 10 pm" sets 10
  (the house accepts 5 to 95 in the thermostat's own unit), "set the thermostat to 72 and turn off the lights" does only the first half, and "turn the thermostat up two
  degrees" is a read. Narrowing it would change phrases that work today ("when it's cold, set the heat to 70"). "What temperature is the oven" and "what's the inside
  temperature" still go to the weather.
* Some regex alternatives are pinned by no test (the third review's mutation run changed them one at a time and nothing failed): `_ORDER_PARTICULAR`, `_ORDER_PREDICATE`,
  `_ORDER_PRONOUN`, `_ORDER_IDIOM`, some alternatives of `RETRY_GROUPS`, `RETRY_GUARDED` and `ACK_TAIL`, the verb forms of `DONE_CLAIMS` and the inside of
  `_asks_ability`. None changes a verdict by itself.
* The turn after the window slides (every fourth) re-reads about 400 tokens (about 1 s), which a warm right after the slide would hide.
* A starred phrase is judged by its look, not its meaning: a lower-case one that starts with a word ending in -s, -ing or -ed that is not in `_NOT_ACTIONS`
  (`*living room*`) is still deleted as a stage direction, and a Title-Case direction (`*Dramatic Pause*`) is spoken (none of the 84 directions in the house logs of 11-01
  to 11-05 was capitalised).
* The backstop skips an order that `NOT_AN_ORDER` reads as a question (`have the music play in the kitchen`) or that names anything in `RISKY_THING`
  (`cancel the oven timer`), where a bare "Done." stands; and a "yes" answered with a bare "Done." and no call is not asked again (the real Core called the tool on all 6
  yes turns of the trial).
* The spoken yes is proved against the real Core and a stub house, not against the real garage opener or the hub's voice path (the yes arrives as a transcript, "Skippy,
  yes"; `says_yes` drops the wake word): do step 7 of the "when it arrives" checklist in `rooms/README.md` by hand the day `HASS_SENSITIVE=1` is set. `CONFIRM_FOR_S` is the
  one constant to change if 120 s is too short at the door.
* A yes to a question the model asked in its own words (not the house's) counts for nothing, so he says it twice (see the guard rails); counting it was left out on
  purpose.
* voice-gate's cache (`services/voice-gate/gate.py`): `Voice._unlink_doomed` decides under its lock whether a leftover file may go (a line that is pending or listed again
  is kept, decided when the pass reaches the name, not when its list was made) and unlinks it outside the lock. A store of the same words that begins after the decision
  and has put its new file in place before the unlink call has that file removed: the line is listed with no file, its next `cached()` is a miss, and the line is rendered
  and stored once more (one render). The wider interleavings are pinned (`tests/test_sat_fix_gate.py`, `test_sat_fix2_gate.py`); this gap, between the decision and the
  call, is not, and is not closed (closing it means unlinking inside the lock that `cached()` and `remember()` also take). A file that will not go (a reader has it open,
  on the Windows bind mount) is given `DOOMED_TRIES` = 3 stores and then left for a restart, with a warning in the log.
* The 19 tts-studio tests that make audio (`needs_numpy`) skip in CI, whose `services` job installs no numpy; the other 24 of the three `test_sat_*_tts.py` run there.

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

**His car and Mentat, by voice** (`car.py`, `physics.py`: the portal chat's `car_status` and `ask_mentat`; 2026-11-04, asked "What is my car's charge level right now?" the
house said it had no access to his car, and the prompt had told it the car and Mentat were out of reach). "What's my car's charge level?", "Is the Tesla plugged in?", "Where is
the car?" go straight to `car_status` (Core left to itself answered "locked" for a car it had not looked at): READ ONLY, one read of skippy-core's car reader (`:7351`,
TeslaMate's last values; asking never wakes the car, and nothing here can lock, heat, charge or sign in to anything), said in percent, miles (the range is the car's estimate, else
its rated range) and °F, home or away from where the house has learned home is (`SKIPPY_HOME_LATLON`, else where the car spends the night), and how old the reading is when the car
is asleep (it still gives the reading). Physics, chemistry, astronomy and unit questions ("the escape velocity of Mars", "how many ounces are in a cup", "what would 70 kg weigh on
the Moon") go straight to `ask_mentat`: Mentat on skippy-core (`:7350`; `SKIPPY_MENTAT_URL` overrides; 75 s at most, it is usually 0.1 to 5), the question in his words, the
answer as the value to five digits with its units, how Mentat read the question (an input it filled in shows there), a short source and its provenance, never a markup, link or
citation mark. Skippy computes no physics (PLATINUM 21): when his words were a factual question and the reply gives a physical figure with no Mentat call (digits or spoken
numbers: "five point zero kilometers per second"), or says "Mentat" without one, the house asks Mentat itself with his words and Core only words the answer (`guard=force physics`
in the house log; a second try with the tool on offer wrote "[ask_mentat: ...]" as text instead, and a call written as words is never spoken). When Mentat leaves an input
out it suggests a value and computes nothing; only his "yes" sends Mentat's own suggestion back (`use_suggestion`, kept five minutes per room). **Mentat's own answers are not
always right** (2026-11-04: a driving distance came back as a black hole's radius, the boiling point on Everest as the sea-level one, "energy in a gallon of gasoline" as its
mass equivalent, the half-life of carbon 14 as an activity): the tool shows how Mentat read the question and its source so Skippy can say "Mentat misread that" instead of passing
the number off. The prompt's "out of your reach" now lists only what has no tool (his files and code, and the car or Mentat while their tool is missing). Not here: starting or stopping the game servers (`server_status` and the
rest need a machine route on the portal first), `make_music`/`make_image` and the pop-ups (patient jobs that end in a browser player). Tests: `tests/test_sat_car.py`,
`tests/test_sat_physics.py`.

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
  book is playing or is the station's current item; otherwise they are Bilby's (it is one stop either way).
* **Where it plays (2026-11-05, the Captain: "Bilby plays all the audio, even Audible books"):** a book is an ITEM OF THE HOUSE RADIO'S QUEUE (`book:<id>`, [RADIO.md](RADIO.md)), so it is the
  one stream: it comes out of whichever speakers are switched on (the Sony, the Enclave, a box tuned in), the volume is the station's, pause / resume / next (a chapter, in a book) /
  what's playing are the station's, and Skippy's voice ducks it with everything else. The engine below (`BookPlayer`: the place, the chapters, the speed, the sleep timer) lives in the
  radio process (`radio.py` builds it on `stereo.StationBookOut`), so a hub restart cannot stop a book and the radio's own restart resumes it at the place the store kept. Music started
  over a book replaces it and the place is kept ("read my book" carries on); a book that ends goes on to the next item of the queue. The hub holds no book player any more: the
  `audiobook` tool and the plain commands (`book_voice.pre_route`, reading the radio's `/state` through `radio_client.BookView`) go to the radio like every music tool.
* **The car:** a book the car is playing (the Drive page saves its place with `src: car, playing` every few seconds) makes the house let go of its copy without writing its own place
  over the car's (`HouseRadio.car_tick`); "play my book" at home carries on from the car's place.
* **The portal's Audiobooks app** (`/audiobooks`, docs/portal/README.md): the browser saves its place into `state/audiobooks-inbox/` (through `services/audiobook-gate`), which `Store` merges
  (newest wins), and the House stereo's Connect check-in carries `state.book` (never as a `track`); a `book:` command from the page (`play:<id>[:<s>]`, `pause`, `resume`, `skip:<s>`,
  `chapter:<±1>`, `speed:<x>`) is run by `audiobooks.run_command` with the station as its transport (`music.BookTransport`); a pod forwards its own to the radio, which plays on that room.
* Tests: `tests/test_sat_audiobooks.py` (the engine; real ffmpeg builds a synthetic multi-chapter m4b and a track-per-chapter folder), `tests/test_sat_book_item.py` (the book as the station's queue item).

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
- **The morning routine comes before the alarm, and one Skippy speaks at a time (the Captain, 2026-11-05):** at 07:00 the alarm began and at 07:10 the
  morning routine, and Skippy said "This is your alarm" over the routine's lines (and, from the third ring, the Enclave and the box said each ring a
  moment apart). Now (1) `daily.Daily.morning_time` puts the briefing `SKIPPY_MORNING_BEFORE_ALARM_MIN` (10) minutes before the day's first alarm
  (`AlarmClock.wake_time(date)`, read from the schedule; an alarm between 04:30 and 11:00; no alarm that day: `SKIPPY_MORNING_AT`; an early first event can
  only move it earlier), waits while an alarm rings, and the bedtime line says when it comes. (2) A ring takes every room's voice lock (`AlarmClock._floor`):
  it waits up to `FLOOR_WAIT_S` (20 s) for a line being said to end, nothing new is said over the ring, and after 20 s it rings anyway. (3) A box that joins
  the stereo's ring (the third) rings after the Enclave has finished and its echo has died away (`Room.ring`), never with it. (4) Things that fall due together,
  or while one rings, ring as ONE (`_fire`): an alarm leads, a reminder's words and a second place follow it, one stop or snooze covers all, and a reminder
  never silences an alarm. A reminder that rides an alarm's ring is said in its first two rings (a ring of reminders alone goes on until each has been said twice);
  a reminder that rode a ring which ended before a whole ring was said, and an alarm that fell due in the very tick the ring gave up, is set again a minute on (a
  reminder that LED the ring and was stopped is not); and cancelling a repeating alarm that leads, on his phone, hands the ring to the next of them. (5) `wake_time` applies the 04:30-11:00 window to each alarm before it takes the
  first (a 03:30 one-off, or a snooze due at 00:05, hides nothing), and leaves out today's occurrence of a repeating alarm that first rings tomorrow. (6) The
  briefing also holds while a snoozed alarm waits (`is_ringing`) and when the day's first alarm is under 7 minutes away (`daily.BRIEFING_NEEDS_S`), so one that
  missed its slot starts after his answer, not into the ring. (7) His own "good morning" and the clock's scheduled start mark the day, so the scheduled briefing
  does not repeat it; a run asked for through `request.json` (`python daily.py run morning`) does only once the briefing's slot has come (before 11:00): an earlier test
  run, at 03:00 say, leaves the day alone, or the 06:50 briefing would never start. (8) Bedtime's alarm-and-briefing line is worked out in
  turn, after `plan_tomorrow` has stored an early event's time, so it names one briefing time; `plan_tomorrow` asks the calendar (`first_tomorrow(day)` in
  `google_home.py`) about the very morning the bedtime leads to, which after midnight is today's date. (9) Timers are said after the ring, in the background
  (`_say_due`), and never hold the clock or a ring up: the line waits for the room's voice lock like any other (a pod that is away takes the house's, as a ring
  does). (10) A cancelled `RoutedOut.play` (an answer ended the ring) stops its clip on the router, so "Alarm off." is not said over the rest of the ring. Tests:
  `test_sat_alarm_clock.py`, `test_sat_alarm_clock_r2.py`, `test_sat_one_voice.py`, `test_sat_daily.py`.

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
  :47851, that a pod's media player plays from the radio, never the hub). They never play different songs.
* **The devices decide (2026-11-05, the Captain: "a single audio stream going out and the devices decide if they are outputting that stream or not"):** every speaker has a
  switch kept in `state/radio/outputs.json` (`outputs.py`): the Sony and the Enclave are on or off (everything on until he says otherwise; a missing or torn file means every
  stereo on, never silence; the first start after the switches arrived keeps where the stream played), a room's box is `auto` (silent), `on` or `off`. A box on `auto` plays only to
  cover for its floor's stereo that is switched on but has been gone for 20 s (the Enclave drops for ~30 s on every Tesla charge command); a stereo he switched off is respected
  and nothing covers for it. "Everywhere / upstairs / downstairs / a room" are only SHORTCUTS that set the switches (exactly those on, the other stereos off), "here" is no longer
  the default (a play that names no place leaves the switches alone), and the `speakers` tool takes `device` + `on` ("turn the Sony off", "play it on the Sony too":
  `brain.device_route`). The portal's Bilby page shows them as toggles (Connect state `outs`, command `out:<id>:on|off`). Flipping a switch moves what plays at once: the same
  play, the same song, never restarted.
* **No listener, no stream (the Captain, 2026-11-05: "If no devices are listening, the stream should pause"):** `HouseRadio.listen_tick`, once a second. When the last speaker
  that is on is switched off the stream is held at once; when it is on but drops out it gets 30 s to come back or for a box to cover (`RADIO_HOLD_S`, 0 turns the rule off),
  then it is held and the place is put back to where the sound was lost, not 30 s on. A play with every speaker off is held at once (the reply says so). It carries on by itself
  when a speaker is back within 10 minutes (`RADIO_RESUME_S`) and nobody gave a command since (`BoxMusic.epoch`); otherwise it stays an ordinary pause. A hold is the radio's own
  pause, never counted as his stop (`RadioClient.pauses`), survives a radio restart (`station.json` keeps `held`), and is in `/state` (`held`, `listening`) and Connect.
* **The hub is a remote:** `hub.music_tools` hands every music tool, the audiobook's too, to the radio (`POST /tool`, with the asking room). It keeps only where
  Skippy's voice comes from. The routines' shuffle and stop, Connect commands to a pod ("Play on" a pod sets that room's box on) go to the radio too. The station itself
  checks in to Connect as "House stereo", so the portal's players and the car steer it.
* **Ducking:** Skippy's voice ducks the WHOLE stream on every speaker, whichever room he is in (he is the only listener). Each voice is a holder with its own expiry
  (`POST /duck {holder}`: `room:<name>` from `Room._woke` / `_say`, `router` from the hub's router for any clip: an alarm, a timer, an announcement), so one finishing never
  brings the stream back while another still talks, and a hub that dies mid-sentence cannot leave it ducked. `speaker_out.Ducker` leaves the radio's process alone (it ducks
  itself; the Sony would be ducked twice).
* **A hub restart never stops or skips the music:** nothing the hub holds plays; a pod tuned in keeps the radio's stream, and a hub that comes
  back finds it playing and leaves it alone. Tests: `tests/test_sat_radio.py`, `tests/test_sat_esphome.py` (the pod as tuner and remote).
* **Stopping Bilby, and the chat knowing what Bilby is (the Captain, 2026-11-04):** *"Bilby plays all the audio, even Audible books ... a single audio
  stream ... the house chat needs to know how to talk to Bilby for audio."* He said "stop the music" as he came in; the homecoming routine's own
  library shuffle started it again 90 s later and it played on the Enclave for 50 minutes, while "stop Bilby" and the rest never reached a tool.
  So: (1) `brain.stop_route` makes every way of stopping (`stop Bilby`, `turn it off`, `enough`, `kill the music`, `stop the heavy metal`, `no more
  music`, `stop the book` ...) a `music` pause before the model runs; a stop with an audio word or Bilby named is always a stop, a bare `stop` /
  `enough` / `shut up` only while the house plays (`houseaudio.Audio.playing()`), never a conditional or timed one. (2) The `music` pause pauses the
  book AND the station (Bilby plays both; `hub.music_tools`). (3) A stop he gives after a routine began keeps that routine's library shuffle off
  (`RadioClient.pauses` against `Run.pauses`, read by `Daily._music_on`). (4) The chat's prompt (`brain.BILBY`, `ABILITIES`) says what Bilby
  is, how to talk to it (play_music / music / speakers / dj_mode / spotify / audiobook), the tools this voice really has and what it does not (his car,
  Mentat, code); the note that starts his newest message (`Brain.live` in `Brain.context()`, not the system prompt) carries `houseaudio.Audio.status()`: what plays and where, volume, queue, a loaded book, a running routine, and who started the
  music (`houseaudio.note_start`). A question about the house ("why is the Enclave playing metal?") answered with how things stand is no claim
  (`musicguard.describing`). Tests: `tests/test_sat_stop_bilby.py`. Since 2026-11-05 it is one stream: the book is an item of the station's queue and each speaker
  opts in or out with its switch (the three bullets above); `houseaudio.Audio.status()` says each speaker's state (playing, switched off, gone, silent), a held stream and
  its reason, and a book as "the audiobook 'X', chapter n of m". Tests: `tests/test_sat_book_item.py`, `tests/test_sat_outputs.py`.
* **The chat knows what it can reach, and is asked again when it denies it (the Captain, 2026-11-05: the house chat "still gets confused about what it has
  access to"):** the prompt's "What you are" paragraph lists the real tool names and, in his words, what they are (`brain.CAPS`: the house, Bilby, audiobooks,
  alarms, timers, lists, announcements, the news, a web search, the weather, his car, Mentat; a phrase only while its tool is here). A reply that says
  "I cannot / I have no access / it isn't connected" with no tool called (`brain.DENIAL`) gets one more go with the real tool list in front of it
  (`Brain._second_try_denied`); a tool that runs is its answer unless its words claim a change that never ran (`_unsound`), otherwise the first reply stands (it may be
  true). It is shown the whole tool list and every call it makes passes the gate of the second tries (Snappy like an Echo, "What a second try may do"); a denial to words
  that name anything in `RISKY_THING` is not asked again.

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
  a free `what` the model sends ("heavy metal on enclave": place and time out, the place becomes the speaker; a speaker it was given wins): a place
  only after a preposition or when plainly one ("upstairs"), so "Living Colour" and "Theatre of Tragedy" keep their names, and "on the stereo" leaves
  the speakers alone. A free `what` that is nothing but a genre ("heavy metal", "some loud metal music") is a genre BEFORE it is a title search
  (`mixes.genre_phrase`): by title "heavy metal" found 3 songs (Pantera's "Heavy Metal Rules"), by tag 1,941 of the 21,451 tracks.
* **"Play it on the upstairs stereo"** (2026-11-04): "it", "this", "that" (or "the music" with move / send / switch ...) plus a place is the
  `speakers` tool (`brain.speakers_route`), run before the model answers; with play / put and nothing playing, the library starts there
  (`play` flag, `station.run`). Before this the model heard it, said "Playing ... *turns an imaginary dial*" and called no tool.
* **A claim with no tool** (2026-11-04, `musicguard.py`, read its docstring and `tests/test_sat_musicguard.py` before changing a pattern): in 4.5 days of logs
  one music request in three that reached the model was answered with a made-up success ("Consider it done. Playing ...", "*adjusts imaginary dial*",
  `tools: []` in the house log), and nothing played; the old guard (alarms, lists, "I've paused") never fired on one. Now: a reply that claims music was played, moved
  or turned, with no MUSIC tool among the turn's actions (a lit lamp does not excuse it), is caught by what he ASKED (his words, or for "yes" / "go ahead" the offer before
  it) and what it CLAIMED. He asked: ONE more model call with only the music tools and his words quoted ("if those words ask for music ..."); a tool that runs is the
  answer, a second try that takes the claim back or asks stands, anything else is another claim and gets the fixed line. He did not ask: no model call at all, the
  claiming sentence is cut and the plain truth said, so nothing can start. A music command answered by neither a tool nor a question ("silent"), and a nameless "Done.",
  are logged only (`[brain] (shadow, ...)`, and `guard` in the house log) until a week of logs says they are safe to act on. **To tell what really ran, read
  `skippy-home/log/<date>.jsonl` (`tools`) and `state/radio.log`** (every music tool call the hubs make, as `[radio] <room> <tool> {args} -> <answer>`): a spoken
  "Playing ..." with `tools: []` was a claim, not an action. The same lie exists for lights, news, announcements, alarms and lists (9 more in those logs): not yet guarded.
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
