# Pivot: change subject without a new chat (design, 2026-10-27)

Status: **design, not built.** The Captain's rule: he doesn't like making new chats; purple Skippy must be **resilient to that**. He
pivots by a **knowledge dump, compression, and re-use of the Skippy voice** (the persona, the text-to-voice, the pre-recorded samples).

## What a pivot is

A pivot is a **segment boundary he asks for** (or the chat offers when the subject clearly changes), inside the same conversation. The SDK
engine already ends a segment at a measured context (90,000 tokens) and starts the next from a hand-off (`run_state.handoff`: the request,
git state and the agent's last messages). A pivot is the same boundary on demand, with a better hand-off:

1. **Knowledge dump.** Before the boundary the agent writes what must outlive it into the conversation folder (`.skippy-goal/GOAL.md`
   when a goal is running, else `.skippy-notes/<date>-<slug>.md`): decisions made and why, what is done, what is half done, files and
   commands that matter, open questions, what the Captain said he wants. Plain, short, no transcript.
2. **Compression.** The old turns are not carried. The next segment starts from: the persona block, the dump, the new request, git state.
   Tool-result bulk is dropped, never summarized again (a summary of a summary is what the goal file avoids).
3. **Voice re-used, not re-described.** The persona is five short example lines, one per mood (about 130 tokens), not the 183 recorded lines
   (10,064 tokens). The three rules from the Garry's Mod brief ride with them: insults sit on top of real help; the catchphrase is rare;
   lines stay short. It is set at the start of a conversation and **re-asserted at every pivot**, which is where drift is repaired.
4. **Speech.** Skippy's reply is spoken by the local voice (tts-studio, the F5 clone; ElevenLabs only as the fallback and then not at all
   once the Captain says so). A pre-recorded line (rendered once, free, by the local voice, cached as audio) can play while the model works,
   only when the wait is long enough to notice, chosen by mood and context, never the same line twice in a row, and mixed with dynamic
   speech so the variety stays real.

## Who builds what

- Engine side (`services/claude-bridge`, the Shifting Prophesy session): a `pivot` request on the Run API that ends the segment cleanly, asks
  for the dump, and starts the next segment from dump + persona + new request. It is the Goal-mode GOAL.md hand-off with a user trigger.
- Portal side (this session): the Pivot key beside New chat, the persona examples file, the filler audio set and its picker, ElevenLabs
  removal as the fallback. Nothing starts before the engine side has a window (the cruise is first).

## How a pivot is asked for (the Captain, 2026-10-27)

- **Phrases that ask:** "let's pivot", "let's change the subject", "on to a new feature", "moving on to ...", "switching gears", "new topic", "next up" and
  the like, at the start of a message (`static/skippy-pivot.js`, `detect()`, with node tests).
- **Phrases that only add, never pivot:** "by the way", "also", "I noticed", "one more thing", "while we're at it", "side note", "quick question",
  "speaking of". An additive opener wins over a pivot phrase behind it.
- **Not a pivot:** "pivot table", "pivot point", or a pivot phrase buried deep in a long message (only the first 90 characters count).
- **A pivot request only opens a question.** Nothing is dumped or compressed until he agrees. If stopping would lose something (a run still going, a goal being
  worked on, uncommitted changes, queued messages) the chat says so and offers "Pivot now" or "Finish first" (`inTheMiddle()`); when nothing is in flight it
  goes straight to the dump.

## Open choices

- Where the dump is shown: as a card in the transcript he can edit before the boundary.
- The wiring (the composer calls `detect()` before sending, the portal supplies the in-flight state) waits for the engine's pivot request.
