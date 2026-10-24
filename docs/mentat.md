# Mentat as a service: suggestions, not defaults, and the research log

Mentat (sigma-ground's physics engine) runs twice, from the same `mentat/` code: **mentat-home** on skippy-pc
(`docker-compose.override.yml`, Qwen helps its translator; Skippy's chat and the private Skippy ask it) and the
**cloud copy** `skippy-mentat` on Cloud Run (keywords only; the Experiments playground and Claude's `mentat` MCP
connector). Endpoints: `mentat/app.py`'s docstring.

## Mentat never fills in an input (the Captain, 2026-10-23)

A value the question gives is always used, as given, converted with Mentat's own unit registry
(`sigma_ground/units_registry.py`, the one behind `convert_units`): "100 miles per hour", "100 mph", "60 km/h",
"30 ft/s", "15 °C". A value the question does not give is **never filled in**. Mentat computes nothing and answers
with a clarification that suggests one:

```json
{"value": null, "error": {"code": "needs_input", ...},
 "clarification": {
   "needs": ["speed_m_s"],
   "suggested": {"speed_m_s": {"value": 30.0, "units": "m/s", "why": "a hard throw by hand, about 67 mph", "label": "launch speed"}},
   "sentence": "Your question doesn't give everything I need. I'd assume 30 m/s for the launch speed (...). Use that?",
   "follow_up": {"question": "range of a projectile launched at 45 degrees", "given": {"speed_m_s": 30.0}}}}
```

Mentat is stateless, so the caller carries the suggestion: when the person agrees ("yes", "sure", "ok", "go
ahead"), POST `follow_up` back to `/ask` (`{"question": ..., "given": {...}}`) and Mentat computes with exactly those
values. A `given` value is a number in the input's units or a quantity string ("100 mph"). The same rule holds for
`run_simulation` (its follow-up is `{verb, params}`) and the cockpit's front door (a "yes" in the same session).

- **Skippy's chat** (`portal/app.py` `/chat/send`, `portal/chat_tools.py`): the ⚛ card shows the suggestion (amber,
  open, no number). The page keeps the newest suggestion's follow-up and sends it as `mentat_follow_up` with the next
  message; when that message is a plain yes (`chat.is_yes`), the portal asks Mentat with it itself and the brain only
  explains the answer. Anything else ("yes but 40 m/s") goes to the brain, which asks Mentat in the person's words.
  A suggestion is not a gap: it is never logged as "failed due to incompetence".
- **The private Skippy** (`services/claude-bridge/workshop_prompt.md`): relays the suggestion and, on the Captain's
  yes, sends `mentat_call.sh ask '<question>' '<given JSON>'`.

Where the 30 m/s came from: `projectile_motion` declared no slots at all, so a question's speed and angle never reached
it and the Python defaults of the scenario function (30 m/s, 45°, from the June 2026 snapshot) ran every time. The
October defaults-and-ranges work (`sigma-ground/misc/DEFAULTS_AND_RANGES_DESIGN.md`) was a design only, never built.
What was rolled back is listed in sigma-ground's `sigma_ground/materia/inputs.py` docstring and the session notes.

## The research log: everything Mentat outputs, kept forever

The Captain's words: "We should log everything mentat outputs. it's our research data." Every request to a compute
endpoint (`/ask`, `/tools/<name>`, an MCP `tools/call`, `/wva/solve`), refused ones included, is one JSON line,
appended, never rewritten (`mentat/research.py`):

| field | what |
|---|---|
| `v`, `id`, `ts` | format version 1, a unique id, UTC time |
| `instance`, `sigma_ground_ref` | `home` or `cloud`, and the sigma-ground commit that answered |
| `trace` | the `X-Skippy-Trace` request header (the portal chat sends `chat-<hex>` per turn; the reporter falls back to the job id; `mentat_call.sh` sends `$MENTAT_TRACE`) |
| `endpoint`, `tool` | `/ask`, `/tools/convert_units`, `/mcp` (+ tool name), `/wva/solve` |
| `caller` | `ip`, `user_agent`, `via` (`X-Skippy-Caller`: `portal-chat`, `mentat_call.sh`, `deploy-smoke`) |
| `request` | `/ask`: `{question, given}`; otherwise the arguments as sent |
| `interpreter`, `understood` | `/ask` only: `keywords + qwen` or `keywords`; the route and each step's verb, inputs with their units, and bindings |
| `status`, `ok`, `response` | HTTP status, and Mentat's whole reply as sent (result with steps, sources, provenance tag, clarification, or the error) |
| `ms` | time to answer |

Where it lives:

- **Home**: `SkippyTel/state/mentat-research/<YYYY-MM>.jsonl` (bind-mounted at `/research`, survives rebuilds; `state/`
  is not in git).
- **Cloud**: the private bucket `gs://skippy-tel-mentat-research/mentat-research/<YYYY>/<MM>/<DD>/<HHMMSS>-<instance>-<n>.jsonl`,
  a batch of lines per object (every 200 lines or 30 s, and at shutdown). Mentat's identity may only create objects
  there (`roles/storage.objectCreator`): it can't overwrite or delete what it wrote, and no lifecycle rule deletes
  anything. The bucket comes from `infra/bootstrap` (re-run `bash infra/bootstrap/bootstrap.sh` in Cloud Shell once);
  until it exists, each batch is printed whole to Cloud Logging as `mentat_research_unsaved` so nothing is lost.

`GET /research` on either copy says whether the log is writing (`enabled`, `queued`, `dropped`, `failed`,
`last_error`); it never serves records. Logging is off the request path: `record()` only queues, a background
thread writes, and a failure there is counted, never raised into an answer.

Tests: `mentat/tests/test_mentat_research.py`, `portal/tests/test_mentat_suggest.py`,
`services/home-reporter/tests/test_reporter_mentat.py`, and sigma-ground's
`sigma_ground/materia/tests/test_suggest_not_fill.py`.
