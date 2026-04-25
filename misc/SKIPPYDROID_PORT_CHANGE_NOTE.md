# Lockstep change required in SkippyDroid

**SkippyTel external port is `3003`, not `5001`.** The brief
(`misc/SKIPPYTEL_BRIEF.md`) was authored against `:5001`; on the
SkippyTel side that's been overridden to `:3003`. The Z-Phone
(SkippyDroid) egress should use the **same number `3003`** — they're
a paired endpoint that never run on the same machine, so there's no
conflict and matching numbers cuts confusion.

(Earlier interim choice `:7334` was abandoned — that port is reserved
for the DJ Block Planner / SUIP manifest on the Mac.)

## What needs to change in the SkippyDroid repo

1. **`apps/SkippyDroid/.../MainActivity.kt:53`**
   ```kotlin
   // before
   val pcUrl = "http://skippy-pc:5001"
   // after
   val pcUrl = "http://skippy-pc:3003"
   ```

2. **`apps/SkippyDroid/.../layers/TransportLayer.kt`** — anywhere the
   port `5001` appears (timeouts, log strings, default fallback URL).

3. **Z-Phone egress** — whatever port the phone listens on for outbound
   coordination should also be `3003` to match the pair.

4. **`misc/SKIPPYTEL_BRIEF.md`** in the SkippyDroid repo — update the
   Transport Contract row "Hostname the phone uses" from `:5001` to
   `:3003` so it stays consistent with this side.

## Why

Captain's standing preference is to avoid common default ports
(5000/8000/8080/etc.) as a lazy port-conflict hedge. `3003` was chosen
for the SkippyTel ↔ Z-Phone pair. Reserved-elsewhere numbers to avoid:
- `7334` — DJ Block Planner / SUIP manifest on the Mac
- `47823` — SkippyView passthrough host on the phone
- `11434` — Ollama (forced default; clients hardcode it)

## Lockstep rename: `/yeybuddy/` → `/heybuddy/`

The face-recognition endpoint was authored in the brief as `/yeybuddy/identify`. SkippyTel ships it as `/heybuddy/identify` — the Captain's intended name ("hey buddy" is what you say when you've forgotten someone's name and want to be sly about it).

Update everywhere on the SkippyDroid side that references the old path:
- Future `FriendRecognizerModule.kt` URL string
- Brief copy in the SkippyDroid repo
- Any voice intent that triggers the identify call

## Verification after the SkippyDroid-side change

- Phone's RightBar SignalStack shows 4 bars when on the same Tailnet.
- `adb logcat -s SkippyDroid` shows successful `/health` polls every 10s.
