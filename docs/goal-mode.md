# Goal mode: an unattended coder on purple Skippy (design, 2026-10-26)

Status: **design, not built.** Split agreed with the Shifting Prophesy session (2026-10-26): it builds the engine side in a worktree
(GOAL.md writer and segment handoff, done-checks as the SDK engine's Stop hook, the fingerprint rule, the `goal/<slug>` branch and reset;
the cruise's referee loop is the same hook with the referee as the check); the infra session builds the queue and windows, the quiet-gate
integration, the Discord park/report, reviewer step (b), the orange plumbing and Core-slot telemetry. The boundary: the engine emits typed
events, calls a done-check hook and writes the goal state file; the infra side consumes them. All of it lives in `services/claude-bridge` (the engine, the supervisor) and a little in `portal/workshop.py`;
build it only in a window the Shifting Prophesy session gives (it owns the bridge and the Core coder while its trial runs).

The Captain's aim: purple Skippy (the local coder on skippy-core) works for hours, alone, without asking "want to continue?", and ends in
reviewed, rollback-able commits. "Spendependant" (his word): the default path costs electricity and GPU time, never tokens.

## What fable's "goal" really was

Its results came from the **loop** (plan, act, check, retry against a written goal), not from the many parallel threads. We run one worker:
a **single FIFO queue of goals** with a sleepy window behind it. Nothing below needs parallelism; a reviewer or a repair is just the next
step in the same queue.

## The pieces

1. **Goal mode (a mode, not always on).** A conversation is switched to Goal mode (a key in the chat, or a `/goal <text>` message). Quick
   questions stay as they are. The bridge writes `.skippy-goal/GOAL.md` in the conversation's folder: the task, the **done-checks** (named
   commands), a checklist, a log of attempts, and a state line it rewrites after every step. The engine already ends a segment at a measured
   context (90,000 tokens); **every new segment starts from GOAL.md, not from a summary of a summary**. No "do you want to continue?":
   the system prompt for Goal mode says to keep going until the checks pass or the budget is spent.
2. **Mechanical done.** The run ends only when the named checks pass (tests, build, lint, a smoke call). A check that fails twice with the
   same fingerprint (same failing test names + same error line) forces a **change of approach** (the log lists what was tried; the prompt
   forbids repeating it); a third time parks the goal.
3. **Small commits on its own branch.** `goal/<slug>`, created from a clean tree (a folder with uncommitted work uses a worktree, never his
   changes: the existing "never discard his uncommitted work" rule). After each green step: commit. On red: `git reset --hard` to the last
   green commit **on that branch only**. The Captain reviews one diff at the end; merging to main stays his (or the normal CI/CD path).
   Never pushes to main, never deploys, lane guard unchanged.
4. **A reviewer ladder, free first.** (a) Mechanical: tests, linters, type check, "files touched outside the goal's scope" and diff-size
   limits, and "a new test must fail before the fix". (b) A **different local model with a fresh context**: Core's chat model
   (Qwen3.6-35B-A3B) reads the diff against GOAL.md and answers drift / not-drift with reasons. (c) Orange (Claude) only when (a) or (b)
   flags something the coder cannot resolve in two tries.
5. **Watchdog and self-repair, self-governed.**
   - Detect by **events, not timers** (the Captain calls timers instead of callbacks a band-aid): the SDK engine's api_retry and no_response,
     an error ResultMessage, the process ending, a Stop-hook refusal repeating with the same fingerprint. Core-slot telemetry (`n_decoded`)
     only annotates what a park report says; it never decides.
   - Default: **detect, park and notify.** A self-repair step where an agent edits skippy-tel and deploys is PLATINUM §19 territory, so it
     is off until the Captain says yes explicitly (he said he wants it, 2026-10-26; the choice of exactly what it may touch is still to be
     confirmed). When on: orange fixes the **harness** (bridge, queue, skippy-tel), never the model that is running on it; normal commit,
     normal CI gate, normal deploy through the quiet gate; then the goal resumes from GOAL.md.
   - Governors against thrash: a **repair budget** per goal and per 24 h; the same failure fingerprint twice after a repair = stop;
     exponential backoff between attempts; a circuit breaker that **parks** the goal ("stuck, here is why") and sends a Discord DM; a kill
     switch (Stop in the chat, and `goal-pause` in the bridge's state); the repair step is never allowed to touch the governors themselves.
6. **Schedules.** The queue has windows (work hours: run unattended; sleep hours: also allowed; "quiet" hours when Core is shared). Outside a
   window the goal waits; inside it the quiet gate (`core/quiet.py`) still defers builds and CI while a goal runs.
7. **Orange as the fixer (allowed by the Captain, 2026-10-26).** When purple parks a goal or a repair fails, the queue may hand the same
   GOAL.md and the failure log to orange for **one bounded session** (token cap per goal), then purple resumes. Every escalation is logged
   on the goal and counted.

## The boundary (proposed by the engine side, 2026-10-26; held stable once the Captain picks the order)

Two files in the goal's folder, `.skippy-goal/`, plus the existing Run API (nothing in-process):
- `events.jsonl`, append-only, written by the engine: `{"ts": ms, "goal", "ev", ...}` with events `segment_started`, `usage`, `tool`, `retry`,
  `trouble`, `segment_ended` (why: context|turn_limit|done|error|stopped), `gate_blocked`, `check_result` (name, passed, fingerprint, tail),
  `committed`, `reset`, `parked`, `finished`. The watchdog reads this stream only: "stalled" = the stream is quiet while state says running.
- `state.json`, rewritten atomically after every step: version, goal, slug, branch, task (the Captain's words), state
  (queued|running|checking|parked|done|failed), segment, session, checks [{name, command}], last_green, fingerprints, parked, updated.
  `GOAL.md` beside it is the readable copy, generated from the same data.
- Control: start and cancel through the Run API (`Workshop.cancel(task)` ends cleanly with `segment_ended` why "stopped"). The queue, windows
  and kill switch are the infra side's.
- Fingerprint: first 12 hex of sha256 over the sorted failing check names plus each one's first `fail|error` line; twice = change of
  approach, three times = park (the engine counts).

## Details to keep

- `.skippy-goal/` is excluded from the Stop gate's uncommitted-path list and from commits (git info/exclude), or the gate would nag about
  its own file.
- Goal-mode code is off by default and must not change how a non-goal run behaves; anything touching `sdk_engine.drive` / `run_core` for
  ordinary runs waits for a gap between trials. Work in a worktree, never the primary checkout.

## Order of work

1. GOAL.md writer + segment restart from the file + the "never ask to continue" prompt (Goal mode, off by default).
2. Done-checks, fingerprinted retry rule, branch commits and reset.
3. Watchdog (stall, crash, silent exit) and the supervisor's resume. (Already parked: supervisor self-restart, re-attach after a dropped
   connection, interrupted-run resume.)
4. Reviewer ladder (a) then (b); orange escalation (c).
5. The queue windows and the repair governors; the Discord park/report.
6. The follow-up test: the same 5 to 10 well-specified tasks on purple (Goal mode) and on orange; compare share ending green, share of the
   diff kept, and wall-clock.

## Realities, so they are not forgotten

Hours do not fix design judgment: a weaker coder gets routine, well-tested work done, not hard design. Drift is the main failure, so GOAL.md,
the scope check and the reviewer matter more than the clock. Core's GPU is shared with chat, Mentat and pop-ups, so windows and the quiet
gate are part of the design, not an extra.
