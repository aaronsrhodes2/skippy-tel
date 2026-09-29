#!/usr/bin/env python3
"""Discord setup from the command line — the same thing the portal's
Admin → "Set up / repair Discord channels" button does (prefer the button;
it writes the result straight into the VM's settings).

  DISCORD_BOT_TOKEN=... python scripts/discord_setup.py [--invite gPTVS7Hmj] [--games valheim ...]

Writes DISCORD_* keys into games/.env; then `scripts/games.sh env`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))

import discord_admin  # noqa: E402
import envfile  # noqa: E402

ENV_FILE = ROOT / "games" / ".env"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--invite", default="gPTVS7Hmj")
    ap.add_argument("--games", nargs="+", default=list(discord_admin.notify.GAMES),
                    choices=list(discord_admin.notify.GAMES))
    args = ap.parse_args(argv)

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("set DISCORD_BOT_TOKEN")
    if not ENV_FILE.exists():
        sys.exit("copy games/.env.example to games/.env first")
    text = ENV_FILE.read_text()
    updates = discord_admin.setup(token, args.invite, args.games, envfile.parse(text))
    ENV_FILE.write_text(envfile.merge(text, updates))
    print(f"wrote {len(updates)} Discord settings to games/.env — next: scripts/games.sh env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
