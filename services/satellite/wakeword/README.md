# Skippy's wake word

A small [openWakeWord](https://github.com/dscripka/openWakeWord) model, `skippy.onnx`, that hears "Skippy" or "Hey Skippy" in the raw microphone stream.
With it, nothing said in a room goes anywhere until the room is addressed: no utterance is sent to Whisper just to find out whether it began with his name
(the old way, still the fallback while there is no model). The model runs in three places:

* `ear.py` on skippy-pc's own microphone;
* `hub.py`, for room boxes that stream everything (`start_stage: wake`);
* a Raspberry Pi satellite's own `wyoming-openwakeword` (copy `skippy.tflite` or `skippy.onnx` into its `--custom-model-dir`), so the box wakes by itself.

## Train it (skippy-pc, once; again whenever it needs to be better)

1. **Record the Captain** (10 minutes, worth more than thousands of synthetic voices):

        cd services/satellite
        ./.venv/Scripts/python wakectl.py record positive 50     # "Skippy" / "Hey Skippy", near and far, loud and quiet, from other rooms' doorways
        ./.venv/Scripts/python wakectl.py record negative 30     # "Skipper", "skip it", "Computer", sentences, laughs: everything that must NOT wake it

2. **Train** (Docker Desktop with the NVIDIA GPU runtime):

        bash services/satellite/wakeword/train.sh

   The first run downloads about 20 GB into `state/wakeword/data` (the 17 GB of pre-computed negative features is most of it), makes 50,000
   synthetic "Skippy"s in 900+ voices, mixes in his own recordings (each 20 times, differently augmented), plays them all "in" real rooms with real
   noise and music, and trains. Expect a few hours. Every step resumes: if it stops, run it again. Output: `state/wakeword/skippy.onnx` (+ `.tflite`).

3. **Test it on his voice**, pick the threshold, and put it in the vault `.env`:

        ./.venv/Scripts/python wakectl.py test       # recall and false wakes at 0.3 ... 0.9, and the one to use: SATELLITE_WAKE_THRESHOLD=...
        ./.venv/Scripts/python wakectl.py listen     # its score live; WOKE when it fires

`ear.py` and `hub.py` pick the model up on their next start (`SATELLITE_WAKE_MODEL` points elsewhere).

## The choices in `skippy.yml`

* **"Skipper" is a negative.** It is one of the Captain's own names: hearing it must never wake the house. So are "skip it", "Skype",
  "Computer" (the Echo's word, while both are in the house) and other near-misses.
* **0.1 false wakes an hour** as the training target (openWakeWord's default is about 0.25): a house hears all day.
* Versions are pinned in the `Dockerfile` (openWakeWord commit `368c037`, piper-sample-generator `v2.0.0`, the last with the `generate_samples.py`
  its trainer imports; the notebook's library versions). The notebook's AudioSet `.tar` link no longer exists; `data.py` reads a parquet shard instead.

The `state/wakeword/` folder is git-ignored: his recordings are his voice, and the model is rebuilt from them.

## Recording through a room pod (the way to do it)

A pod's microphone (ESP32-S3, its ADC, noise suppression and gain) sounds nothing like skippy-pc's USB mic, so record through the pod itself:

    bash services/satellite/wakeword/record.sh positive 50 [room]     # stops the hub (by PID), Skippy prompts aloud and on the pod's screen, restarts the hub
    bash services/satellite/wakeword/record.sh negative 30 [room]

`wakerec.py` keeps each clip (0.6 s before his voice, to the end of the phrase) in `state/wakeword/real/{positive,negative}/`, every 5th in `real/test/`
(held out of training, so `wakectl.py test` is honest). It resumes where it stopped. `train.sh` trains on the rest, his negatives included.

## What the first models did (2026-10-31, Office pod)

Trained on 29 real positives and 28 real negatives through the pod (plus 7+8 held out), each mixed in 20 times at x2..x5 gain, and 50,000 synthetic voices.
`skippy.onnx` is **v1** (negative weight 1500) at `SATELLITE_WAKE_THRESHOLD=0.2`; v2 (weight 400, cleaned labels) was no better (`skippy_v2.onnx`).
Measured: held-out "Skippy" 5 of 7, recorded training clips 16 of 29, 0 false wakes in 2.4 h of background audio, 2 of 36 of his not-Skippy clips wake it.
The limit is the data: the pod's microphone is quiet and some takes are mush (7 of 43 "positives" were not the word at all). Record more, clean takes, then `train.sh`.
Training pitfalls fixed here: it needs a Docker volume (`skippy-wake-train`), `--shm-size`, and a quarter of the big negative and validation sets, or it kills Docker's VM.
