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

## The house: Home Assistant

Skippy never talks to a device. The standard home-AI pattern is **Home Assistant as the hub** (it already speaks Hue, Zigbee, Matter, Alexa devices, plugs,
thermostats...) with the assistant calling its REST API with a long-lived access token. Set `HASS_URL` (e.g. `http://homeassistant.local:8123`) and
`HASS_TOKEN` (Home Assistant, your profile, Security, Long-lived access tokens) in the vault and the brain gains three tools (`hass.py`):
`home_devices`, `home_state`, `home_control`. Guard rails, all tested against a fake Home Assistant (`tests/test_sat_hass.py`):

* only lights, switches, fans, covers, climate, media players, scenes, scripts, vacuums, humidifiers and input booleans can change; sensors can be read;
* locks, alarm panels and garage doors/gates are invisible unless `HASS_SENSITIVE=1`, and then still need a spoken yes (`confirm`); an alarm panel is never driven;
* a spoken name must match one entity (the room counts as part of the name); several matches change nothing and Skippy asks which;
* no raw service calls or templates from the model; the token is never returned or printed.

Later, the same Home Assistant can also be given to Claude as an MCP server (its built-in MCP Server integration) so the private MCP pane on /me lists it, and
this listener can speak the Wyoming protocol so other rooms' satellites (Raspberry Pi, ESP32) can join.

Tests: `python -m pytest tests` from this folder (its own session: other services have a `gate.py` too).
