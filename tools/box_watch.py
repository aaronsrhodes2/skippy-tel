"""Watches a room box's own log for Wi-Fi drops, crashes and resets, and keeps a line for each in state/box-watch.log (a diagnostic, run by hand).

    services/satellite/.venv/Scripts/python tools/box_watch.py [host]
"""
import asyncio
import sys
import time
from pathlib import Path

from aioesphomeapi import APIClient, LogLevel

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.84"
OUT = Path(__file__).resolve().parents[1] / "state" / "box-watch.log"
KEYS = ("net]", "WIFI", "crash", "CRASH", "Reason", "reboot", "Reset", "rssi", "Wi-Fi", "wifi")


def env() -> dict:
    out = {}
    for line in Path(r"G:\My Drive\vault\.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def note(text: str) -> None:
    with OUT.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {text}\n")


async def main() -> None:
    key = env()["SATELLITE_ESPHOME_KEY"]
    while True:
        c = APIClient(HOST, 6053, None, noise_psk=key, client_info="box-watch")
        try:
            await c.connect(login=True)
            note("connected")
            c.subscribe_logs(lambda m: note(m.message.decode("utf-8", "replace")[:200]) if any(k in m.message.decode("utf-8", "replace") for k in KEYS) else None,
                             log_level=LogLevel.LOG_LEVEL_INFO)
            ents, _ = await c.list_entities_services()
            sig = next((e.key for e in ents if "signal" in e.object_id), None)
            up = next((e.key for e in ents if e.object_id == "uptime"), None)
            last = {}
            c.subscribe_states(lambda s: last.__setitem__(s.key, s.state))
            gone = asyncio.Event()
            c._on_stop = lambda expected: gone.set()
            while True:
                await asyncio.sleep(30)
                note(f"signal {last.get(sig)} dBm, up {last.get(up)} s")
        except Exception as e:                                       # noqa: BLE001
            note(f"lost: {type(e).__name__}: {e}")
        finally:
            try:
                await c.disconnect()
            except Exception:                                        # noqa: BLE001
                pass
        await asyncio.sleep(3)


asyncio.run(main())
