"""Put a MineBench run on the Experiments page: one folder, no code to change.

    python tools/voxel/publish_run.py <run's working folder> <id> --title "..." --verdict "..." [--model "..."] [--prompt "..."]
        [--expanded] [--request request.txt] [--transcript session.jsonl]

Renders the run's build.json (tools/voxel/render.py: four views and the 3D model), copies it into
portal/static/experiments/minebench/<id>/ and writes run.json there with the next n. The run's time, tokens and models come from
its Claude Code transcript (tools/voxel/run_stats.py: the newest one kept for the working folder, or --transcript).
A verdict is required: a bad run is published too, saying why it is bad. Commit and push to deploy.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys

import run_stats

HERE = pathlib.Path(__file__).resolve().parent
RUNS = HERE.parents[1] / "portal" / "static" / "experiments" / "minebench"
CORE = "Purple Skippy: Claude Code on Skippy Core's coder (Qwen3.6-35B-A3B, MTP, Q8), local and free, the same message as v5 word for word"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=pathlib.Path)
    ap.add_argument("id")
    ap.add_argument("--title", required=True)
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--model", default=CORE)
    ap.add_argument("--prompt", default="a choo-choo train")
    ap.add_argument("--transcript", type=pathlib.Path, help="the run's session transcript, when it isn't the folder's newest")
    ap.add_argument("--expanded", action="store_true", help="the run got more than the benchmark's one line")
    ap.add_argument("--request", type=pathlib.Path, help="the full message the builder got")
    a = ap.parse_args()
    transcript = a.transcript or run_stats.find_transcript(a.work)
    if not transcript:
        raise SystemExit(f"no Claude Code transcript for {a.work}: pass --transcript")
    stats = run_stats.stats(transcript)
    out = RUNS / a.id
    old = json.loads((out / "run.json").read_text(encoding="utf-8")) if (out / "run.json").exists() else None
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(a.work / "build.json", out / "build.json")
    r = subprocess.run([sys.executable, str(HERE / "render.py"), str(out / "build.json"), str(out / "views.png"), a.title,
                        "--glb", str(out / "model.glb")], capture_output=True, text=True, check=True)
    shot = json.loads(r.stdout.strip().splitlines()[-1])
    n = old["n"] if old else 1 + max((json.loads(f.read_text(encoding="utf-8"))["n"] for f in RUNS.glob("*/run.json")), default=0)
    run = {"n": n, "title": a.title, "model": a.model, "prompt": a.prompt, "comparable": not a.expanded,
           "blocks": shot["blocks"], "minutes": stats["minutes"], "size": " x ".join(map(str, shot["size"])), "verdict": a.verdict, "stats": stats}
    if a.request:
        shutil.copyfile(a.request, out / "request.txt")
    if (out / "request.txt").exists():
        run["request"] = f"experiments/minebench/{a.id}/request.txt"
    (out / "run.json").write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"published": a.id, "n": n, "blocks": run["blocks"], "size": run["size"], "minutes": run["minutes"], "tokens": stats["tokens"]["total"], "folder": str(out)}))


if __name__ == "__main__":
    main()
