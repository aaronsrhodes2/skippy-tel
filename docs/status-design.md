# Status: one page for the whole house, and one place for every log (design, 2026-10-25)

The Captain's ask: now that there is a quorum of machines (skippy-portal, skippy-pc, skippy-core), the portal (the one with the best
uptime) shows a **live map** of the machines, the containers on each, and the models and MCP servers inside each container. It
**replaces** the Recovery pane and the monitoring panes. And the portal becomes the **universal log intake**: crash data first, then
development, experiment and chat-progress logs.

Not built yet. This is the plan to agree on.

## What exists today (the survey)

| Piece | Where | What happens to it |
|---|---|---|
| Recovery pane, "Put everything back" | `templates/_recovery.html`, `recovery_web.py`, `doctor.py`, `starter.py` | Becomes the Status page's top key and its Repairs pane. The doctor stays the brain |
| `recovery.ACTIONS`, `PC_SERVICES` | `portal/recovery.py` | Leftovers of the old many-button design, used by no route: deleted |
| /me panes: Home · skippy-pc, Core · skippy-tel, Server, Watcher | `templates/me.html` | Their content moves to Status. The panes become Graveyard entries (`me-<pane>`), retired once Status is live |
| Admin > Server logs (games VM serial ports) | `/admin/logs`, `diagnostics.py` | Becomes one source in the Logs pane (filter: games VM) |
| Home report: doctor checks, capabilities, health | `reporter.gather()` | Feeds the skippy-pc column, unchanged |
| Starter status: docker counts, GPU, UPS | `tools/skippy_starter.py` | Feeds the skippy-pc column. Gains the container list (below) |
| skippy-core's deploy and doctor state | `status.json`, `doctor.json` on the box | **Reaches nothing today.** skippy-core starts reporting itself (below) |
| The Captain's MCP servers | bridge `mcp_access.py`, the `mcp_list` job | Listed under the bridge on skippy-pc |
| Log aggregation | none | **Nothing exists**: no branch, worktree or service. The search found only Dozzle (local), the games VM's serial logs and usage telemetry. Designed fresh here |

## Part 1: the live map

### What each machine says, and how it gets to the portal

Each machine reports **out** to the portal on its own. No machine's view depends on another being up, which is what makes this a
quorum and not a chain:

- **skippy-portal (Cloud Run):**
  - from itself: its version, revision and start time (`buildinfo`);
  - the deploy pipeline (`recovery.pipeline()`);
  - its own checks: Skippy Core answering (`doctor.core_test`), the games VM status, Mentat cloud's `/health`.
- **skippy-pc:**
  - **the starter** (on the Windows host; Docker can die without it dying) adds `containers` to the status it already sends: for every
    container, its name, state, health, restart count, image, uptime, and the CPU and memory from `docker stats`;
  - **the home report** keeps its doctor checks, capabilities, pop-ups and bridge state;
  - **the bridge's MCP list** comes once a minute, cached, never per page view.
- **skippy-core (new: `core/reporter`, a systemd timer every 30 s):**
  - POSTs `/home/core-report` with the home token, the same door its UPS events already use;
  - **it carries:** `docker ps` and `docker stats`; the deployer's `status.json` and `doctor.json`; llama-swap's `/running` (which models
    are loaded); the shim's names (which old names point at which slot); memory, disk, GPU busy; the UPS.
  - **this turns the "busy" guess into a fact:** today the portal can only infer that skippy-core is busy from a slow 8-token reply.
- **games VM:**
  - unchanged: the ops agent's container snapshot (serial port 2), read only while the Status page is open.

### The model of the map (`portal/status.py`)

One tree, built from the latest of each report:

```
machine  (portal | skippy-pc | skippy-core | games-vm)
  state: ok | degraded | down | stale | off      reason, since, report age
  vitals: CPU, memory, disk, GPU, UPS
  containers[]
    name, state, health, restarts, uptime, cpu, mem
    serves[]     what is inside, each with its own lamp:
      model      e.g. "skippy-chat → Qwen3.6-35B-A3B Q8 (resident, loaded)"   from llama-swap /running + core/models.json
      model      e.g. "bge-m3 (resident)"                                       from popups.json + the pop-up's /health
      mcp        e.g. "skippy-house (3 tools)", "mentat (cloud)"                from mcp_list / mcp_test
      endpoint   e.g. "Mentat :7350 (tailnet)", "music gate (Funnel :443)"
    checks[]     the doctor's checks that belong to it (by name), with what the doctor did lately
```

**What a lamp means:**

| Lamp | Meaning |
|---|---|
| ok | healthy |
| degraded | up but a check failed (Ollama answers, its default model is missing) |
| down | not answering |
| stale | no report within 3 intervals: grey, never green |
| off | stopped on purpose (the starter's stop, a pop-up asleep, the VM off) |

Where the names of what serves what come from: `core/models.json` (skippy-core's slots), the pop-ups' `popups.json`, and one small
table in `status.py` for the rest (flask-api, the gates, the bridge). A test fails when a compose service has no entry.

### The page (`/status`, Admin only, on the bridge)

One phone screen, no scrolling, glyphs, a deck of panes flipped like the other bridge apps. It becomes a launcher tile and Admin's
first control.

1. **Map** (the first pane):
   - the machines are side by side in landscape and stacked one per swipe in portrait;
   - inside each machine, its containers are lamps;
   - tapping a container opens its card: what it serves (models, MCPs, endpoints), its checks, its last 20 log lines (Part 2) and one
     **Heal** key (the doctor's own `heal`, the same rules: never mid-run, 3 tries an hour);
   - the one **Put everything back** key sits on the top bar of every pane, exactly as today.
2. **Problems:** only what isn't ok, oldest first, each with why and since. The Watcher's lines move here too (an access request
   waiting, the VM over 85% memory).
3. **Repairs:** what the doctor did and when (`doctor.actions`), the repair in progress, the starter's last results.
4. **Deploys:** the portal's pipeline (ahead, stuck, failed); skippy-core's deploy state (`status.json`, at last visible); skippy-pc's
   checkout against main.
5. **Logs:** Part 2's search.

It refreshes every 10 s while open (one JSON call, `GET /status/state`). Nothing is fetched while it's closed.

**What goes away once it's live:**
- the Recovery pane;
- the /me panes Home, Core and Server (retired through the Graveyard, so a revive is one tap);
- `/admin/logs` (it redirects to Status > Logs filtered to the games VM);
- `recovery.ACTIONS` and `PC_SERVICES`;
- the stale "Recovery cards" text in docs/portal/README.md.

## Part 2: the portal as the universal log intake

### One record, every source

```json
{"ts": "2026-10-25T21:04:05.123Z", "host": "skippy-pc", "source": "docker/skippytel-tts-studio", "level": "error",
 "kind": "crash", "msg": "CUDA out of memory ...", "trace": "chat-1f3a", "fields": {"exit_code": 137, "restarts": 4}}
```

**`kind`:** what the record is about.

| `kind` | What it records |
|---|---|
| `crash` | a container died or restarted, OOM, a traceback, a reboot, a UPS event |
| `error`, `warn`, `info` | ordinary service logs |
| `dev` | an agent run started, ended, committed or pushed; a deploy; a test run |
| `experiment` | a MineBench run, a benchmark, a pop-up's measurements |
| `chat` | a turn's progress |

**`trace`:** ties records across machines. The chat turn id that Mentat's research already uses also rides on the house, picture and
pop-up jobs, so one chat turn's path across all three machines reads as one story.

### Intake

| Route | Who calls it | Rules |
|---|---|---|
| `POST /logs/ingest` | skippy-pc's shipper, skippy-core's reporter, the games VM's ops agent (machine auth: the home token, or the VM's metadata token) | Batches of up to 500 records, 16 KB each; per-host rate limits; secrets redacted at the door (`diagnostics`' redaction, plus the vault's keys by name) |
| `POST /logs/client` | the browser: `window.onerror` and unhandled promise rejections on every portal page (`static/crashlog.js`) | Signed-in people only, 20 a minute, no page content: the error, the file and line, the page path, the browser |
| in-process | the portal itself: a `logging` handler, Flask's 500 handler (the traceback) and every `log` line the doctor, the patient queue and the starter queue already print | |

Every route goes into `test_route_access`'s REGISTRY (machine, beacon).

### Where it is kept

1. **Live:** an in-memory ring of the last 20,000 records. The Logs pane and every container card read it, instantly.
2. **30 days, searchable:**
   - each record is also printed as one structured JSON line, so **Cloud Logging** keeps it with no new service;
   - the Logs pane searches older records through the Logging API (by host, source, kind, level, trace and text);
   - Cloud Logging's free tier is 50 GB a month; the whole house logs far less.
3. **Forever, for the kinds worth keeping:**
   - `crash`, `dev`, `experiment` and `chat` are appended to `logs/<kind>/<YYYY-MM-DD>.ndjson` in a new private bucket,
     `skippy-tel-logs`, flushed every minute and at shutdown;
   - the bucket is made by `infra/bootstrap`, so it needs one Cloud Shell run of the bootstrap.

### The shippers (store and forward, so crash data survives the crash)

Each machine writes to a local spool first and sends from the spool, so nothing is lost while the network, the portal or Docker is down.
A crash's last lines are on disk before anything tries to send them.

- **skippy-pc: `tools/log_shipper.py`, run by the starter** (the starter is already the host process that outlives Docker). It collects:
  - `docker events` (die, oom, restart, health_status), each as a `crash` or `warn` with the container's last 50 log lines attached;
  - every container's own log at warning and above (`docker logs --since <cursor>`), and info lines from the services that print
    progress (agent runs, pop-up jobs);
  - the host's own logs: `state/starter.log`, `state/bridge-supervisor.log`, `state/starter-bridge.log`;
  - Windows: unexpected reboots and shutdowns (the System event log's 41 / 1074 / 6008), and the UPS's on-battery events.
- **skippy-core: in `core/reporter`:**
  - `journalctl` from a saved cursor (the deployer, the doctor, NUT, the kernel's OOM killer, docker);
  - `docker events` the same way as skippy-pc.
- **games VM:** the ops agent adds the containers' crash and restart events to what it sends; the serial snapshot stays as the
  fallback.
- **dev, experiment and chat progress come from where they happen:**
  - **dev:**
    - the bridge logs each agent run's start and end (brain, folder, steps, commits, pushes);
    - `ship.py` logs each deploy's outcome;
    - CI posts its result from the workflows;
  - **experiment:**
    - `tools/skippy_log.py` is a two-line helper (`log("experiment", "minebench v4 done", blocks=74451, ...)`) used by the
      benchmark tools, `retest_failures.py` and the voxel renderer;
    - the pop-ups' job results carry their timings;
  - **chat:**
    - for each turn: the mode, the brain, the tools it called, the time each took, any error or fallback (Mentat forced, a picture
      drawn by the portal);
    - **metadata only, never the words:** the chats themselves already live in `chat_history.py` with their own retention, and a
      guest's chat must not outlive its 30 days through the logs.

## Phases

1. **The map, from what already flows:**
   - the starter's container list;
   - skippy-core's own report;
   - `status.py` and the page's Map, Problems, Repairs and Deploys panes;
   - the Recovery pane retired.
2. **Log intake:**
   - the routes, the ring, the structured output to Cloud Logging;
   - the portal's own and the browsers' errors;
   - skippy-pc's shipper with its spool;
   - the Logs pane and the container cards' log lines.
3. **Everything else:**
   - skippy-core's journal and the games VM's events;
   - dev, experiment and chat-progress records;
   - the bucket archive (needs the bootstrap run);
   - the old /me panes and `/admin/logs` retired.

Each phase ends deployed and checked on the real machines, with tests: `test_route_access`, a test per shipper's spool, and the map
built from recorded reports of all three machines.

## Decisions for the Captain

1. **Chat logs carry metadata only, never what was said.** Recommended, for the guests' 30-day promise.
2. **Retention:** 30 days searchable for everything; `crash`, `dev`, `experiment` and `chat` kept forever in the bucket. A different
   split is a setting.
3. **The bucket needs one bootstrap run in Cloud Shell** (`infra/bootstrap`). Until then phase 3 keeps the archive in Cloud Logging
   only.
