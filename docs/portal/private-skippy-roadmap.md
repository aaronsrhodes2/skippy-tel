# The private Skippy: roadmap and task list

The Captain's own chat (`/me` → Skippy) is becoming a full agentic coding chat, like Claude Desktop's Code tab without the chrome.
This file is the **single task list**: read it at the start of any session that touches the chat, and update it as each item
moves (status, and when it started and finished). Times are the Captain's local time, 2026-10-10 evening unless a date is given.
Spec: his messages of 2026-10-11 03:18 and 03:26 (UTC) in conversation `cd1902d4`, plus his annotated screenshot of Claude Desktop
(green = keep, blue = let the model manage, red = drop).

**What he uses (green):** the folded "Ran N commands ›" lines, the working indicator with a clock, image thumbnails in the composer, `+` to
attach and a mic. **Model-managed (blue):** the folder/project, the model and effort, the mode. **Not used (red):** title/branch chrome,
terminal and file icons, the git bar (diff counts, Create PR), the "/ for commands" hint. **Left panel:** he switches between several active
conversations and unpins each when the task is done; nothing else.

| # | Task | Status | Started | Finished |
|---|------|--------|---------|----------|
| 1 | Private Skippy runs Sonnet 5.5 at medium effort (was Opus) | done, bridge restarted and live | 20:40 | 20:45 |
| 2 | Voice in both chats: mic, speech to text (home Whisper), text to speech (Skippy's voice for the Captain) via the voice gate | done, live (`ea68dbe`) | 19:45 | 20:08 |
| 3 | The mess: reply parts were laid out as columns (bridge.css `.turn` is a flex row); the log is now a plain scrolling transcript | done, live (`c3f9b32`) | 20:55 | 21:15 |
| 4 | Folded "Ran N commands ›" and a working line with a clock; less chrome; folder picker hidden | done, live | 21:00 | 21:15 |
| 5 | Pictures: paste, drop or pick a screenshot (up to 4); browser to portal to reporter to bridge, saved for Claude Code to Read | done, live (`eeb7092`); untested with a real paste | 21:05 | 21:20 |
| 6 | Pinned conversations (Active / Earlier, unpin with ×); a browser that never opened the chat opens the latest conversation | done, live; pins are per browser | 21:10 | 21:15 |
| 7 | Stage 1, the harness: a conversation is no longer locked to one project; the model picks the folder; "new workspace" makes a private GitHub repo, a folder, a CLAUDE.md | **blocked at 21:30: the permission classifier refused an edit to the private Skippy's own prompt (`services/claude-bridge/workshop_prompt.md`, "self-modification"); needs the Captain's yes or his own edit.** The wording is ready: replace line 3 (the "ONE project" rule) with the roaming and new-workspace paragraphs, then update the prompt tests | 21:25 | |
| 8 | Lift the old rules: Mentat and memory allowed (mail and calendar stay out unless he says) | queued | | |
| 9 | Inline media: pictures Skippy makes or finds, audio clips with a small player, markdown and code rendered | queued | | |
| 10 | Steer mid-run: type while Skippy works and he gets it at his next step | queued | | |
| 11 | One-tap Yes / No when Skippy asks a question | queued | | |
| 12 | Search across every conversation | queued | | |
| 13 | A small context meter ("start fresh soon") | queued | | |
| 14 | Pins saved on the server so they follow him across devices | queued | | |
| 15 | Mic and Skippy's voice tried in a real browser | **done: the Captain confirmed voice-to-text and text-to-voice work (2026-10-10, 21:3x)** | | 21:3x |

Decisions taken on the Captain's behalf (he said "proceed"; change any of them by saying so): the chat is exempt from the portal's
no-scroll rule; new workspace repos are private; mail and calendar are out.
