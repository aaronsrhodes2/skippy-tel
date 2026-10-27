"""The pop-up inventory table, from the registries (docs/popups.md), so docs/popup-inventory.md never drifts from what is registered.

    python tools/popup_inventory.py            print the Markdown table
    python tools/popup_inventory.py --check    exit 1 when docs/popup-inventory.md's table (between its markers) differs from the registries

A registry entry is one tool the private Skippy (or the portal chat) can call: where it runs (`box`), how fast it starts (`tier`), what it takes in and
gives back, how long a run is, how much memory, who may use it. A pop-up loads its model when asked and gives the memory back: "on call" (blue on the
Status page) until it has answered a question.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "popup-inventory.md"
BEGIN, END = "<!-- popups:begin (tools/popup_inventory.py) -->", "<!-- popups:end -->"


def entries(root: Path = ROOT) -> list[dict]:
    paths = [root / "services" / "popups" / "registry.json", *sorted((root / "services").glob("*-popups/popups.json"))]
    out = []
    for p in paths:
        if p.is_file():
            out += [e for e in json.loads(p.read_text(encoding="utf-8")).get("popups") or [] if isinstance(e, dict) and e.get("name")]
    return out


def _short(text: str, n: int = 150) -> str:
    one = re.sub(r"\s+", " ", str(text or "")).strip()
    first = re.split(r"(?<=[.!?])\s", one, maxsplit=1)[0]
    return (first if len(first) <= n else first[: n - 1] + "…").replace("|", "/")


def _memory(e: dict) -> str:
    gb = e.get("vram_gb") or e.get("mem_gb")
    return f"{gb:g} GB" if isinstance(gb, (int, float)) else ""


def _time(sec) -> str:
    if not isinstance(sec, (int, float)):
        return ""
    return f"{sec:g} s" if sec < 120 else f"{sec / 60:.0f} min"


def table(root: Path = ROOT) -> str:
    rows = ["| Tool | What it does | Runs on | Takes | Gives | Typical run | Memory | Who |", "|---|---|---|---|---|---|---|---|"]
    for e in entries(root):
        take = ", ".join(sorted({m.split("/")[0] for m in e.get("media_in") or []})) or "words"
        give = ", ".join(sorted({o.split("/")[-1] for o in e.get("outputs") or []}))
        rows.append(f"| `{e['name']}` | {_short(e.get('description'))} | {e.get('box', '')} ({e.get('tier', '')}) | {take} | {give} | "
                    f"{_time(e.get('est_seconds'))} | {_memory(e)} | {e.get('audience', '')} |")
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    if "--check" in argv:
        text = DOC.read_text(encoding="utf-8").replace("\r\n", "\n")
        m = re.search(re.escape(BEGIN) + r"\n(.*?)\n" + re.escape(END), text, re.S)
        if not m:
            print(f"{DOC} has no {BEGIN} ... {END} block")
            return 1
        if m.group(1).strip() != table().strip():
            print("docs/popup-inventory.md's table differs from the registries: run  python tools/popup_inventory.py  and paste it between the markers")
            return 1
        return 0
    print(table())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
