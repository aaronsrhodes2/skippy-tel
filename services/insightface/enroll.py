#!/usr/bin/env python3
"""Hey Buddy enrollment CLI.

Usage (from inside the running insightface container):
  python /app/enroll.py add <name> <photo.jpg>     # enroll a face
  python /app/enroll.py list                       # list enrolled names
  python /app/enroll.py remove <name>              # delete all samples for a name

Or from the PC host (one-shot, no need to docker exec):
  docker compose exec insightface python /app/enroll.py add aaron /data/aaron.jpg

Hits the local FastAPI on 127.0.0.1:8000 — same code path the flask-api
uses. Photos are NOT persisted; only the 512-dim embedding survives.
"""
import sys
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"


def usage():
    print(__doc__)
    sys.exit(2)


def add(name: str, path: str):
    p = Path(path)
    if not p.is_file():
        print(f"file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with p.open("rb") as f:
        r = requests.post(
            f"{BASE}/enroll",
            files={"image": (p.name, f.read(), "image/jpeg")},
            data={"name": name},
            timeout=60,
        )
    print(r.status_code, r.text)
    sys.exit(0 if r.ok else 1)


def list_():
    r = requests.get(f"{BASE}/enrolled", timeout=10)
    print(r.text)


def remove(name: str):
    r = requests.delete(f"{BASE}/enrolled/{name}", timeout=10)
    print(r.text)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage()
    cmd = sys.argv[1]
    if cmd == "add" and len(sys.argv) == 4:
        add(sys.argv[2], sys.argv[3])
    elif cmd == "list":
        list_()
    elif cmd == "remove" and len(sys.argv) == 3:
        remove(sys.argv[2])
    else:
        usage()
