"""Run INSIDE skippytel-flask-api: renders lines.json with ElevenLabs (flask-api's own client and settings; argv[1] picks the voice),
timing first audio byte and total. The key never leaves the container and is never printed."""
import json, os, sys, time
from app.clients import elevenlabs_client as el
from app.config import settings
lines = json.load(open("/tmp/vb/lines.json"))
WHICH = sys.argv[1] if len(sys.argv) > 1 else "skippy"   # skippy = ELEVENLABS_SKIPPY_VOICE_ID; chat = what /tts/stream speaks today (settings.elevenlabs_voice_id)
vid = os.environ.get("ELEVENLABS_SKIPPY_VOICE_ID") if WHICH == "skippy" else settings.elevenlabs_voice_id
pre = "el_" if WHICH == "skippy" else "chat_"
out = []
for i, t in enumerate(lines):
    t0 = time.perf_counter(); first = None; buf = b""
    for c in el.stream_tts(t, voice_id=vid):
        if first is None: first = time.perf_counter() - t0
        buf += c
    tot = time.perf_counter() - t0
    open(f"/tmp/vb/{pre}{i:02d}.mp3", "wb").write(buf)
    out.append({"i": i, "ttfa_s": round(first, 3), "total_s": round(tot, 3)})
    print(out[-1], file=sys.stderr)
json.dump({"model": settings.elevenlabs_model, "voice": WHICH, "runs": out}, open(f"/tmp/vb/{pre}timing.json", "w"))
