"""What a MineBench run cost, read from its Claude Code transcript: when it started, how long it ran, the tokens, every model.

    python tools/voxel/run_stats.py <session.jsonl>        # prints the stats as JSON
    find_transcript(<run's working folder>)                # the newest transcript of a run made in that folder

The numbers are the transcript's own: the last cost-state record (Claude Code's per-model totals and the session's length), and
the eyes hook's replies (services/claude-bridge/eyes.py), because a picture the coder opens is looked at by Core's vision slot,
which the transcript does not bill. Core's slot names are spelled out as the models they run (core/models.json).
"""
import json
import pathlib
import sys

HOMES = (pathlib.Path.home() / ".claude", pathlib.Path("D:/Aaron/development/.skippy-workshop-core-claude"))
MODELS = {"coder": "Qwen3.6-35B-A3B (Skippy Core's coder slot: MTP, Q8_0)",
          "skippy-chat": "Qwen3.6-35B-A3B (Skippy Core's chat slot: UD-Q4_K_XL)",
          "chat": "Qwen3.6-35B-A3B (Skippy Core's chat slot: UD-Q4_K_XL)",
          "vision": "Qwen3-VL-8B-Instruct (Skippy Core's vision slot: Q8_0), describing the pictures the coder opened",
          "qwen3-8b-stock": "Qwen3-8B (Skippy Core's stock 8B, router-stock slot: Q4_K_M)",
          "router-stock": "Qwen3-8B (Skippy Core's stock 8B, router-stock slot: Q4_K_M)",
          "claude-haiku-5-5": "Claude Haiku 5.5"}
EYES = "for you instead. What it sees"     # eyes.py's reply: "Skippy Core's vision model looked at <path> for you instead. ..."


def find_transcript(work):
    """The newest session transcript Claude Code kept for a run made in this folder (either home: the Captain's or Core runs')."""
    key = "".join(c if c.isalnum() else "-" for c in str(pathlib.Path(work).resolve()))
    found = [f for home in HOMES for f in (home / "projects" / key).glob("*.jsonl")]
    return max(found, key=lambda f: f.stat().st_mtime) if found else None


def stats(path):
    last, started, looks = None, None, 0
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        started = started or r.get("timestamp")
        if r.get("type") == "cost-state":
            last = r
        looks += r.get("type") == "user" and EYES in line
    if not last:
        raise SystemExit(f"{path}: no cost-state record (the session never finished a turn)")
    models, tokens = [], {"input": 0, "cache_read": 0, "output": 0}
    for name, u in last["modelUsage"].items():
        t = {"input": u["inputTokens"] + u.get("cacheCreationInputTokens", 0), "cache_read": u["cacheReadInputTokens"],
             "output": u["outputTokens"]}
        for k in tokens:
            tokens[k] += t[k]
        models.append({"name": MODELS.get(name, name), "tokens": t})
    if looks:
        models.append({"name": MODELS["vision"], "looks": looks})
    tokens["total"] = sum(tokens.values())
    return {"started": started, "minutes": round(last["totalDuration"] / 60000), "tokens": tokens, "models": models}


if __name__ == "__main__":
    print(json.dumps(stats(sys.argv[1]), indent=1))


def single(path):
    """The same numbers for a one-call run (tools/voxel/minebench_api.py's run.json): no agent, no re-read context."""
    r = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    t = {"input": r["input_tokens"], "cache_read": 0, "output": r["output_tokens"]}
    return {"started": r["started"], "minutes": max(1, round(r["seconds"] / 60)), "tokens": {**t, "total": t["input"] + t["output"]},
            "models": [{"name": MODELS.get(r["model"], r["model"]), "tokens": t}]}
