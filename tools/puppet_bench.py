"""Runs the pod's puppet-mode spike: asks the box to draw its built-in scene with its own rasterizer, prints the timing it logs, and takes its screenshot.

    services/satellite/.venv/Scripts/python tools/puppet_bench.py [host] [out.png]
"""
import asyncio
import subprocess
import sys
from pathlib import Path

from aioesphomeapi import APIClient, LogLevel

ROOT = Path(__file__).resolve().parents[1]
HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.84"
OUT = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / ".skippy-shots" / "puppet-pod.png")


def env() -> dict:
    out = {}
    for line in Path(r"G:\My Drive\vault\.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


async def main() -> None:
    c = APIClient(HOST, 6053, None, noise_psk=env()["SATELLITE_ESPHOME_KEY"], client_info="puppet-bench")
    await c.connect(login=True)
    c.subscribe_logs(lambda m: print("POD:", m.message.decode("utf-8", "replace").strip()[:300]) if b"puppet" in m.message else None,
                     log_level=LogLevel.LOG_LEVEL_INFO)
    _, services = await c.list_entities_services()
    svc = next(s for s in services if s.name == "puppet_bench")
    await c.execute_service(svc, {})
    await asyncio.sleep(4)
    r = subprocess.run([sys.executable, str(ROOT / "services" / "satellite" / "rooms" / "shot.py"), HOST, OUT], capture_output=True, text=True, timeout=90)
    print("shot:", (r.stdout + r.stderr).strip()[-300:])
    await asyncio.sleep(1)
    await c.disconnect()


asyncio.run(main())
