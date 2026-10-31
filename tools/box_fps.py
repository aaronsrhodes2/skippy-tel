"""Measures how fast a room box draws its face, from the box's own log, in a few states (a diagnostic, run by hand).

    services/satellite/.venv/Scripts/python tools/box_fps.py [host] [seconds-per-state] [state ...]

For each state (default: idle speaking thinking) it sets the face by hand (the `face` action, plus a mouth and emote text for speaking), listens to the box's
log for the given seconds and prints the face tick rate ("face: tick .. (.. Hz)", the on-box drawing) or the streamed rate ("skippy_stream: .. frames/s").
It also prints a line for every Wi-Fi drop it sees. The hub may be running: the box takes more than one API client.
"""
import asyncio
import re
import sys
from pathlib import Path

from aioesphomeapi import APIClient, LogLevel

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.84"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 12
STATES = sys.argv[3:] or ["idle", "speaking", "thinking"]
MOUTH = "n" + "BCDBCEAXBDCBCAXDBC" * 12                      # a talking mouth, 60 ms a letter
EMOTE = "n|" + "nnnhhhHHHnnn" * 8


def env() -> dict:
    out = {}
    for line in Path(r"G:\My Drive\vault\.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


async def main() -> None:
    c = APIClient(HOST, 6053, None, noise_psk=env()["SATELLITE_ESPHOME_KEY"], client_info="box-fps")
    await c.connect(login=True)
    ents, services = await c.list_entities_services()
    face = next(s for s in services if s.name == "face")
    keys = {("mouth" if "mouth" in e.object_id else "emote"): e.key for e in ents if type(e).__name__ == "TextInfo" and ("mouth" in e.object_id or "emote" in e.object_id)}
    seen: list[str] = []
    c.subscribe_logs(lambda m: seen.append(m.message.decode("utf-8", "replace")), log_level=LogLevel.LOG_LEVEL_INFO)
    for st in STATES:
        seen.clear()
        if st == "speaking":
            c.text_command(keys["mouth"], MOUTH)
            c.text_command(keys["emote"], EMOTE)
        await c.execute_service(face, {"state": st})
        await asyncio.sleep(SECONDS)
        rates = [float(m.group(1)) for line in seen for m in [re.search(r"\(([\d.]+) Hz\)", line)] if m]
        stream = [line for line in seen if "frames/s" in line]
        drops = [line for line in seen if "isconnect" in line or "WIFI" in line]
        print(f"{st:10s} on-box tick: {sum(rates) / len(rates):5.1f} Hz (n={len(rates)})" if rates else f"{st:10s} on-box tick: -", end="")
        print(f"   stream: {stream[-1].strip()[-70:]}" if stream else "", f"   wifi events: {len(drops)}" if drops else "")
        if st == "speaking":
            await c.execute_service(face, {"state": "idle"})
    await c.disconnect()


asyncio.run(main())
