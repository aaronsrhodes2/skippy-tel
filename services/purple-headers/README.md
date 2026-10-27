# Purple Skippy's headers

Purple Skippy (Claude Code on skippy-core's local coder, through `services/claude-bridge`) has about 86k tokens of working room per
segment: too little for the full CLAUDE.md files and PLATINUM_RULES.md. It gets these two compressed headers instead, set at the start
of a conversation and re-asserted at every pivot (docs/pivot.md).

- `dev.md` (~1,450 tokens, cap 2,500): for coding work. The rules that matter (bash first, deploy and verify, push to main, Mentat for
  physics, ready means ready, no secrets, kill by PID, never work around a denial, the lane), where things live on skippy-pc,
  skippy-core and the portal, how to reach skippy-core, and a one-line index of its tools and skills with "read X" pointers.
- `stm.md` (~260 tokens, cap 400): when it speaks as Skippy the Magnificent. The persona in brief (from
  `services/flask-api/app/skippy_persona.md`) and the speech rules (spoken, short, no markdown).

Both are generated; don't edit them by hand. Change the wording in `tools/purple_headers.py`, then:

```bash
python tools/purple_headers.py
python -m pytest tools/tests/test_purple_headers.py -q
```

The test fails when a header goes over its cap, loses a required rule phrase, or differs from what the generator makes now. The
generator also stops when a rule it summarizes is renamed or dropped in its source (`ANCHORS`); the sources outside the repo
(D:/Aaron/development/PLATINUM_RULES.md and CLAUDE.md) are checked only where they exist, so CI still passes.

## For the bridge (the Shifting Prophesy session's side; not wired yet)

The lines the engine would need, in `services/claude-bridge` (a Core run's system prompt append, and again in each segment's or pivot's
hand-off):

```python
PURPLE_HEADERS = Path(__file__).resolve().parents[1] / "purple-headers"
dev_header = (PURPLE_HEADERS / "dev.md").read_text(encoding="utf-8")   # coding runs on Skippy Core
stm_header = (PURPLE_HEADERS / "stm.md").read_text(encoding="utf-8")   # when the reply is spoken as Skippy the Magnificent
```

With the dev header loaded, the run should leave out the full CLAUDE.md files (the engine's `claude_md_excludes`), or it pays for both.
