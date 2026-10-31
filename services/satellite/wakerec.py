"""Record the Captain's wake-word clips through a room pod's real microphone chain (the pod's ADC, its noise suppression and gain), not skippy-pc's USB mic.

    ./.venv/Scripts/python hub.py --record positive 50 [ROOM]     stop the hub first (the pod talks to one hub); then restart it
    ./.venv/Scripts/python hub.py --record negative 30 [ROOM]

The hub connects to the pod as always, but the room's audio goes here instead of the listener. For each clip Skippy says what to say (out of the pod,
in his voice) and the pod's screen shows it; the clip is what the pod streamed from just before he started to the end of his phrase. Clips go to
state/wakeword/real/{positive,negative}/ (git-ignored: his voice), every 5th into real/test/ instead, held out of training so `wakectl.py test`
measures something. Resumes: the numbering continues after what is already there. Nothing is sent anywhere.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from vad import rms
from voicegate import wav_bytes

BLOCK_MS = 50
LEAD_BLOCKS, TAIL_BLOCKS = 10, 8                                # 0.5 s kept before his voice, 0.4 s after
HOLD_OUT_EVERY = 5

POSITIVE_SAYINGS = ("Skippy", "Hey Skippy")
POSITIVE_WAYS = (
    "close and normal", "close and quiet", "from across the room, normal voice", "from across the room, louder",
    "from the doorway, as if calling in", "close, but while you walk about", "tired and a bit hoarse",
    "casual, in passing", "turned away from me", "close, quick",
)
NEGATIVES = (
    "Skipper", "Hey Skipper", "skip it", "Computer", "Hey computer", "Thanks, Gibby", "what time is it", "Skippy's", "Skype", "Hey, Siri",
    "Alexa, stop", "Hippie", "a happy sentence of your own", "Skinny", "turn the lights off", "a cough", "a laugh", "clear your throat",
    "I need a skipper for the boat", "say anything about the weather", "Slippy", "Zippy", "Kippy", "Hey, Kelly", "Hey, listen", "Whisky",
    "a sentence about music", "any random three words", "good night", "what are you doing",
)


def plan(kind: str, count: int, start: int = 0) -> list[tuple[str, str]]:
    """(what to say, how) for clips start .. start+count-1. A positive alternates the two phrasings and cycles the distances."""
    out = []
    for n in range(start, start + count):
        if kind == "positive":
            out.append((POSITIVE_SAYINGS[n % 2], POSITIVE_WAYS[(n // 2) % len(POSITIVE_WAYS)]))
        else:
            out.append((NEGATIVES[n % len(NEGATIVES)], "normal"))
    return out


def prompt_text(kind: str, what: str, way: str) -> str:
    if kind == "positive":
        return f'Say "{what}", {way}.'
    return f"Say: {what}." if way == "normal" else f"Say: {what}, {way}."


class Recorder:
    """Takes the room's 50 ms blocks, one clip per prompt. `speak(text)` says it aloud and shows it; `log` reports; `root` is state/wakeword/real."""

    def __init__(self, kind: str, count: int, root: Path, speak, log=print, floor: float = 0.007, window_s: float = 4.5):
        self.kind, self.count, self.root, self.speak, self.log = kind, count, Path(root), speak, log
        self.floor, self.window = floor, int(window_s * 1000 / BLOCK_MS)
        self.win: list | None = None                            # the blocks since the prompt was spoken
        self.got: asyncio.Future | None = None
        self.done = 0

    # the room hands us every block
    def feed(self, block) -> None:
        if self.win is None:
            return
        self.win.append(block)
        if len(self.win) >= self.window and self.got is not None and not self.got.done():
            win, self.win = self.win, None
            self.got.set_result(win)

    def trim(self, win, need: int = 3):
        """The phrase inside a window of blocks, or None when only room noise is in it. The pod's noise floor drifts, so the bar is a multiple of this window's own
        quiet level (its 20th-percentile 100 ms), never under 0.003; a phrase is at least 300 ms above it. Returns (samples, info)."""
        lv = [rms([x for b in win[i:i + 2] for x in b]) for i in range(0, len(win) - 1, 2)]       # 100 ms levels
        noise = sorted(lv)[len(lv) // 5]
        bar = max(noise * 3.0, 0.003)                           # the pod's own noise is ~0.001-0.008 and his far, quiet voice only ~10x that
        info = {"noise": round(noise, 4), "bar": round(bar, 4), "peak": round(max(lv), 4)}
        hot = [i for i in range(len(lv) - need + 1) if all(v > bar for v in lv[i:i + need])]     # `need` 100 ms levels in a row
        if not hot:
            return None, info
        first, last = hot[0] * 2, (hot[-1] + need) * 2
        i0, i1 = max(0, first - LEAD_BLOCKS), min(len(win), last + TAIL_BLOCKS)
        return [x for b in win[i0:i1] for x in b], info

    def _folder(self, held_out: bool) -> Path:
        return self.root / "test" / self.kind if held_out else self.root / self.kind

    def existing(self) -> int:
        return sum(len(list(self._folder(h).glob("*.wav"))) for h in (False, True))

    async def run(self) -> int:
        start = self.existing()
        for i, (what, way) in enumerate(plan(self.kind, self.count, start)):
            n = start + i
            text = prompt_text(self.kind, what, way)
            self.log(f"[{i + 1}/{self.count}] {text}")
            await self.speak(text)
            await asyncio.sleep(0.5)                            # his own voice is still ringing in the pod's microphone
            self.got = asyncio.get_running_loop().create_future()
            self.win = []
            win = await self.got
            clip, info = self.trim(win)
            if clip is None:
                skipped = self.root / "skipped"
                skipped.mkdir(parents=True, exist_ok=True)
                (skipped / f"{time.strftime('%Y%m%d-%H%M%S')}-{self.kind}-{n:03}.wav").write_bytes(wav_bytes([x for b in win for x in b]))
                self.log(f"   (heard no phrase: noise {info['noise']}, loudest {info['peak']}, bar {info['bar']}; the window is in real/skipped)")
                continue
            held = (n + 1) % HOLD_OUT_EVERY == 0
            folder = self._folder(held)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{n:03}.wav").write_bytes(wav_bytes(clip))
            with (self.root / "index.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"kind": self.kind, "n": n, "said": what, "way": way, "held_out": held, "seconds": round(len(clip) / 16000, 2), **info}) + "\n")
            self.done += 1
            self.log(f"   kept {len(clip) / 16000:.1f} s{' (held out for testing)' if held else ''}")
        await self.speak(f"That is all {self.count}. Thank you, Captain." if self.done else "I heard nothing at all. Is the pod's microphone muted?")
        return self.done


def salvage(root: Path, floor: float = 0.007, log=print) -> int:
    """Re-trim the windows in real/skipped (kept when a phrase was not found) with the current rule, and file the ones that now have a phrase."""
    import wave                                                 # noqa: PLC0415
    n = 0
    for path in sorted((Path(root) / "skipped").glob("*.wav")):
        kind = path.stem.split("-")[2]
        with wave.open(str(path)) as w:
            raw = w.readframes(w.getnframes())
        samples = [int.from_bytes(raw[i:i + 2], "little", signed=True) / 32768 for i in range(0, len(raw) - 1, 2)]
        win = [samples[i:i + 800] for i in range(0, len(samples) - 799, 800)]
        rec = Recorder(kind, 0, root, None, floor=floor)
        clip, info = rec.trim(win)
        if clip is None:
            clip, info = rec.trim(win, need=2)                  # a quick, quiet "Skippy" is only ~200 ms above the noise
        if clip is None:
            log(f"{path.name}: still no phrase {info}")
            continue
        held = (rec.existing() + 1) % HOLD_OUT_EVERY == 0
        folder = rec._folder(held)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / path.name.replace(f"-{kind}-", "-")).write_bytes(wav_bytes(clip))
        path.unlink()
        n += 1
        log(f"{path.name}: kept {len(clip) / 16000:.1f} s {info}")
    return n


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parents[2] / "state" / "wakeword" / "real"
    sys.exit(0 if len(sys.argv) > 1 and sys.argv[1] == "salvage" and salvage(root) >= 0 else 2)
