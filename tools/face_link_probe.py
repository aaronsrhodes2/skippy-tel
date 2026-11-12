"""How much does the Wi-Fi link to a pod really carry? Sends it junk datagrams at a set rate beside the live face stream and reads, from the box's own
log, how many arrived (the box counts a datagram with the wrong first byte as `bad` and ignores it, so the picture is untouched).

    services/satellite/.venv/Scripts/python tools/face_link_probe.py [host]

Each step: SECONDS of junk at a rate and datagram size, then the box's counts over that time: junk sent, junk that arrived, and what the real stream lost
meanwhile. A diagnostic run by hand (the hub's stream slows itself while this crowds the link, and recovers after).
"""
import asyncio
import re
import socket
import sys
import time
from pathlib import Path

from aioesphomeapi import APIClient, LogLevel

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.84"
STEPS = [(100, 1400), (200, 1400), (300, 1400), (450, 1400), (200, 700), (300, 700), (300, 1400)]      # (KB a second, bytes a datagram)
SECONDS = 15


def env() -> dict:
    out = {}
    for line in Path(r"G:\My Drive\vault\.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


async def main() -> None:
    c = APIClient(HOST, 6053, None, noise_psk=env()["SATELLITE_ESPHOME_KEY"], client_info="link-probe")
    await c.connect(login=True)
    seen = {"bad": 0, "missing": 0, "dgrams": 0, "lines": 0}

    def on_log(m):
        t = m.message.decode("utf-8", "replace")
        if "frames/s" in t and "dgrams/s" in t:
            g = re.search(r"(\d+) dgrams/s.*missing (\d+).*bad (\d+)", t)
            if g:
                seen["dgrams"] += int(g.group(1)) * 5
                seen["missing"] += int(g.group(2))
                seen["bad"] += int(g.group(3))
                seen["lines"] += 1
    c.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_INFO)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    await asyncio.sleep(6)
    for kb, size in STEPS:
        for k in seen:
            seen[k] = 0
        junk = b"X" + bytes(size - 1)
        gap = size / (kb * 1024)
        sent, t0 = 0, time.perf_counter()
        nxt = t0
        while time.perf_counter() - t0 < SECONDS:
            sock.sendto(junk, (HOST, 10803))
            sent += 1
            nxt += gap
            d = nxt - time.perf_counter()
            if d > 0:
                await asyncio.sleep(d)
            elif sent % 20 == 0:
                await asyncio.sleep(0)
        await asyncio.sleep(5.5)                                       # the box's last 5 s line for this step
        got = seen["bad"]
        print(f"{kb:4d} KB/s of {size} B junk: sent {sent}, arrived {got} ({got / max(1, sent):.0%}); the real stream meanwhile: {seen['dgrams']} datagrams in, {seen['missing']} missing "
              f"({seen['lines']} box lines)", flush=True)
        await asyncio.sleep(6)
    await c.disconnect()


asyncio.run(main())
