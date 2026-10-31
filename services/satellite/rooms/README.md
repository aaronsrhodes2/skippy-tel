# The room boxes

A Waveshare **ESP32-S3 Smart 86 Box** (Waveshare's name for it in its wiki: ESP32-S3-Touch-LCD-4B) per room, flashed with `skippy-room.yaml`. Each one is a dumb
terminal: it hears and it plays. Everything that thinks lives on skippy-pc in `hub.py`: the "Skippy" wake word, Whisper, the brain, Skippy's voice,
the lights and plugs, and Bilby's music. The box only does the things that are faster done on the box itself:

* it cleans and streams its microphones (16 kHz) to the hub while the hub is connected;
* it plays the URLs the hub sends it from skippy-pc's media server: Skippy's voice as an *announcement* over the music, and Bilby's tracks as
  *media*. It ducks the music under the voice itself;
* the two volume keys change the volume at once;
* a ring on the screen breathes in the colour of what is happening: blue idle, cyan listening, amber thinking, purple speaking, green music,
  red muted. Under it, the hub writes what is playing or what Skippy just said;
* a tap on the screen counts as "Skippy" (the hub decides what that means).

## Flash one (skippy-pc, Git Bash)

1. In the vault `.env`: `ROOM_WIFI_SSID` and `ROOM_WIFI_PASSWORD` (a **2.4 GHz** network: the ESP32-S3 has no 5 GHz).
2. Plug the box into skippy-pc with USB-C, then:

        bash services/satellite/rooms/flash.sh kitchen "Kitchen"

   The first run makes `SATELLITE_ESPHOME_KEY` (the API encryption key, one for all the boxes) and `SATELLITE_ESPHOME_OTA`, and prints them
   **once**: put both lines in the vault. Later updates use the same command over Wi-Fi.
3. Tell the hub about it, in the vault `.env`:

        SATELLITE_ROOMS=kitchen=esphome://skippy-kitchen.local,bedroom=esphome://skippy-bedroom.local,den=esphome://skippy-den.local
        SATELLITE_ROOM_NAMES=kitchen=Kitchen (Skippy),bedroom=Bedroom (Skippy),den=Den (Skippy)      # what Bilby's "Play on" calls them

4. Let the boxes reach skippy-pc's media server: allow inbound TCP **10800** on the private network in Windows Firewall (one time):

        netsh advfirewall firewall add rule name="Skippy media (room boxes)" dir=in action=allow protocol=TCP localport=10800 profile=private

5. Start the hub: `cd services/satellite && ./.venv/Scripts/python hub.py`.

## Bilby's Audio Service on the boxes

Each connected box checks in with the portal as one of the Captain's Connect devices (`POST /home/music/checkin`, with the home token). So every
Bilby player lists it under **Play on** next to the speaker windows (**All** when there are more than two). Choosing it hands the box the phone's
queue at the same place, and play, pause, next and previous from any player's remote reach it. The music never passes through the portal: the
box fetches each track from skippy-pc's media server, which reads it from the local music gate and has ffmpeg turn it into 16 kHz mono FLAC
(the library's `.m4a` files are AAC, which an ESP32 cannot decode). By voice, in the room: "Skippy, play some Pink Floyd", "Skippy, pause",
"next", "louder".

## Why these choices

* **ESPHome, not custom firmware.** It already has the codecs, the touch screen, the media player and a voice-assistant client for this exact
  board (the I2S pins, the expander, and the panel's init sequence come from Matthieu Boulord's MIT-licensed
  [config for this board](https://github.com/MatthieuBoulord/ESP32-S3-Touch-LCD-4B-ESPHome-Home-Assistant-Voice-Satellite)). The hub talks
  to the box with `aioesphomeapi`, the same library Home Assistant uses, so no Home Assistant is needed.
* **Full duplex on one I2S bus.** The microphones and the speaker share their clock pins, so without ESPHome's `full_duplex` the box could not
  hear "Skippy, pause" while it plays. That option is in ESPHome 2026.11; until that is released, `flash.sh` pins the dev commit that has it
  (`55a62993`). Then set `ESPHOME_PIN=esphome==2026.11.0`.
* **16 kHz music.** The voice assistant needs a 16 kHz microphone, and duplex needs the speaker at the same rate. That is roughly AM-radio
  bandwidth, which is about what the small speaker in the box can reproduce anyway.
* **The wake word is heard on skippy-pc** (`use_wake_word`: the box streams, the hub's trained `skippy.onnx` listens), because an ESP32 cannot
  run openWakeWord. An on-box "Skippy" would need a separately trained microWakeWord model: possible later, if the Wi-Fi traffic (32 KB/s per
  box) ever matters.

## What is proven, and what is not yet

* `esphome config` passes on the pinned ESPHome (2026.11.0-dev): every component, pin and option checks out.
* The hub side is tested against a simulated box (`tests/test_sat_esphome.py`): Home Assistant's own event order, the follow-up turn,
  announcements, the tap, Bilby's take, pause, resume and next, the Connect check-ins, and the media server over real HTTP, with a pass the real
  music gate accepts.
* **Not yet:** a compiled build and a real box. The firmware's first compile, the panel init, the speaker's loudness and the microphone gain
  (`mic_gain`, `volume_multiplier`) all get checked on the first box. If the screen stays black, it is the panel init sequence; if it can't hear
  across the room, raise `mic_gain` (33 dB now) before anything else.

## The streamed face (2026-10-31): skippy-pc draws Skippy, the box only shows him

The box's own LVGL drawing of Skippy (the 50 ms `interval` in `skippy-room.yaml`) only reaches 5 frames/s on the ESP32-S3. So when the hub is connected it draws him
itself and streams him: `face_render.py` is a line-for-line port of that tick (same expression easing, emotion table, tints, hat physics, effects, ears, thinking
graphics, rocking, the conversation text) drawing with numpy + OpenCV in about 8 ms a frame, and `face_stream.py` sends the box only the 16x16 tiles that changed,
deflated, many to a UDP datagram, at 20 frames/s (the tick rate) on port 10803. The box's `components/skippy_stream` inflates them and writes them straight into the
panel's frame buffer, each frame at its last datagram, so a frame is never half drawn.

* **What the hub knows.** The box publishes its state as the `Face` text sensor (`"<state>,<talking 0/1>"`: it knows when the audio really starts), the hub writes
  `Mouth`, `Emote` and `Session` itself (`EsphomeLink.set_text` tells the box and the drawing the same thing, mirrored pods included) and adds the conversation lines.
* **The box owns the screen unless datagrams arrive.** The first valid datagram makes the yaml pause LVGL and let the receiver draw (`Stream live` turns on and the hub
  sends the whole picture at once); 1.2 s without one (the hub is down, the Wi-Fi is gone) and LVGL resumes, so the box draws its own Skippy, the `offline` face
  included. The `Streamed face` switch (and `SATELLITE_FACE_STREAM=0` on the hub) turns it off.
* **Loss.** UDP over a weak Wi-Fi drops datagrams: the box draws what arrived, and the sender resends a rolling slice of the screen every frame (the whole screen every
  ~6 s) and a keyframe whenever the box says it took over, so a lost tile never stays wrong for long.
* **Safety.** The receiver checks every field before using it (magic, version, rectangle inside the panel, lengths, exact inflated size), never writes unless LVGL is
  paused, and decompresses with a heap-held state on an 8 KB task (the first version put the ~11 KB inflate state on a 4 KB stack: the LoadProhibited crash).
  `tests/test_sat_face_stream.py` runs the same checks against garbage.
* **Changing how he looks:** change the yaml drawing (the fallback), then `face_render.py` to match. `tools/face_stream_demo.py` streams by hand (a colour-bar test picture, a
  scripted run, a throughput test); `tools/box_fps.py` reads the box's own rates; `rooms/shot.py` takes the picture from the panel itself while streaming.
