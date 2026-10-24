# Skippy at home (the Alexa replacement)

An always-on listener for a room. It hears through a microphone, wakes on **"Skippy"** (a small openWakeWord model trained on his name, `wakeword/`; until
it is trained, the home Whisper hears the name), thinks with a small fast model in Skippy's character, and answers in Skippy's own voice (ElevenLabs through
the voice gate). First home: skippy-pc (`ear.py`); every other room through the Wyoming hub (`hub.py`, below).

    python -m venv .venv && .venv/Scripts/pip install -r requirements.txt     # once
    ./run.sh --devices        # which microphone and speaker numbers
    ./run.sh --calibrate      # levels, to set SATELLITE_FLOOR
    ./run.sh --text "set a timer for ten seconds"                              # the brain and the voice, no microphone
    ./run.sh                  # listen

Settings (the vault `.env`, or the environment): `SKIPPY_HOME_TOKEN`, `ANTHROPIC_API_KEY` (required); `SATELLITE_MIC`, `SATELLITE_SPEAKER`, `SATELLITE_GAIN`,
`SATELLITE_FLOOR`, `SATELLITE_GATE` (default the voice gate on :3007), `SATELLITE_MODEL` (default claude-haiku-4-5); `SATELLITE_WAKE_MODEL` (default
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
  Bilby's music go to it as URLs from the hub's media server (`media.py`, port 10800), and each box is a Bilby Connect speaker (`music.py`).
* `den=192.168.1.41[:10700]`: **a Wyoming satellite** (`wyoming-satellite` on a Raspberry Pi), either waking on "Skippy" itself (its own
  `wyoming-openwakeword` with our model) or streaming everything for the hub to hear its name.

The hub dials each box (as Home Assistant would) and dials it again if it drops. Each room has its own short memory and its own timers
(announced where they were set); the house (lights, plugs) is shared. A `Room` (hub.py) never knows which kind of box it has: that is its link
(`WyomingLink`, `EsphomeLink`). Settings: `SATELLITE_ESPHOME_KEY` (the boxes' API key), `SATELLITE_ROOM_NAMES` (`kitchen=Kitchen (Skippy),...`:
their names in Bilby), `SATELLITE_MEDIA_PORT` (10800), `SATELLITE_MEDIA_HOST` (this PC's address as the boxes see it; found by itself),
`SATELLITE_MUSIC_GATE` (`http://127.0.0.1:3005`), `PORTAL_URL`. Tests: `tests/test_sat_hub.py` (a fake Wyoming box over a real socket, the real
protocol through the `wyoming` package) and `tests/test_sat_esphome.py` (a fake ESPHome box, Home Assistant's event order).

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

`matter.py` turns each node into lights, switches and a thermostat (spoken to in the unit the thermostat shows: a Nest in Fahrenheit
hears 70). Every on/off is read back from the device and tried once more if it didn't change, as with HomeKit. HomeKit, Matter and Home
Assistant together are one house (`house.Combined`): one set of names, one set of guard rails, and one backend that is down never hides
another's things. Tests: `tests/test_sat_matter.py`, against a fake controller speaking python-matter-server's real message format.

Guard rails (`house.py`, the same for every backend; tests in `tests/test_sat_house*.py`... see `test_sat_hass.py` and `test_sat_homekit.py`):

* only lights, switches, fans, covers, climate, media players, scenes, scripts, vacuums, humidifiers and input booleans can change; sensors can be read;
* locks, alarm panels and garage doors/gates are invisible unless `HASS_SENSITIVE=1`, and then still need a spoken yes (`confirm`); an alarm panel is never driven;
* a spoken name must match one entity (the room counts as part of the name); several matches change nothing and Skippy asks which;
* no raw service calls from the model; pairing keys and tokens are never returned or printed.

Other backends plug into the same `House`: Home Assistant (`hass.py`, if he ever runs one: `HASS_URL` + `HASS_TOKEN` in the vault). Later: the LG webOS TV
(`aiowebostv`), other rooms' satellites over the Wyoming protocol.

Tests: `python -m pytest tests` from this folder (its own session: other services have a `gate.py` too).
