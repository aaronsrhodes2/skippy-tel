"""Everything Mentat outputs, kept forever: the Captain's research data (2026-10-23).

One JSON line per request to any compute endpoint (/ask, /tools/<name>, an MCP
tools/call, /wva/solve), appended and never rewritten:

    {"v": 1, "id", "ts" (UTC ISO), "instance" ("home" | "cloud" | ...), "sigma_ground_ref",
     "trace" (the X-Skippy-Trace request header, or null), "endpoint", "tool" (or null),
     "caller": {"ip", "user_agent", "via" (X-Skippy-Caller)},
     "request": {"question", "given"} for /ask, else the arguments as sent,
     "interpreter" (/ask: "keywords + qwen" | "keywords"),
     "understood" (/ask: the route and each step's verb, inputs with units, bindings),
     "status" (HTTP), "ok", "response" (Mentat's whole reply: result with steps, sources,
     provenance tag, clarification, or the error), "ms" (time to answer)}

Where it lives:
  * MENTAT_RESEARCH_DIR (the copy on skippy-pc: SkippyTel/state/mentat-research, a bind mount
    that survives rebuilds): <dir>/<YYYY-MM>.jsonl, one file a month.
  * MENTAT_RESEARCH_BUCKET (the cloud copy): gs://<bucket>/mentat-research/<YYYY>/<MM>/<DD>/
    <HHMMSS>-<instance id>-<n>.jsonl, a batch per object (GCS objects can't be appended to, and
    the service account may only CREATE objects: nothing it wrote can be changed or deleted).
    A batch that can't be uploaded is printed whole to stdout (Cloud Logging) so it isn't lost.

Logging never slows or breaks an answer: record() only puts the line on a queue; a
background thread does the writing, and every failure there is swallowed (and counted).
"""
from __future__ import annotations

import atexit
import datetime as dt
import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
import uuid

VERSION = 1
DIR = os.environ.get("MENTAT_RESEARCH_DIR", "")
BUCKET = os.environ.get("MENTAT_RESEARCH_BUCKET", "")
INSTANCE = os.environ.get("MENTAT_INSTANCE", "")
BATCH_LINES = 200
BATCH_SECONDS = 30
MAX_QUEUE = 20_000
_METADATA = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Log:
    """The queue and its writer. One per process (``LOG``); tests make their own."""

    def __init__(self, directory: str = DIR, bucket: str = BUCKET, instance: str = INSTANCE,
                 uploader=None, clock=_now):
        self.directory, self.bucket = directory, bucket
        self.instance = instance or ("cloud" if bucket else "home" if directory else "")
        self.uploader = uploader or _upload_gcs
        self.clock = clock
        self.dropped = self.failed = 0
        self.last_error = ""
        self._q: queue.Queue = queue.Queue(MAX_QUEUE)
        self._batch: list[str] = []
        self._batch_started = 0.0
        self._seq = 0
        self._tag = uuid.uuid4().hex[:8]                # tells two Cloud Run instances' objects apart
        self._lock = threading.Lock()
        self._io = threading.Lock()           # one writer at a time: the thread, or drain()
        self._thread = None

    @property
    def enabled(self) -> bool:
        return bool(self.directory or self.bucket)

    # ── the request side: cheap, never raises ──────────────────────────────
    def record(self, entry: dict) -> None:
        if not self.enabled:
            return
        try:
            entry = {"v": VERSION, "id": uuid.uuid4().hex, "ts": self.clock().isoformat(timespec="milliseconds"),
                     "instance": self.instance, "sigma_ground_ref": os.environ.get("SIGMA_GROUND_REF") or "dev",
                     **entry}
            self._q.put_nowait(entry)
            self._start()
        except queue.Full:
            self.dropped += 1
        except Exception:  # noqa: BLE001 — research logging must never break an answer
            self.dropped += 1

    def _start(self) -> None:
        if self._thread is None:
            with self._lock:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name="mentat-research", daemon=True)
                    self._thread.start()

    # ── the writer side ────────────────────────────────────────────────────
    def _run(self) -> None:
        while True:
            try:
                entry = self._q.get(timeout=5)
            except queue.Empty:
                entry = None
            with self._io:
                try:
                    if entry is not None:
                        self._write(entry)
                    if self._batch and (len(self._batch) >= BATCH_LINES
                                        or time.monotonic() - self._batch_started >= BATCH_SECONDS):
                        self.flush_batch()
                except Exception as exc:  # noqa: BLE001
                    self.failed += 1
                    self.last_error = str(exc)[:300]
                finally:
                    if entry is not None:
                        self._q.task_done()

    def _write(self, entry: dict) -> None:
        line = json.dumps(entry, default=str, ensure_ascii=False)
        if self.directory:
            os.makedirs(self.directory, exist_ok=True)
            path = os.path.join(self.directory, f"{entry['ts'][:7]}.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if self.bucket:
            if not self._batch:
                self._batch_started = time.monotonic()
            self._batch.append(line)

    def flush_batch(self) -> None:
        if not self._batch:
            return
        lines, self._batch = self._batch, []
        self._seq += 1
        now = self.clock()
        name = f"mentat-research/{now:%Y/%m/%d}/{now:%H%M%S}-{self._tag}-{self._seq:06d}.jsonl"
        body = ("\n".join(lines) + "\n").encode()
        try:
            self.uploader(self.bucket, name, body)
        except Exception as exc:  # noqa: BLE001 — keep the data in Cloud Logging instead
            self.failed += 1
            self.last_error = str(exc)[:300]
            for line in lines:
                print(json.dumps({"event": "mentat_research_unsaved", "error": self.last_error,
                                  "record": json.loads(line)}, default=str), flush=True)

    def drain(self, timeout: float = 5.0) -> None:
        """Write everything queued now, and upload the batch (tests, and process exit)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                entry = self._q.get_nowait()
            except queue.Empty:
                if self._q.unfinished_tasks == 0:
                    break
                time.sleep(0.01)              # the writer thread has one in hand
                continue
            with self._io:
                try:
                    self._write(entry)
                except Exception as exc:  # noqa: BLE001
                    self.failed += 1
                    self.last_error = str(exc)[:300]
                finally:
                    self._q.task_done()
        with self._io:
            try:
                self.flush_batch()
            except Exception:  # noqa: BLE001
                pass

    def status(self) -> dict:
        return {"enabled": self.enabled, "instance": self.instance, "dir": bool(self.directory),
                "bucket": self.bucket or None, "queued": self._q.qsize(), "dropped": self.dropped,
                "failed": self.failed, "last_error": self.last_error or None}


def _upload_gcs(bucket: str, name: str, body: bytes) -> None:
    """Create one object with the runtime identity's token (no SDK: one HTTP call).
    ifGenerationMatch=0: create only, never overwrite."""
    req = urllib.request.Request(_METADATA, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=5) as r:
        token = json.loads(r.read())["access_token"]
    url = (f"https://storage.googleapis.com/upload/storage/v1/b/{urllib.parse.quote(bucket, safe='')}/o"
           f"?uploadType=media&ifGenerationMatch=0&name={urllib.parse.quote(name, safe='')}")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/x-ndjson"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def understood(result) -> dict | None:
    """What Mentat read an /ask question as, lifted out of its reply so the record says it in one place:
    the route and each step's verb, inputs (with units) and bindings."""
    if not isinstance(result, dict):
        return None
    inputs = result.get("inputs") if isinstance(result.get("inputs"), dict) else {}
    steps = inputs.get("understood")
    steps = json.loads(json.dumps(steps, default=str)) if steps is not None else None   # a copy: the reply stays as sent
    if steps is None:
        return {"route": inputs.get("route")} if inputs.get("route") else None
    try:
        from sigma_ground.materia.inputs import verb_inputs
        for st in steps:
            units = {n: m["units"] for n, m in verb_inputs(st["verb"]).items()}
            st["units"] = {n: units.get(n, "") for n in st.get("params", {})}
    except Exception:  # noqa: BLE001 — the record is still worth keeping without the units
        pass
    return {"route": inputs.get("route"), "steps": steps}


LOG = Log()
atexit.register(LOG.drain)
