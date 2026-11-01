"""The wake loop, tested end to end through a pod's own speaker (the Captain, 2026-11-01: "worked quite well", the standard way to test it).

    ./.venv/Scripts/python wakeloop.py                          3 trials in the Office: "Hey Skippy", a pause, a question (a new one each time)
    ./.venv/Scripts/python wakeloop.py --trials 5 --pause 0     the name and the command in one breath
    ./.venv/Scripts/python wakeloop.py --wake clips             the Captain's own held-out recordings of the name (state/wakeword/real/test)
    ./.venv/Scripts/python wakeloop.py --command ""             the name alone: the room must keep listening (no "no audio" from the watchdog)

The running hub is not touched: this is a second ESPHome API client that plays each clip on the pod's media player AS MEDIA (an
announcement would stop the pod's microphone; media does not), so the pod's own microphone hears it like a voice in the room and the hub
hears its name the usual way. The projector's HDMI is no good for this: it is off at night and too quiet at the pod. Each trial then reads
state/hub.log for what the hub did: woke, heard (the words), and whether the watchdog said "no audio from the box".

The wake: `tts` (default) is a synthesized "Hey Skippy" (the en-AU voice wakes the trained model reliably), `clips` the Captain's recordings,
served by the hub's own media server (GET /w/<pass>/..., media.py: port 10800 is the only one the firewall lets the pods reach). The command
is synthesized too. Waits for the room to be quiet before each trial, and skips while Bilby plays on the pod. It is audible in the room.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import re
import shutil
import socket
import sys
import time
from pathlib import Path
from urllib.parse import quote

from media import WAKELOOP_DIR, wakeloop_pass

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "state" / "hub.log"
CLIPS = ROOT / "state" / "wakeword" / "real" / "test" / "positive"
TTS = "https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl=en-AU&q="
COMMANDS = ("What time is it?", "What day is it today?", "Tell me a short joke.", "What is the weather like?", "How are you today?")
BUSY = re.compile(r"\] (woke|heard|said|routine|\(filler|listening)")


def tts_url(text: str) -> str:
    return TTS + quote(text)


def log_tail(since_bytes: int) -> list[str]:
    with LOG.open("rb") as f:
        f.seek(since_bytes)
        return f.read().decode("utf-8", "replace").splitlines()


def room_lines(lines: list[str], room: str) -> list[str]:
    return [ln for ln in lines if f"[{room}]" in ln and "face stream" not in ln]


def verdict(lines: list[str], room: str, command: str) -> dict:
    """What the hub did in one trial, from its log lines since the trial began."""
    mine = room_lines(lines, room)
    heard = [ln.split("heard: ", 1)[1] for ln in mine if "] heard: " in ln]
    out = {"woke": any("] woke" in ln for ln in mine), "heard": heard[0] if heard else "",
           "watchdog": any("no audio from the box" in ln for ln in mine), "said": any("] said: " in ln for ln in mine)}
    want = re.sub(r"[^a-z ]", "", command.lower()).split()
    got = re.sub(r"[^a-z ]", "", out["heard"].lower()).split()
    out["ok"] = out["woke"] and not out["watchdog"] and (not want or sum(w in got for w in want) >= 0.6 * len(want))
    return out


def quiet_for(room: str, seconds: float) -> bool:
    """No woke / heard / said / routine in the room's log for `seconds` (by the log's HH:MM:SS stamps)."""
    size = LOG.stat().st_size
    lines = log_tail(max(0, size - 200_000))
    now = time.strftime("%H:%M:%S")
    for ln in reversed(room_lines(lines, room)):
        if BUSY.search(ln) and ln[:8] <= now:
            h, m, s = (int(x) for x in ln[:8].split(":"))
            nh, nm, ns = (int(x) for x in now.split(":"))
            return (nh * 3600 + nm * 60 + ns) - (h * 3600 + m * 60 + s) >= seconds
    return True


def local_ip(towards: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((towards, 80))
        return s.getsockname()[0]
    finally:
        s.close()


def box_host(env: dict, room: str) -> str:
    for part in (env.get("SATELLITE_ROOMS") or "").split(","):
        name, _, addr = part.strip().partition("=")
        if name == room:
            return addr.replace("esphome://", "").split(":")[0]
    raise SystemExit(f"no room {room!r} in SATELLITE_ROOMS")


async def run(args, env: dict, out=print) -> int:
    from aioesphomeapi import APIClient, MediaPlayerEntityState, MediaPlayerState    # noqa: PLC0415
    host = box_host(env, args.room)
    client = APIClient(host, 6053, None, noise_psk=env.get("SATELLITE_ESPHOME_KEY") or None, client_info="wakeloop")
    await client.connect(login=True)
    player, state = None, {}
    try:
        entities, _ = await client.list_entities_services()
        player = next((e.key for e in entities if type(e).__name__ == "MediaPlayerInfo"), None)
        if player is None:
            out(f"the {args.room} box has no media player")
            return 2
        def on_state(s):
            if isinstance(s, MediaPlayerEntityState) and s.key == player:
                state["player"] = s.state
                if s.state == MediaPlayerState.PLAYING:
                    state["played"] = True
        client.subscribe_states(on_state)
        wake_urls = [tts_url("Hey Skippy")]
        if args.wake == "clips":
            wpass = wakeloop_pass(env.get("SKIPPY_HOME_TOKEN", ""))
            clips = sorted(CLIPS.glob("*.wav"))
            if not wpass or not clips:
                out("clips need SKIPPY_HOME_TOKEN in the vault and recordings in " + str(CLIPS))
                return 2
            WAKELOOP_DIR.mkdir(parents=True, exist_ok=True)
            base = f"http://{local_ip(host)}:{env.get('SATELLITE_MEDIA_PORT', '10800')}/w/{wpass}/"
            wake_urls = []
            for c in clips:
                shutil.copyfile(c, WAKELOOP_DIR / c.name)
                wake_urls.append(base + c.name)
        results = []
        for i in range(args.trials):
            for _ in range(int(args.wait_quiet * 2) + 120):
                if quiet_for(args.room, args.wait_quiet) and state.get("player") != MediaPlayerState.PLAYING:
                    break
                await asyncio.sleep(1)
            else:
                out(f"trial {i + 1}: the room never went quiet (or Bilby is playing on the pod): skipped")
                continue
            start = LOG.stat().st_size
            state["played"] = False
            wake = random.choice(wake_urls) if args.wake == "clips" else wake_urls[0]
            client.media_player_command(player, media_url=wake, announcement=False)
            command = args.command if args.command is not None else COMMANDS[i % len(COMMANDS)]   # a new one each time: the same words
            if command:                                             # within 90 s are taken for his own voice coming back (Room.is_echo)
                await asyncio.sleep(args.pause + 1.2)               # (the wake clip itself lasts about a second)
                client.media_player_command(player, media_url=tts_url(command), announcement=False)
            end = time.monotonic() + args.timeout
            while time.monotonic() < end:
                v = verdict(log_tail(start), args.room, command)
                if v["watchdog"] or (v["said"] if command else (v["woke"] and time.monotonic() > end - args.timeout + 14)):
                    break
                await asyncio.sleep(1)
            v = verdict(log_tail(start), args.room, command)
            results.append(v)
            why = "" if v["ok"] or state.get("played") else " (the pod never played the clip: no sound, nothing to hear)"
            out(f"trial {i + 1}: {'OK  ' if v['ok'] else 'FAIL'} woke={v['woke']} heard={v['heard']!r} watchdog={v['watchdog']}{why}")
        passed = sum(r["ok"] for r in results)
        out(f"{passed}/{len(results)} trials passed")
        return 0 if results and passed == len(results) else 1
    finally:
        await client.disconnect()


def main(argv=None, out=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--room", default="office")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--wake", choices=("tts", "clips"), default="tts")
    ap.add_argument("--command", default=None, help='what is said after the name (default: a different question each trial; "" for the name alone)')
    ap.add_argument("--pause", type=float, default=3.0, help="seconds between the name and the command")
    ap.add_argument("--timeout", type=float, default=45.0, help="seconds to wait for the hub's answer")
    ap.add_argument("--wait-quiet", type=float, default=35.0, help="the room's log quiet this long before each trial")
    args = ap.parse_args(argv)
    from ear import load_env                                     # noqa: PLC0415
    return asyncio.run(run(args, load_env(), out=out))


if __name__ == "__main__":
    sys.exit(main())
