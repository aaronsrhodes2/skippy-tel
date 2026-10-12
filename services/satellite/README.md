# Skippy at home (the Alexa replacement)

An always-on listener for a room. It hears through a microphone, wakes on **"Skippy"** (found by the home Whisper: no wake-word model to train), thinks with a
small fast model in Skippy's character, and answers in Skippy's own voice (ElevenLabs through the voice gate). First home: skippy-pc.

    python -m venv .venv && .venv/Scripts/pip install -r requirements.txt     # once
    ./run.sh --devices        # which microphone and speaker numbers
    ./run.sh --calibrate      # levels, to set SATELLITE_FLOOR
    ./run.sh --text "set a timer for ten seconds"                              # the brain and the voice, no microphone
    ./run.sh                  # listen

Settings (the vault `.env`, or the environment): `SKIPPY_HOME_TOKEN`, `ANTHROPIC_API_KEY` (required); `SATELLITE_MIC`, `SATELLITE_SPEAKER`, `SATELLITE_GAIN`,
`SATELLITE_FLOOR`, `SATELLITE_GATE` (default the voice gate on :3007), `SATELLITE_MODEL` (default claude-haiku-4-5).

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

Guard rails (`house.py`, the same for every backend; tests in `tests/test_sat_house*.py`... see `test_sat_hass.py` and `test_sat_homekit.py`):

* only lights, switches, fans, covers, climate, media players, scenes, scripts, vacuums, humidifiers and input booleans can change; sensors can be read;
* locks, alarm panels and garage doors/gates are invisible unless `HASS_SENSITIVE=1`, and then still need a spoken yes (`confirm`); an alarm panel is never driven;
* a spoken name must match one entity (the room counts as part of the name); several matches change nothing and Skippy asks which;
* no raw service calls from the model; pairing keys and tokens are never returned or printed.

Other backends plug into the same `House`: Home Assistant (`hass.py`, if he ever runs one: `HASS_URL` + `HASS_TOKEN` in the vault). Later: the LG webOS TV
(`aiowebostv`), other rooms' satellites over the Wyoming protocol.

Tests: `python -m pytest tests` from this folder (its own session: other services have a `gate.py` too).
