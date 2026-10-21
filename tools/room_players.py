#!/usr/bin/env python3
"""Open Bilby's Audio Service as the speakers on skippy-pc: one browser window per room, each pinned to its own audio output.

    python tools/room_players.py            # open both rooms (portal/player.py ROOMS)
    python tools/room_players.py downstairs # one room
    python tools/room_players.py --print    # show the commands, start nothing

Each window is /music?room=<key> in a dedicated Chrome profile (D:/Aaron/development/.skippy-rooms-profile by default, or
$SKIPPY_ROOMS_PROFILE): sign in to the portal in it ONCE as the Captain and it stays signed in. The flags let a window that
nobody touches make sound (no tap) and keep running while it is hidden or covered. Add it to Windows startup to have the
speakers ready after a reboot. Needs Chrome (or Edge via $SKIPPY_CHROME)."""
import os
import subprocess
import sys
from pathlib import Path

PORTAL = os.environ.get("SKIPPY_PORTAL_URL", "https://makerportal.live").rstrip("/")
ROOMS = ("downstairs", "upstairs")
CANDIDATES = (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
FLAGS = ("--autoplay-policy=no-user-gesture-required",           # a window nobody taps still makes sound
         "--use-fake-ui-for-media-stream",                       # grants the microphone prompt, which is what unlocks output names
         "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
         "--disable-backgrounding-occluded-windows", "--no-first-run", "--no-default-browser-check")


def chrome() -> str:
    found = os.environ.get("SKIPPY_CHROME") or next((c for c in CANDIDATES if Path(c).exists()), "")
    if not found:
        sys.exit("No Chrome or Edge found: set SKIPPY_CHROME to its path.")
    return found


def command(room: str, exe: str, profile: Path) -> list[str]:
    return [exe, f"--user-data-dir={profile}", *FLAGS, f"--app={PORTAL}/music?room={room}", "--window-size=420,300"]


def main(argv: list[str]) -> int:
    dry = "--print" in argv
    rooms = [a for a in argv if not a.startswith("--")] or list(ROOMS)
    bad = [r for r in rooms if r not in ROOMS]
    if bad:
        sys.exit(f"Unknown room {bad[0]!r}; rooms are {', '.join(ROOMS)}.")
    profile = Path(os.environ.get("SKIPPY_ROOMS_PROFILE", r"D:\Aaron\development\.skippy-rooms-profile"))
    exe = chrome() if not dry else (os.environ.get("SKIPPY_CHROME") or "chrome")
    for room in rooms:
        cmd = command(room, exe, profile)
        print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
        if not dry:
            subprocess.Popen(cmd, close_fds=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
