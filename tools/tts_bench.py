"""Time Skippy chat's voices through the REAL voice-gate: time to first audio and real-time factor, per voice.

    python tools/tts_bench.py [stock skippy ...]      (default: stock skippy; "elevenlabs" spends real characters)

It runs itself inside the skippytel-voice-gate container (docker exec), which holds the home token, signs a pass for each voice with
the gate's own sign(), and POSTs to the gate on 127.0.0.1:8080 exactly as a browser does through Funnel (minus the Funnel hop).
First audio = the first bytes of the streamed reply; total = the last byte; audio = the decoded length of what came back;
RTF = total / audio (below 1 is faster than real time). The first line of each voice includes the model's load (it loads on
demand, unless it is already loaded) and is reported apart. Each line's cached copy is removed first, so every line is a fresh
render. Writes state/tts-bench/<UTC>.json via the container's /state.
"""
import json
import os
import subprocess
import sys
import time

LINES = [
    "Done. The tests pass, the pill is green, and I didn't even break a sweat.",
    "At ninety percent of light speed the Lorentz factor is 2.294, so your clock runs at 44 percent of mine.",
    "Earth's escape speed is 11.19 kilometers per second. You'd need a bigger rocket. Much bigger.",
    "I queued three songs, skipped the one you hate, and turned the volume down. You're welcome.",
    "Your server is up. Two players are on, and one of them is losing badly.",
    "That question again? Fine. The answer is still no, but I admire the persistence, monkey.",
]


def inside(voices):
    sys.path.insert(0, "/app")
    import requests
    import gate
    key = gate.gate_key(os.environ["SKIPPY_HOME_TOKEN"].strip())
    exp = int(time.time()) + 3600
    out = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "voices": {}}
    for v in voices:
        token = gate.sign(key, exp, v)
        runs = []
        for i, line in enumerate(["Right. Warming up, since apparently that is my job now."] + LINES):
            cached = gate.Voice("/state", 0, time.time)._path(f"{v}|{line}" if v in gate.LOCAL_VOICES else line)
            if cached.exists():
                cached.unlink()                                       # a fresh render every time, never the cache
            t0 = time.perf_counter()
            r = requests.post(f"http://127.0.0.1:8080/t/{token}/tts", json={"text": line}, stream=True, timeout=900)
            first, data = None, b""
            for piece in r.iter_content(chunk_size=None):
                if first is None:
                    first = time.perf_counter() - t0
                data += piece
            total = time.perf_counter() - t0
            if r.status_code != 200 or r.headers.get("X-Voice-Cache") == "hit":
                runs.append({"i": i, "status": r.status_code, "cached": r.headers.get("X-Voice-Cache") == "hit"})
                continue
            p = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "24000", "pipe:1"],
                               input=data, capture_output=True)            # decoded and counted: a raw MP3 stream has no duration header
            audio = len(p.stdout) / 48000
            runs.append({"i": i, "chars": len(line), "first_audio_s": round(first, 3), "total_s": round(total, 3),
                         "audio_s": round(audio, 2), "rtf": round(total / audio, 3) if audio else None, "voice": r.headers.get("X-Voice")})
            print(v, json.dumps(runs[-1]), flush=True)
        warm = [x for x in runs[1:] if "rtf" in x]
        out["voices"][v] = {"cold_first_line": runs[0], "runs": runs,
                            "warm_mean": {k: round(sum(x[k] for x in warm) / len(warm), 3) for k in ("first_audio_s", "total_s", "audio_s", "rtf")}
                            if warm else None}
    os.makedirs("/state/tts-bench", exist_ok=True)
    with open(f"/state/tts-bench/{out['utc'].replace(':', '')}.json", "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({v: d["warm_mean"] for v, d in out["voices"].items()}, indent=1))


if __name__ == "__main__":
    if os.environ.get("TTS_BENCH_INSIDE"):
        inside(sys.argv[1:])
    else:
        src = open(__file__, encoding="utf-8").read()
        sys.exit(subprocess.run(["docker", "exec", "-i", "-e", "TTS_BENCH_INSIDE=1", "skippytel-voice-gate", "python", "-", *(sys.argv[1:] or ["stock", "skippy"])],
                                input=src.encode()).returncode)
