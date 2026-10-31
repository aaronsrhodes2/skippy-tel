"""A long-form audio stream out of skippy-pc's own outputs (the HDMI to the Sony, the Bluetooth stereo): the audiobook's PC side.

speaker_out.PcOut plays one short clip that was decoded whole into memory. A book is hours long, so this is the streaming variant: ffmpeg decodes
the file from a given second (at a given speed) into a pipe, and a thread feeds it to a sounddevice output a tenth of a second at a time. Seeking,
pausing and changing speed are all "stop, then start again from a second"; the audiobook player (audiobooks.py) owns the clock and the place, so this
class never needs to say where it is.

`gain` is how Skippy's own voice ducks the book: audiobooks.BookPlayer.duck() sets it, the feeder applies it on the very next tenth of a second
(speaker_out.Ducker only lowers OTHER programs, and the hub is this process).
"""
from __future__ import annotations

import subprocess
import threading

import speaker_out

RATE = speaker_out.RATE
CHUNK_S = 0.1


def tempo_filter(speed: float) -> list[str]:
    """ffmpeg's atempo takes 0.5 to 2.0 per stage; the player keeps speed inside that."""
    return ["-af", f"atempo={speed:.2f}"] if abs(speed - 1.0) > 0.01 else []


def pcm_args(source: str, start_s: float, speed: float = 1.0) -> list[str]:
    args = ["ffmpeg", "-v", "error", "-nostdin"]
    if start_s > 0:
        args += ["-ss", f"{start_s:.2f}"]
    return args + ["-i", source, "-vn", *tempo_filter(speed), "-f", "f32le", "-ac", "2", "-ar", str(RATE), "pipe:1"]


class PcBookOut:
    """One PC output for the book. `popen` and `open_stream` are the seams the tests replace."""

    def __init__(self, kind: str, part: str, delay_s: float = 0.0, log=print, popen=subprocess.Popen, open_stream=None, find=None):
        self.kind, self.part, self.delay_s, self.log = kind, part, delay_s, log
        self.popen, self.open_stream, self.find = popen, open_stream, find or speaker_out.find_device
        self.gain = 1.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc = None

    def available(self):
        try:
            return self.find(self.part)
        except Exception:                                        # noqa: BLE001
            return None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, source: str, start_s: float, speed: float = 1.0) -> bool:
        """Begin streaming `source` (a file) from `start_s`. False when the output is not there (a Bluetooth stereo that is off)."""
        self.stop()
        dev = self.available()
        if dev is None:
            self.log(f"(book output {self.kind}: no sound device matching {self.part!r}: skipped)")
            return False
        self._stop = threading.Event()
        stop = self._stop
        self._thread = threading.Thread(target=self._run, args=(dev, source, start_s, speed, stop), daemon=True, name=f"book-{self.kind}")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2)

    def _run(self, dev, source: str, start_s: float, speed: float, stop: threading.Event) -> None:
        # the outputs line up by starting late (speaker_out's delays); a stop during the wait cancels the start
        if self.delay_s > 0 and stop.wait(self.delay_s):
            return
        try:
            import numpy as np                                    # noqa: PLC0415
            proc = self.popen(pcm_args(source, start_s, speed), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._proc = proc
            stream = (self.open_stream or self._sd_stream)(dev[0])
            frame = int(RATE * CHUNK_S) * 2 * 4                   # 0.1 s of stereo float32
            try:
                while not stop.is_set():
                    raw = proc.stdout.read(frame)
                    if not raw:
                        break
                    data = np.frombuffer(raw[: len(raw) // 8 * 8], dtype="<f4").reshape(-1, 2)
                    g = self.gain
                    stream.write((data * g).astype("float32") if g != 1.0 else data)
            finally:
                stream.close()
                try:
                    proc.kill()
                except OSError:
                    pass
        except Exception as e:                                    # noqa: BLE001 — a dead output must not take the hub down
            self.log(f"(book output {self.kind} {dev[1]!r}: {type(e).__name__}: {str(e)[:80]})")

    @staticmethod
    def _sd_stream(device: int):
        import sounddevice as sd                                  # noqa: PLC0415
        s = sd.OutputStream(samplerate=RATE, channels=2, dtype="float32", device=device)
        s.start()
        return s


def build_outputs(env: dict, room: str, log=print) -> list[PcBookOut]:
    """The PC outputs a room's book plays on, the same ones and the same start delays as Skippy's voice (SATELLITE_OUT, SATELLITE_OUT_DELAYS)."""
    return [PcBookOut(p.kind, p.part, p.delay_s, log=log) for p in speaker_out.parse_outputs(env, room, log)[1]]


__all__ = ["PcBookOut", "build_outputs", "pcm_args", "tempo_filter"]
