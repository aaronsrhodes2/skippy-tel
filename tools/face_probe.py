"""Measures the face stream from the box's side: its own stream counters, Wi-Fi signal and face state, one line each, in state/face-probe.log.

    services/satellite/.venv/Scripts/python tools/face_probe.py [host] [minutes]

The box logs every 5 s "N frames/s, N dgrams/s, N tiles/s (...), missing N, partial frames N, dropped N, bad N, vsync-synced a/b".
Lines are stamped with the face state ("Face" text sensor), the signal (dBm) and the cumulative "Stream loss", so loss can be read against what he was doing.
A diagnostic run by hand; it is a second API client beside the hub (read only).
"""
import asyncio
import sys
import time
from pathlib import Path

from aioesphomeapi import APIClient, LogLevel

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.84"
MINUTES = float(sys.argv[2]) if len(sys.argv) > 2 else 10
OUT = Path(__file__).resolve().parents[1] / "state" / "face-probe.log"


def env() -> dict:
    out = {}
    for line in Path(r"G:\My Drive\vault\.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


async def main() -> None:
    key = env()["SATELLITE_ESPHOME_KEY"]
    c = APIClient(HOST, 6053, None, noise_psk=key, client_info="face-probe")
    await c.connect(login=True)
    ents, _ = await c.list_entities_services()
    by_key = {e.key: e.object_id for e in ents}
    last: dict = {}

    def on_state(s):
        last[by_key.get(s.key, s.key)] = getattr(s, "state", None)
    c.subscribe_states(on_state)
    f = OUT.open("a", encoding="utf-8")
    f.write(f"# probe {time.strftime('%Y-%m-%d %H:%M:%S')} {HOST} {MINUTES} min; entities: {sorted(by_key.values())}\n")

    def on_log(m):
        t = m.message.decode("utf-8", "replace")
        if "frames/s" in t and "dgrams/s" in t:
            sig = next((v for k, v in last.items() if "signal" in str(k)), None)
            face = last.get("face")
            loss = last.get("stream_loss")
            f.write(f"{time.strftime('%H:%M:%S')} face={face} signal={sig} loss_total={loss} | {t.strip()[:260]}\n")
            f.flush()
    c.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_INFO)
    await asyncio.sleep(MINUTES * 60)
    await c.disconnect()
    f.close()


asyncio.run(main())
