"""The screens of skippy-pc's world, for a Skippy-vision moment (portal/vision.py): this PC's monitors and the room boxes' displays.

The bridge is the one piece of the chain that runs on Windows itself (the home reporter is a container and can't see the desktop), so
the reporter's `flash` job asks it here: POST /flash/screens -> {"files": [{"name", "b64"}], "errors": [str]}. Every source is best
effort: a monitor that can't be read or a box that doesn't answer in time is a line in `errors`, and the rest still come back.

  this PC          each monitor, a PNG (at most MAX_WIDTH wide), named pc-screen-1.png ... (the monitors' own order)
  the room boxes   services/satellite/rooms/shot.py <host> <out.png>, run in the satellite's own virtualenv (it needs aioesphomeapi), one
                   box after another, named pod-<room>.png; the rooms are SATELLITE_ROOMS ("theater=esphome://192.168.0.84")

The names are the ones portal/vision.py takes (HOME_NAME): nothing else is accepted over there.
"""
from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(os.getenv("SKIPPY_COMPOSE_DIR", r"D:\Aaron\development\SkippyTel"))
SHOT = ROOT / "services" / "satellite" / "rooms" / "shot.py"
VENV_PYTHON = ROOT / "services" / "satellite" / ".venv" / "Scripts" / "python.exe"
VAULT_ENV = Path(os.getenv("SKIPPY_VAULT_ENV", r"G:\My Drive\vault\.env"))
MAX_MONITORS = 4
MAX_WIDTH = 2560                 # a 4K monitor's PNG is several MB: this keeps a moment's screens light and still readable
POD_TIMEOUT_S = 25
ROOM = re.compile(r"[a-z0-9][a-z0-9-]{0,29}")
HOST = re.compile(r"[A-Za-z0-9.-]{1,80}")


def monitors() -> list[tuple[int, int, int, int]]:
    """Each monitor's rectangle (left, top, right, bottom) in desktop pixels, in the order Windows lists them; [] where that can't be asked."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []
    if not hasattr(ctypes, "windll"):
        return []
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)            # real pixels, not the scaled ones
    except Exception:  # noqa: BLE001 — already set, or an older Windows: the grab still works
        pass
    found: list[tuple[int, int, int, int]] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def each(_monitor, _dc, rect, _data):
        r = rect.contents
        found.append((r.left, r.top, r.right, r.bottom))
        return 1
    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(each), 0)
    return found[:MAX_MONITORS]


def grab(rect: tuple[int, int, int, int]) -> bytes:
    """One monitor as PNG bytes, scaled down to MAX_WIDTH when wider."""
    from PIL import Image, ImageGrab                              # Pillow: the host's Python has it (the bridge's own requirement)
    shot = ImageGrab.grab(bbox=rect, all_screens=True)
    if shot.width > MAX_WIDTH:
        shot = shot.resize((MAX_WIDTH, round(shot.height * MAX_WIDTH / shot.width)), Image.LANCZOS)
    out = io.BytesIO()
    shot.convert("RGB").save(out, "PNG", optimize=False)
    return out.getvalue()


def rooms_from(text: str) -> list[tuple[str, str]]:
    """[(room name, host)] from SATELLITE_ROOMS: "theater=esphome://192.168.0.84,den=192.168.1.41". Anything that isn't a plain name and host is skipped."""
    out = []
    for part in (text or "").split(","):
        name, _, where = part.strip().partition("=")
        host = re.sub(r"^[a-z]+://", "", where.strip()).split(":")[0].split("/")[0]
        if ROOM.fullmatch(name.strip().lower()) and HOST.fullmatch(host):
            out.append((name.strip().lower(), host))
    return out[:8]


def configured_rooms(env=None, vault=VAULT_ENV) -> list[tuple[str, str]]:
    """The rooms: SATELLITE_ROOMS from the environment, else from the vault .env (the hub reads the same line)."""
    text = (env if env is not None else os.environ).get("SATELLITE_ROOMS", "")
    if not text:
        try:
            for line in Path(vault).read_text(encoding="utf-8").splitlines():
                if line.startswith("SATELLITE_ROOMS="):
                    text = line.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            pass
    return rooms_from(text)


def pod_png(host: str, run=subprocess.run, python=VENV_PYTHON, shot=SHOT) -> bytes:
    """One box's screen as PNG bytes (shot.py asks the box to post it, and waits for the hub's media server or its own). Raises on failure."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "box.png"
        done = run([str(python), str(shot), host, str(out)], capture_output=True, timeout=POD_TIMEOUT_S, cwd=str(shot.parent))
        if done.returncode != 0 or not out.is_file():
            raise RuntimeError((done.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] and
                               (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1][:160] or f"exit {done.returncode}")
        return out.read_bytes()


def capture(monitors_fn=monitors, grab_fn=grab, rooms=None, pod_fn=pod_png) -> dict:
    """{"files": [{"name", "b64"}], "errors": [str]}: this PC's monitors, then each room box's screen."""
    files, errors = [], []
    try:
        for n, rect in enumerate(monitors_fn(), 1):
            try:
                files.append({"name": f"pc-screen-{n}.png", "b64": base64.b64encode(grab_fn(rect)).decode()})
            except Exception as exc:  # noqa: BLE001 — one monitor not readable (a locked screen): say so, take the others
                errors.append(f"pc-screen-{n}: {type(exc).__name__}: {str(exc)[:80]}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"monitors: {type(exc).__name__}: {str(exc)[:80]}")
    for name, host in (configured_rooms() if rooms is None else rooms):
        try:
            files.append({"name": f"pod-{name}.png", "b64": base64.b64encode(pod_fn(host)).decode()})
        except Exception as exc:  # noqa: BLE001 — a box that is off or busy
            errors.append(f"pod-{name}: {type(exc).__name__}: {str(exc)[:100]}")
    return {"files": files, "errors": errors}
