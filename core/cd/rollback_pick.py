#!/usr/bin/env python3
"""Which Cloud Run revision should the `rollback` workflow serve, and the last few revisions for its job summary.

.github/workflows/rollback.yml runs this between two gcloud calls: it cannot test the cloud steps, but this decision is
plain Python on the JSON `gcloud run revisions list --format=json` prints, so it is tested (tools/tests/test_rollback_pick.py).

    python3 tools/rollback_pick.py --revisions revisions.json --serving skippy-portal-00042-abc \
        [--want ""|latest|<revision name>] [--summary "$GITHUB_STEP_SUMMARY"] [--limit 5]

Prints the chosen revision name (or the word `latest`) on stdout. Exit 2, with the reason on stderr, when there is nothing
safe to choose. Whatever happens, the summary table is written first, so the person on a phone still sees the list.

The blank choice is "step back one": the newest healthy revision OLDER than the one serving now. Not "the newest other
than the serving one": after a failed deploy rolled itself back, the serving revision is the old good one and the newest
is the broken one, and a blank rollback must never send the portal back onto that.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

NAME = re.compile(r"[a-z][a-z0-9-]{0,62}")
SHA = re.compile(r"[0-9a-f]{40}")
TIME = re.compile(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?")


class Nothing(Exception):
    """There is no safe revision to choose; the message says why, in plain English."""


def _get(obj, *path):
    for key in path:
        if isinstance(key, int):
            obj = obj[key] if isinstance(obj, list) and len(obj) > key else None
        else:
            obj = obj.get(key) if isinstance(obj, dict) else None
        if obj is None:
            return None
    return obj


def when(created: str | None) -> tuple:
    """A sortable form of an RFC 3339 time (`2026-09-30T05:30:11.123456Z`); zeros when it is missing or odd."""
    m = TIME.match(created or "")
    if not m:
        return (0, 0, 0, 0, 0, 0, 0)
    *parts, frac = m.groups()
    return (*map(int, parts), int((frac or "0").ljust(9, "0")[:9]))


def normalize(rev: dict) -> dict:
    """One revision as `{name, created, ready, image, sha}`. `ready` is True/False, or None when the JSON doesn't say."""
    name = _get(rev, "metadata", "name") or rev.get("name") or ""
    created = _get(rev, "metadata", "creationTimestamp") or rev.get("creationTimestamp") or rev.get("creation_timestamp") or ""
    ready = None
    for cond in _get(rev, "status", "conditions") or []:
        if isinstance(cond, dict) and cond.get("type") == "Ready":
            ready = str(cond.get("status")) == "True"
    image = _get(rev, "spec", "containers", 0, "image") or ""
    tag = image.rsplit(":", 1)[-1]               # deploy.yml tags every image with the commit: a 40-hex git sha
    return {"name": str(name), "created": str(created), "ready": ready, "image": image,
            "sha": tag[:7] if SHA.fullmatch(tag) else ""}


def newest_first(revisions: list) -> list[dict]:
    revs = [normalize(r) for r in revisions if isinstance(r, dict)]
    revs = [r for r in revs if r["name"]]
    # The name's generation number (`-00042-`) rises with every deploy, so it breaks a tie in the timestamps.
    return sorted(revs, key=lambda r: (when(r["created"]), r["name"]), reverse=True)


def table(revs: list[dict], serving: str, limit: int = 5) -> str:
    """Markdown for the job summary: the last `limit` revisions, newest first, the serving one marked."""
    lines = ["### Revisions (newest first)", "", "| | Revision | Deployed (UTC) | Commit | Healthy |", "|---|---|---|---|---|"]
    for r in revs[:limit]:
        mark = "**serving**" if r["name"] == serving else ""
        ok = {True: "yes", False: "NO", None: "?"}[r["ready"]]
        lines.append(f"| {mark} | `{r['name']}` | {r['created'][:19].replace('T', ' ')} | {('`' + r['sha'] + '`') if r['sha'] else ''} | {ok} |")
    if not revs:
        lines.append("| | (none listed) | | | |")
    return "\n".join(lines) + "\n"


def pick(revs: list[dict], serving: str, want: str = "") -> str:
    """The revision to serve: `want` (a name, or `latest`), else one step back from `serving`."""
    want = (want or "").strip()
    if want == "latest":
        return "latest"
    if want:
        if not NAME.fullmatch(want):
            raise Nothing(f"'{want}' is not a revision name.")
        if want not in {r["name"] for r in revs}:
            raise Nothing(f"No revision called {want} exists (see the list above). Leave the box blank to step back one.")
        return want
    if not serving:
        raise Nothing("Could not tell which revision is serving now, so there is no 'previous' to step back to. "
                      "Run again and type a revision name from the list.")
    now = next((r for r in revs if r["name"] == serving), None)
    if now is None:
        raise Nothing(f"{serving} is serving but is not in the revision list, so there is no 'previous' to step back to. "
                      "Run again and type a revision name from the list.")
    older = [r for r in revs if (when(r["created"]), r["name"]) < (when(now["created"]), now["name"]) and r["ready"] is not False]
    if not older:
        raise Nothing(f"{serving} is the oldest healthy revision there is: nothing earlier to roll back to.")
    return older[0]["name"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--revisions", required=True, help="the JSON from `gcloud run revisions list --format=json`")
    ap.add_argument("--serving", default="", help="the revision serving traffic now")
    ap.add_argument("--want", default="", help="blank (one step back), `latest`, or a revision name")
    ap.add_argument("--summary", default="", help="append the revision table to this file ($GITHUB_STEP_SUMMARY)")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args(argv)
    try:
        with open(args.revisions, encoding="utf-8") as fh:
            data = json.load(fh)
        revs = newest_first(data if isinstance(data, list) else [])
    except (OSError, ValueError) as exc:
        print(f"Could not read the revision list: {exc}", file=sys.stderr)
        return 2
    if args.summary:
        try:
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write(table(revs, args.serving.strip(), args.limit) + "\n")
        except OSError as exc:
            print(f"(could not write the summary: {exc})", file=sys.stderr)
    try:
        print(pick(revs, args.serving.strip(), args.want))
    except Nothing as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
