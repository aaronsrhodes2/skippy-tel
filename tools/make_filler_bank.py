"""Records Skippy's quips (services/satellite/filler.py LINES) in his voice through the voice gate into state/filler/ (once; add --force to redo).

    services/satellite/.venv/Scripts/python tools/make_filler_bank.py [--force]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "satellite"))
from filler import LINES  # noqa: E402
from voicegate import Gate  # noqa: E402


def env() -> dict:
    out = {}
    for line in Path(r"G:\My Drive\vault\.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> None:
    e = env()
    gate = Gate(e.get("SATELLITE_GATE", "http://127.0.0.1:3007"), e["SKIPPY_HOME_TOKEN"])
    out = ROOT / "state" / "filler"
    index = {}
    for kind, lines in LINES.items():
        (out / kind).mkdir(parents=True, exist_ok=True)
        index[kind] = []
        for n, text in enumerate(lines):
            path = out / kind / f"{n:02d}.mp3"
            if "--force" in sys.argv or not path.exists():
                path.write_bytes(gate.tts(text))
                print("recorded", kind, n, f"{path.stat().st_size // 1024} KB:", text[:60], flush=True)
            index[kind].append({"file": f"{kind}/{n:02d}.mp3", "text": text})
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print("bank:", {k: len(v) for k, v in index.items()})


main()
