#!/usr/bin/env python3
"""SkippyCore's token meter: counts what the local models do, so tools/token_savings.py can say what a paid Haiku would have cost.

llama-swap already counts every request it serves (GET /api/metrics/activity: model, input/cached/output tokens, duration), and every local
caller goes through it: the portal's Core brain, the Ollama shim (flask-api, Mentat, the room hub), the coder agent. This polls that list every
10 s and appends each NEW request to ${CORE_STATE}/meter/tokens-YYYY-MM.jsonl, together with the box's measured power while it ran
(amdgpu power1_average, the Strix Halo package: CPU + iGPU, sampled every second).

One line per request: ts, id, model, src (llama-swap's caller address), in (uncached prompt tokens), cached (prompt tokens reused from
llama.cpp's cache), out, ms, w (mean package watts while it ran), idle_w (the box's idle baseline then), overlap (requests running at once:
they share the extra watts). NEVER the text of a prompt or reply: llama-swap's captures are not read. Stdlib only; runs as a systemd service.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

CORE_ENV = "/etc/skippy-core/core.env"
STATE = Path(os.environ.get("CORE_STATE", "/var/lib/skippy-core"))
LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:8080").rstrip("/")
DEFAULT_IDLE_W = 12.0          # the box measured 9-14 W idle on 2026-10-30; used until ten minutes of samples say otherwise


def read_env(path: str) -> dict:
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def find_power_file(root: str = "/sys/class/hwmon") -> Path | None:
    """The amdgpu hwmon's power1_average (microwatts)."""
    try:
        for d in sorted(Path(root).iterdir()):
            if (d / "name").read_text().strip() == "amdgpu" and (d / "power1_average").exists():
                return d / "power1_average"
    except OSError:
        pass
    return None


def read_watts(path: Path | None) -> float | None:
    try:
        return int(path.read_text().strip()) / 1e6 if path else None
    except (OSError, ValueError):
        return None


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class Meter:
    def __init__(self, out_dir: Path, get, power=lambda: None, now=time.time):
        self.out_dir, self.get, self.power, self.now = out_dir, get, power, now
        self.samples: deque = deque(maxlen=4000)          # (t, watts), one a second
        self.minutes: deque = deque(maxlen=1440)          # one-minute means, a day
        self._minute: list = []
        self.seen: deque = deque(maxlen=3000)              # keys "id|ts" already written
        self.recent: deque = deque(maxlen=200)             # (start, end) of recent requests, for overlap
        self.idle_state = out_dir / "idle.json"
        try:
            self.minutes.extend(json.loads(self.idle_state.read_text()).get("minutes", []))
        except (OSError, ValueError):
            pass
        for row in self._tail():
            self.seen.append(f"{row.get('id')}|{row.get('ts')}")

    def _file(self) -> Path:
        return self.out_dir / f"tokens-{datetime.fromtimestamp(self.now(), timezone.utc):%Y-%m}.jsonl"

    def _tail(self, n: int = 3000) -> list[dict]:
        rows = []
        try:
            for line in self._file().read_text(encoding="utf-8").splitlines()[-n:]:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        except OSError:
            pass
        return rows

    def sample(self) -> None:
        w = self.power()
        if w is None:
            return
        self.samples.append((self.now(), w))
        self._minute.append(w)
        if len(self._minute) >= 60:
            self.minutes.append(round(statistics.fmean(self._minute), 2))
            self._minute = []
            try:
                self.out_dir.mkdir(parents=True, exist_ok=True)
                self.idle_state.write_text(json.dumps({"minutes": list(self.minutes)}))
            except OSError:
                pass

    def idle_w(self) -> float:
        """The 10th percentile of the day's one-minute means: a box that is mostly idle spends its lowest decile idle."""
        if len(self.minutes) < 10:
            return DEFAULT_IDLE_W
        ordered = sorted(self.minutes)
        return ordered[len(ordered) // 10]

    def watts_between(self, a: float, b: float) -> float | None:
        inside = [w for t, w in self.samples if a - 1 <= t <= b + 1]
        if inside:
            return round(statistics.fmean(inside), 2)
        return round(self.samples[-1][1], 2) if self.samples else None

    def poll(self) -> int:
        """Write every request llama-swap has finished since last time. Returns how many."""
        try:
            data = self.get("/api/metrics/activity").get("data") or []
        except Exception:  # noqa: BLE001 — llama-swap restarting: try again next tick
            return 0
        fresh = []
        for r in sorted(data, key=lambda r: r.get("timestamp", "")):
            key = f"{r.get('id')}|{r.get('timestamp')}"
            tok = r.get("tokens") or {}
            if key in self.seen:
                continue
            self.seen.append(key)
            if r.get("resp_status_code", 200) >= 400:
                continue
            if not (tok.get("input_tokens", 0) or tok.get("output_tokens", 0) or tok.get("cache_tokens", 0)):
                continue                                  # a model load or a health check: no tokens
            fresh.append(r)
        if not fresh:
            return 0
        rows = []
        for r in fresh:
            tok = r.get("tokens") or {}
            end = parse_ts(r["timestamp"])
            ms = int(r.get("duration_ms") or 0)
            start = end - ms / 1000
            overlap = 1 + sum(1 for s, e in self.recent if s < end and e > start) + \
                sum(1 for o in rows if o["_s"] < end and o["_e"] > start)
            rows.append({"ts": r["timestamp"], "id": r.get("id"), "model": r.get("model", ""), "src": r.get("src", ""),
                         "in": max(0, int(tok.get("input_tokens") or 0)), "cached": max(0, int(tok.get("cache_tokens") or 0)),
                         "out": max(0, int(tok.get("output_tokens") or 0)), "ms": ms,
                         "w": self.watts_between(start, end), "idle_w": round(self.idle_w(), 2), "overlap": overlap,
                         "_s": start, "_e": end})
        for o in rows:
            self.recent.append((o["_s"], o["_e"]))
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            with self._file().open("a", encoding="utf-8") as f:
                for o in rows:
                    f.write(json.dumps({k: v for k, v in o.items() if not k.startswith("_")}) + "\n")
        except OSError as e:
            print(f"meter: write failed: {e}", file=sys.stderr)
        return len(rows)


def main() -> int:
    key = read_env(CORE_ENV).get("LLM_API_KEY", "")

    def get(path: str):
        req = urllib.request.Request(LLM_URL + path, headers={"Authorization": f"Bearer {key}"} if key else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    pf = find_power_file()
    m = Meter(STATE / "meter", get, lambda: read_watts(pf))
    print(f"meter: polling {LLM_URL}, power from {pf}", flush=True)
    tick = 0
    while True:
        m.sample()
        if tick % 10 == 0:
            m.poll()
        tick += 1
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
