"""Skippy's ear: the always-on voice of the house, on skippy-pc, replacing the Alexa that answers to "Computer".

    microphone -> voice activity -> each utterance to Whisper (the voice gate) -> only speech that starts with "Skippy" counts
    -> the quick Skippy brain (Anthropic + timers) -> Skippy's voice (the gate: ElevenLabs, RNNoise polish, a daily budget) -> the speaker

Nothing is recorded or kept: an utterance lives in memory until it has been transcribed, and only the words after "Skippy" are ever logged.
Like Alexa it has a follow-up window: after an answer it keeps listening for a few seconds without the wake word. It runs as a plain Python process
on the PC (Docker cannot use Windows' microphone): see README.md.

    python ear.py --devices          list microphones and speakers
    python ear.py --calibrate        show the microphone's loudness live (to set SATELLITE_FLOOR)
    python ear.py --text "set a timer for ten seconds"      try the brain and the voice without the microphone
    python ear.py                    listen
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from pathlib import Path

from vad import Vad, rms
from voicegate import BudgetSpent, GateError, wav_bytes
from wake import is_goodbye, is_stop, wake_split

BLOCK_MS = 50
IDLE, LISTENING, FOLLOWUP = "idle", "listening", "followup"


class Ear:
    def __init__(self, gate, brain, timers, play, chime, local_voice, flush=lambda: None, log=print, floor: float = 0.012,
                 min_speech_ms: int = 600, listen_ms: int = 8000, followup_ms: int = 6000, summarize_over: int = 280):
        self.gate, self.brain, self.timers, self.play, self.chime, self.local_voice, self.flush, self.log = gate, brain, timers, play, chime, local_voice, flush, log
        self.vad = Vad(floor_min=floor)
        self.min_speech_ms, self.listen_ms, self.followup_ms, self.summarize_over = min_speech_ms, listen_ms, followup_ms, summarize_over
        self.mode, self.deadline, self._arm = IDLE, 0.0, 0
        self.pre: deque = deque(maxlen=8)                           # 400 ms before the voice was noticed, so the first syllable is not lost
        self.buf: list | None = None

    # ── the stream of microphone blocks ──
    def feed(self, t: float, samples) -> None:
        self.pre.append(samples)
        ev = self.vad.push(rms(samples), t)
        if self.buf is not None and ev != "speech":
            self.buf.append(samples)
        if ev == "speech":
            self.buf = list(self.pre)
        elif ev == "end" and self.buf is not None:
            utterance, self.buf = self.buf, None
            if len(utterance) * BLOCK_MS - self.vad.end_silence_ms >= self.min_speech_ms:      # a cough or a click is not worth sending
                self._utterance(t, [x for block in utterance for x in block])
        self._tick(t)

    def _tick(self, t: float) -> None:
        if self._arm:
            self.deadline, self._arm = t + self._arm, 0
        if self.mode != IDLE and t > self.deadline and self.buf is None:
            if self.mode == LISTENING:
                self.chime("done")
            self.mode = IDLE
        for timer in self.timers.due():
            self.log(f"timer done: {timer.label}")
            self.chime("timer")
            self._say(f"Your {timer.label} timer is done.")

    # ── what was said ──
    def _utterance(self, t: float, samples) -> None:
        try:
            text = self.gate.stt(wav_bytes(samples))
        except GateError as e:
            self.log(f"(could not hear: {e})")
            return
        if not text:
            return
        woke, rest = wake_split(text)
        if self.mode == IDLE:
            if not woke:
                return                                              # room talk: gone, never logged
            self.log("woke")
            self.chime("wake")
            if rest:
                self._handle(t, rest)
            else:
                self.mode, self.deadline = LISTENING, t + self.listen_ms
            return
        command = rest if woke else text                            # in a conversation the word is optional
        if command:
            self._handle(t, command)

    def _handle(self, t: float, command: str) -> None:
        if is_goodbye(command) or is_stop(command):
            self.mode = IDLE
            self.chime("done")
            return
        self.log(f"heard: {command}")
        try:
            reply = self.brain.reply(command)
        except Exception as e:                                      # noqa: BLE001 — whatever the API did, he gets an answer
            self.log(f"(brain failed: {type(e).__name__})")
            reply = "My brain is not answering. Typical."
        self.log(f"said: {reply[:120]}")
        self._say(reply)
        self.mode, self._arm = FOLLOWUP, self.followup_ms

    def _say(self, text: str) -> None:
        said = text
        if len(text) > self.summarize_over:
            try:
                said = self.gate.summary(text)
            except GateError:
                said = text[:self.summarize_over]
        try:
            self.play(self.gate.tts(said))
        except BudgetSpent:
            self.log("(Skippy's voice has used today's budget: using the machine's own voice)")
            self.local_voice(said)
        except (GateError, RuntimeError) as e:
            self.log(f"(voice unavailable: {e}: using the machine's own voice)")
            self.local_voice(said)
        self.flush()                                                # never answer our own voice


# ── the program ──
def load_env(path: str | None = None) -> dict:
    env = dict(os.environ)
    for candidate in (path, os.environ.get("SKIPPY_ENV"), "G:/My Drive/vault/.env"):
        if candidate and Path(candidate).is_file():
            for line in Path(candidate).read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
            break
    return env


def build(env: dict):
    import anthropic                                                # noqa: PLC0415
    from brain import Brain                                         # noqa: PLC0415
    from timers import Timers                                       # noqa: PLC0415
    from voicegate import Gate                                      # noqa: PLC0415
    token = env.get("SKIPPY_HOME_TOKEN", "")
    key = env.get("ANTHROPIC_API_KEY", "")
    if not token or not key:
        sys.exit("ear: SKIPPY_HOME_TOKEN and ANTHROPIC_API_KEY must be in the vault .env (or the environment)")
    timers = Timers()
    gate = Gate(env.get("SATELLITE_GATE", "http://127.0.0.1:3007"), token)
    hass = None                                                     # the house: what Skippy can control, through whichever backend is set up
    from homectl import store_path                                  # noqa: PLC0415
    from homekit import HomeKit, Store                              # noqa: PLC0415
    if Store(store_path(env)).load()["devices"]:                    # lights and switches paired with homectl.py: HomeKit, natively
        hass = HomeKit(Store(store_path(env)), sensitive=env.get("HASS_SENSITIVE") == "1")
    elif env.get("HASS_URL") and env.get("HASS_TOKEN"):             # or Home Assistant, if he runs one (the vault holds both)
        from hass import Hass                                       # noqa: PLC0415
        hass = Hass(env["HASS_URL"], env["HASS_TOKEN"], sensitive=env.get("HASS_SENSITIVE") == "1")
    brain = Brain(anthropic.Anthropic(api_key=key), env.get("SATELLITE_MODEL", "claude-haiku-4-5"), timers, hass=hass)
    return gate, brain, timers


def _device(value):
    if value in (None, ""):
        return None
    return int(value) if str(value).isdigit() else value


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--devices", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--text", help="say this to Skippy as if heard, and hear the answer (no microphone)")
    args = ap.parse_args(argv)
    import audio                                                    # noqa: PLC0415
    if args.devices:
        print(audio.list_devices())
        return 0
    env = load_env()
    mic_dev, spk_dev = _device(env.get("SATELLITE_MIC")), _device(env.get("SATELLITE_SPEAKER"))
    if args.calibrate:
        with audio.Mic(mic_dev, float(env.get("SATELLITE_GAIN", "1"))) as mic:
            print("Speak normally, then stay quiet. Ctrl+C to stop. Set SATELLITE_FLOOR a little below your speaking level (default 0.012).")
            for t, samples in mic.blocks():
                lv = rms(samples)
                print(f"{lv:7.4f} " + "#" * min(60, int(lv * 400)))
        return 0
    gate, brain, timers = build(env)
    play = lambda mp3: audio.play_mp3(mp3, spk_dev)                 # noqa: E731
    chime = lambda kind: audio.chime(kind, spk_dev)                 # noqa: E731
    if args.text:
        ear = Ear(gate, brain, timers, play, chime, audio.speak_local)
        ear._handle(0, args.text)
        return 0
    with audio.Mic(mic_dev, float(env.get("SATELLITE_GAIN", "1"))) as mic:
        ear = Ear(gate, brain, timers, play, chime, audio.speak_local, flush=mic.flush, floor=float(env.get("SATELLITE_FLOOR", "0.012")),
                  log=lambda m: print(time.strftime("%H:%M:%S"), m, flush=True))
        print(time.strftime("%H:%M:%S"), 'listening: say "Skippy" ...', flush=True)
        for t, samples in mic.blocks():
            ear.feed(t, samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
