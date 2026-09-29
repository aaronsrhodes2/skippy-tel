"""One-time world imports: a game database carried from skippy-pc to the
games VM, through the portal (neither end can reach the other directly).

  1. The portal asks for it: every home-report reply lists `imports_wanted`
     until the import is done (state in VM metadata `portal-imports`).
  2. The home reporter uploads the dump in chunks (Cloud Run caps a request
     at 32 MB) to PUT /home/imports/<name>; the portal keeps it in /tmp.
  3. Complete + checksum OK → a one-time token and the download URL go into
     VM metadata (`eq-import-token`, `eq-import-url`). On its next start the
     game fetches the dump with that token, loads it, and POSTs .../done.
  4. Done → the file is deleted and the metadata cleared.

If the portal restarts before the VM fetched it, the file is gone: the next
report asks for it again. Today only EverQuest (the Captain's home AkkStack).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

STATE_KEY = "portal-imports"
IMPORTS = {"everquest": {"token_key": "eq-import-token", "url_key": "eq-import-url"}}
DIR = Path(os.environ.get("IMPORTS_DIR", "/tmp/imports"))
MAX_BYTES = 600_000_000


class ImportError_(Exception):
    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


class Imports:
    def __init__(self, vm, clock=time.time):
        self.vm, self.clock = vm, clock
        DIR.mkdir(parents=True, exist_ok=True)

    def _state(self) -> dict:
        try:
            data = json.loads(self.vm.get_metadata(STATE_KEY) or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _save(self, state: dict) -> None:
        self.vm.set_metadata(STATE_KEY, json.dumps(state))

    def _file(self, name: str) -> Path:
        return DIR / f"{name}.sql.gz"

    def status(self, name: str) -> dict:
        entry = self._state().get(name, {})
        state = entry.get("state", "wanted")
        if state == "ready" and not self._file(name).exists():
            state = "wanted"                     # portal restarted: ask skippy-pc again
        return {**entry, "state": state}

    def wanted(self) -> list[str]:
        return [n for n in IMPORTS if self.status(n)["state"] == "wanted"]

    # ── skippy-pc → portal ─────────────────────────────────────────────────
    def receive(self, name: str, offset: int, total: int, sha256: str, chunk: bytes, base_url: str) -> dict:
        if name not in IMPORTS:
            raise ImportError_("unknown import", 404)
        if total <= 0 or total > MAX_BYTES or not sha256:
            raise ImportError_("bad size or checksum")
        part = DIR / f"{name}.part"
        have = part.stat().st_size if part.exists() else 0
        if offset == 0:
            have = 0
            part.write_bytes(b"")
        if offset != have:
            raise ImportError_("offset mismatch", 409, have=have)
        with open(part, "ab") as f:
            f.write(chunk)
        have += len(chunk)
        if have < total:
            return {"have": have}
        if have > total:
            part.unlink(missing_ok=True)
            raise ImportError_("more bytes than announced", 400, have=0)
        digest = hashlib.sha256(part.read_bytes()).hexdigest()
        if digest != sha256.lower():
            part.unlink(missing_ok=True)
            raise ImportError_("checksum mismatch", 400, have=0)
        part.replace(self._file(name))
        token = secrets.token_urlsafe(32)
        keys = IMPORTS[name]
        self.vm.set_metadata(keys["token_key"], token)
        self.vm.set_metadata(keys["url_key"], f"{base_url.rstrip('/')}/games/{name}/import")
        state = self._state()
        state[name] = {"state": "ready", "size": total, "sha256": digest, "received_at": int(self.clock())}
        self._save(state)
        return {"have": have, "complete": True}

    # ── portal → games VM ──────────────────────────────────────────────────
    def authorized(self, name: str, header: str | None) -> bool:
        if name not in IMPORTS or not header or not header.startswith("Bearer "):
            return False
        if self.status(name)["state"] != "ready":     # nothing to fetch: don't ask the Compute API
            return False
        token = self.vm.get_metadata(IMPORTS[name]["token_key"]) or ""
        return bool(token) and hmac.compare_digest(header[7:].strip(), token)

    def path(self, name: str) -> Path | None:
        p = self._file(name)
        return p if p.exists() else None

    def done(self, name: str) -> None:
        self._file(name).unlink(missing_ok=True)
        keys = IMPORTS[name]
        self.vm.set_metadata(keys["token_key"], "")
        self.vm.set_metadata(keys["url_key"], "")
        state = self._state()
        state[name] = {**state.get(name, {}), "state": "done", "done_at": int(self.clock())}
        self._save(state)
