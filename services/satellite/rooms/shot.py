"""A screenshot of a room box's screen: python services/satellite/rooms/shot.py [host] [out.png]

Starts a one-shot receiver on the media port (10800: stop the hub first if it is running), asks the box to post its screen to it
(the box's `screenshot` action), and writes a PNG. The box key is SATELLITE_ESPHOME_KEY in the vault .env.
"""
import asyncio, struct, sys, zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from aiohttp import web  # noqa: E402
from aioesphomeapi import APIClient  # noqa: E402
from media import local_address  # noqa: E402


def env() -> dict:
    out = {}
    for line in Path(r"G:\My Drive\vault\.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def png(w: int, h: int, rgb: bytes) -> bytes:
    raw = b"".join(b"\0" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


async def main(host: str, out: Path) -> None:
    got: asyncio.Future = asyncio.get_running_loop().create_future()
    async def post(req):
        if not got.done():
            got.set_result((req.headers.get("X-Dims", "480,480,960"), await req.read()))
        return web.Response(text="ok")
    app = web.Application(client_max_size=4_000_000); app.router.add_post("/shot", post)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 10800).start()
    c = APIClient(host, 6053, None, noise_psk=env()["SATELLITE_ESPHOME_KEY"])
    await c.connect(login=True)
    _, services = await c.list_entities_services()
    svc = next(s for s in services if s.name == "screenshot")
    await c.execute_service(svc, {"url": f"http://{local_address(host)}:10800/shot"})
    dims, data = await asyncio.wait_for(got, 30)
    w, h, stride = (int(x) for x in dims.split(","))
    rgb = bytearray()
    for y in range(h):
        row = data[y * stride:y * stride + w * 2]
        for i in range(0, w * 2, 2):
            v = row[i] | (row[i + 1] << 8)
            rgb += bytes(((v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31))
    out.write_bytes(png(w, h, bytes(rgb)))
    print("wrote", out, f"{w}x{h}")
    await c.disconnect(); await runner.cleanup()


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "192.168.0.84", Path(sys.argv[2] if len(sys.argv) > 2 else "box.png")))
