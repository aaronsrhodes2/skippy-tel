"""Put a MineBench run on the Experiments page: one folder, no code to change.

    python tools/voxel/publish_run.py <run's working folder> <id> --title "..." --verdict "..." [--model "..."] [--prompt "..."]
        [--expanded] [--request request.txt] [--transcript session.jsonl | --single run.json] [--series fresh]

Renders the run's build.json (tools/voxel/render.py: four views and the 3D model), copies it into
portal/static/experiments/minebench/<id>/ and writes run.json there with the next n. The run's time, tokens and models come from
its Claude Code transcript (tools/voxel/run_stats.py: the newest one kept for the working folder, or --transcript), or for a one-call
run from tools/voxel/minebench_api.py's run.json (--single). --series names the series it belongs to (experiments.MINEBENCH_SERIES).
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
    ap.add_argument("--single", type=pathlib.Path, help="a one-call run: minebench_api.py's run.json instead of a transcript")
    ap.add_argument("--series", default="tuned", help="experiments.MINEBENCH_SERIES: tuned (runs 1-11) or fresh")
    ap.add_argument("--expanded", action="store_true", help="the run got more than the benchmark's one line")
    ap.add_argument("--request", type=pathlib.Path, help="the full message the builder got")
    a = ap.parse_args()
    if a.single:
        stats = run_stats.single(a.single)
    else:
        transcript = a.transcript or run_stats.find_transcript(a.work)
        if not transcript:
            raise SystemExit(f"no Claude Code transcript for {a.work}: pass --transcript")
        stats = run_stats.stats(transcript)
    out = RUNS / a.id
    old = json.loads((out / "run.json").read_text(encoding="utf-8")) if (out / "run.json").exists() else None
    out.mkdir(parents=True, exist_ok=True)
    failed = None
    if not (a.work / "build.json").exists():   # no build at all (a one-call reply with no complete JSON): its raw reply instead
        shutil.copyfile(a.work / "raw.txt", out / "reply.txt")
        why = json.loads(a.single.read_text(encoding="utf-8")) if a.single else {}
        failed = "no voxel.exec call in the reply" + (" (it stopped at its output limit)" if why.get("stop_reason") == "max_tokens" else "")
    else:
        shutil.copyfile(a.work / "build.json", out / "build.json")
    # the runtime first: a build whose code throws fails here in a second, with its own error, not after render.py's 120 s wait
    r = subprocess.run(["node", str(HERE / "runtime.mjs"), str(out / "build.json")], capture_output=True, text=True) if not failed else None
    if r and r.returncode == 0:
        r = subprocess.run([sys.executable, str(HERE / "render.py"), str(out / "build.json"), str(out / "views.png"), a.title,
                            "--glb", str(out / "model.glb")], capture_output=True, text=True)
    if failed:
        shot = {"blocks": 0, "size": []}
    elif r.returncode == 0:
        shot = json.loads(r.stdout.strip().splitlines()[-1])
    else:      # its code does not run: MineBench would score no build. Published as that, with the error, no pictures.
        err = [ln.strip() for ln in (r.stderr + r.stdout).splitlines() if "Error" in ln]
        failed = err[-1] if err else "the build's code failed to run"
        shot = {"blocks": 0, "size": []}
        for f in ("views.png", "model.glb"):
            (out / f).unlink(missing_ok=True)
    n = old["n"] if old else 1 + max((json.loads(f.read_text(encoding="utf-8"))["n"] for f in RUNS.glob("*/run.json")), default=0)
    run = {"n": n, "title": a.title, "model": a.model, "prompt": a.prompt, "comparable": not a.expanded,
           "blocks": shot["blocks"], "minutes": stats["minutes"], "size": " x ".join(map(str, shot["size"])) or "none", "verdict": a.verdict, "stats": stats}
    if failed:
        run["failed"] = failed
    if (out / "reply.txt").exists():
        run["reply"] = f"experiments/minebench/{a.id}/reply.txt"
    if a.series != "tuned":
        run["series"] = a.series
    if a.request:
        shutil.copyfile(a.request, out / "request.txt")
    if (out / "request.txt").exists():
        run["request"] = f"experiments/minebench/{a.id}/request.txt"
    (out / "run.json").write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"published": a.id, "n": n, "blocks": run["blocks"], "size": run["size"], "minutes": run["minutes"], "tokens": stats["tokens"]["total"], "folder": str(out)}))


if __name__ == "__main__":
    main()
