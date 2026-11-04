"""Eyes for Skippy Core's coder (the Captain's rule, 2026-10-24: Private mode must work for real, not be routed around).

The coder slot (Qwen3-Coder-Next) is text-only. Handed a picture, Core refuses it ("image type not supported") and the whole run
fails. Two places a picture reaches it, both covered here with Core's own vision slot (Qwen3-VL, with its mmproj, always loaded):

  * a picture the Captain attaches: the bridge describes it before the run starts (workshop_engine._describe_pictures);
  * a picture the agent opens itself (a screenshot of its own portal page, an image in a repo): this file is also a PreToolUse
    hook on Read. On a Core run (SKIPPY_CORE_EYES=1, set by workshop_engine.core_env) a Read of an image is refused, and the
    refusal carries what the vision slot sees, every word copied out, which Claude Code shows the model in its place.

A picture the agent opens itself is looked at the way Claude looks at its own (the Captain, 2026-11-02: "We're trying to replicate
Haiku in functionality ... we're trying to improve the tool"): Core's big model (the chat slot, Qwen3.6-35B-A3B with its own vision
projector) is handed the task from the run's transcript with the picture, describes it (text copied exactly) and then reviews it
against the task, faults worst first. Without a task, or when the big model can't answer, the vision slot's plain description.
Measured on MineBench's rematch builds: the 8B vision slot found none of the faults the blind judge saw; the 35B with thinking off
found the big ones (a train across its rails, a train lost on its plate) and passed the control build. Thinking stays off: with it
on, one review thought for 8,000 tokens and said nothing. Checked on the Captain's own pictures too (2026-11-02: a plug label, photos of a
laptop screen, a Cloud Shell capture, portal pages): a question about a picture is answered from it first, never a critique of the
photo; a page is checked at all four edges; a deliberate style isn't a fault; it knows today's date; it reads at temperature 0, and a
small picture is doubled first (on a blurry 781-px capture it once invented a whole Terraform plan; doubled, it read the real text).

Exit 2 = refused with the description on stderr; 0 = carry on (any other file, or a Claude-brain run, which sees pictures itself).
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
MAX_BYTES = 12 * 1024 * 1024        # as big as a picture the agent may show (workshop_engine.MAX_SHOT_BYTES)
PROMPT = ("Describe this picture precisely for someone who can't see it and needs every detail: what it is, the layout from top "
          "to bottom, and ALL text in it copied exactly as printed (every word, number, code, symbol and logo; say which you can't "
          "read rather than guess), then colours, counts and anything unusual. No opinions.")
REVIEW_MODEL = "chat"               # the slot that reviews: Core's 35B (its mmproj is loaded: core/models.json)
REVIEW_PROMPT = ("Today is {today}. Someone is doing this task and has this picture in front of them:\n\n<task>\n{task}\n</task>\n\n"
                 "Look at it the way the person who asked would, and be honest: they want the truth, not reassurance.\n"
                 "1. Say precisely what the picture shows: the main things, the layout, and ALL text in it copied exactly as printed. "
                 "A character you can't make out, or one hidden behind something, is unreadable: say so, never guess it.\n"
                 "2. Then do what the task needs from this picture:\n"
                 "- If the task asks a question about what is in the picture (what is this, what does it say, what happened, is it "
                 "stuck), answer it plainly from what you can see, and say what the picture can't tell you.\n"
                 "- If the picture shows work being made or checked (a page, a screen, a build, a render), list what is wrong with that "
                 "work, worst first: anything broken, cut off by an edge of the screen or frame (look at all four edges), overlapping, "
                 "unreadable, misplaced, floating, sideways, out of proportion, lost in empty space or hard to recognise, and anything "
                 "someone would expect that is missing. Only faults you can actually see. A deliberate style is not a fault (icons without "
                 "words, a decorative background, a dark theme); a fault is something that doesn't work or can't be read. Then say "
                 "whether someone who didn't know the task would recognise at a glance what it is, and the three changes that would "
                 "improve it most.\n"
                 "Never critique the photo itself (angle, glare, framing) unless it stops you reading something. Steps about files, code, "
                 "commands or commits are checked elsewhere.")
FAULT_ASK = ("\nEnd with one last line, exactly `WORST FAULT: ` and then the single worst fault you listed, in one sentence the "
             "builder can act on, or `WORST FAULT: none` when nothing you can see is broken, cut off, unreadable, misplaced or hard to "
             "recognise. A fault is something plainly wrong at a glance; when the work does what was asked, say none, however it "
             "could still be improved (polish ideas don't count).")
DRAWN_AS = ("This picture is the builder's {file} as the project's own viewer draws it. The file itself is checked elsewhere, so judge "
            "only the work the picture shows, never whether the picture proves the file or its format. The drawing itself (its views, "
            "angles, lighting, scale, layout) is fixed and is never a fault.\n\n")
REFERENCE_SAID = ("There are two pictures. The FIRST is the reference: examples of the kind of thing the task asks for, side by side, "
                  "found before the work began. The SECOND is the work, possibly drawn from several views at once (that layout is the viewer's, "
                  "never a fault). Everything below about \"the picture\" means the second one. Focus on the reference, as the kind it "
                  "shows, not as the one to copy: the work must read, at a glance, as the same KIND of thing they all are. Its medium, style, colours "
                  "and level of detail are its own, and detail finer than the work's own scale is never a fault; so is a setting around it (a landscape, a station), the builder's choice, unless it hides the thing or makes it hard to find. What makes any example "
                  "of the kind recognisable:\n<reference>\n{sheet}\n</reference>\n"
                  "Start your review with that list, item by item: for each, whether the work shows it (present, wrong, or missing), "
                  "looking at the reference again for each one. Then say whether someone shown both pictures side by side would call "
                  "them the same kind of thing, and why.\n\n")
FAULT_ASK_REF = ("\nEnd with two last lines. First `RECOGNISED: ` and a whole number from 1 to 10: how surely someone shown only the "
                 "work, who didn't know the task, would name it as the reference's kind of thing at a glance (10: at once and without "
                 "doubt; 7: after a moment; 4: only once told; 1: never). Then `WORST FAULT: ` and the single biggest way the work fails "
                 "to read as that kind of thing (a listed item missing or wrong, a part far out of proportion, the thing hard to find in "
                 "its own scene), in one sentence the builder can act on, or `WORST FAULT: none`.")
RECOGNISED_PASS = 8                  # a review scoring the work this recognisable ends the loop, whatever detail it would still add
FIXED_ASK = "The builder was told to fix this fault last time: {fault}\nSay first, in one line, whether this picture shows it fixed.\n\n"
GATE_SAID = ("Stop hook feedback", "<local-command", "Caveat:")   # what the harness itself writes as a user turn: never the task
TASK_CHARS = 4000
ENLARGE_BELOW = 1200                 # longest side in pixels under which a picture is doubled before it is looked at


def describe(url: str, key: str, path: str, post=None, timeout: float = 180) -> str:
    """What Skippy Core's vision slot sees in the picture at `path`. Raises when Core can't be asked or says nothing."""
    data = Path(path).read_bytes()
    if len(data) > MAX_BYTES:
        raise RuntimeError(f"the picture is {len(data) // 1048576} MB, too big to send")
    data, mime = enlarged(data, IMAGE_TYPES.get(Path(path).suffix.lower(), "image/jpeg"))
    return ask(url, key, data, mime, PROMPT, post=post, timeout=timeout)


def task_of(transcript: str) -> str:
    """The latest thing the person asked in this run's transcript (their words, not tool results), or ''."""
    said = ""
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "user":
                    continue
                content = (d.get("message") or {}).get("content")
                if isinstance(content, list):
                    content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if d.get("isMeta") or d.get("isCompactSummary") or not isinstance(content, str) or content.strip().startswith(GATE_SAID):
                    continue
                if content.strip():
                    said = content.strip()
    except (OSError, TypeError):
        return ""
    return said[:TASK_CHARS]


def enlarged(data: bytes, mime: str) -> tuple[bytes, str]:
    """A small picture doubled (as a PNG) so its small text gets more of the vision encoder: on a 781-px Cloud Shell capture the
    35B read the real branch names every time at 2x, a third of the time as sent. Big ones, and anything Pillow can't open, as they are."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if max(im.size) > ENLARGE_BELOW:
            return data, mime
        out = io.BytesIO()
        im.convert("RGB").resize((im.width * 2, im.height * 2), Image.LANCZOS).save(out, "PNG")
        return out.getvalue(), "image/png"
    except Exception:                                            # noqa: BLE001 — no Pillow, or not a picture it knows: send it as it is
        return data, mime


def _today() -> str:
    import datetime
    return datetime.date.today().strftime("%A %d %B %Y")


def picture(path: str) -> tuple[bytes, str]:
    """The picture at `path` as (bytes, mime), a small one doubled. Raises when it is too big to send."""
    data = Path(path).read_bytes()
    if len(data) > MAX_BYTES:
        raise RuntimeError(f"the picture is {len(data) // 1048576} MB, too big to send")
    return enlarged(data, IMAGE_TYPES.get(Path(path).suffix.lower(), "image/jpeg"))


def review(url: str, key: str, path: str, task: str, post=None, timeout: float = 120, before: str = "", after: str = "",
           first: tuple = ()) -> str:
    """Core's big model's look at the picture at `path` against `task`: what it shows, then its faults. Raises like ask()."""
    data, mime = picture(path)
    return ask(url, key, data, mime, before + REVIEW_PROMPT.format(task=task, today=_today()) + after,
               max_tokens=3000, post=post, timeout=timeout, model=REVIEW_MODEL, system="You review pictures honestly and precisely.",
               first=first)


def worst_fault(url: str, key: str, path: str, task: str, previous: str = "", post=None, timeout: float = 180,
                drawn: str = "", reference: dict | None = None) -> tuple[str, str]:
    """(the review, its one worst fault or '' when it names none) for the work check's fix-the-worst-fault loop (work_check.py);
    `previous` is the fault the builder was last told to fix, which the review first says is fixed or not; `drawn` names the file the
    picture is a drawing of (else Core judged the picture for not showing the file); `reference` is the run's reference
    (reference.py: its picture and sheet), sent ahead of the work so every review re-compares the work with it. Raises like
    ask(), and when the review ends without naming one."""
    import re
    first, said = (), ""
    if reference:
        try:
            first, said = (picture(reference["image"]),), REFERENCE_SAID.format(sheet=reference.get("sheet") or "(no sheet)")
        except (OSError, KeyError, RuntimeError):
            first, said = (), ""                      # the reference is gone: review against the task alone
    seen = review(url, key, path, task, post=post, timeout=timeout, first=first,
                  before=said + (DRAWN_AS.format(file=drawn) if drawn else "") + (FIXED_ASK.format(fault=previous) if previous else ""),
                  after=FAULT_ASK_REF if said else FAULT_ASK)
    named = re.findall(r"WORST FAULT:\**\s*(.+?)\s*$", seen, re.M)
    if not named:
        raise RuntimeError("the review named no worst fault")
    worst = named[-1].strip().strip("*`").strip()
    score = re.findall(r"RECOGNISED:\**\s*(\d+)", seen)
    if said and score and int(score[-1]) >= RECOGNISED_PASS:
        return seen, ""                  # a stranger would name it: against a reference the 35B always finds one more detail to add
    return seen, "" if worst.lower().rstrip(".").strip() in ("none", "") else worst


def ask(url: str, key: str, data: bytes | None, mime: str, prompt: str, max_tokens: int = 1500, post=None, timeout: float = 180,
        model: str = "vision", system: str = "You describe pictures precisely.", first: tuple = ()) -> str:
    """One question about one picture (its bytes; None for a question with no picture) to one of Skippy Core's slots (the vision
    slot unless told), thinking off; its words. `first`: (bytes, mime) pictures sent ahead of it (a reference to compare with).
    Raises when Core can't be asked or says nothing."""
    import urllib.request
    if not (url and key):
        raise RuntimeError("Skippy Core isn't configured")
    pictures = [*first, *([(data, mime)] if data is not None else [])]
    content = [{"type": "image", "source": {"type": "base64", "media_type": m, "data": base64.b64encode(d).decode()}} for d, m in pictures]
    body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
                       "messages": [{"role": "user", "content": content + [{"type": "text", "text": prompt}]}]}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/v1/messages", data=body, method="POST",
                                 headers={"x-api-key": key, "Authorization": "Bearer " + key,
                                          "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
    if post is not None:
        got = post(req)
    else:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read().decode("utf-8", "replace"))
    text = " ".join(b.get("text", "") for b in (got or {}).get("content", []) if isinstance(b, dict) and b.get("type") == "text").strip()
    if not text:
        raise RuntimeError("the vision model said nothing")
    return text


def check(event: dict, env=os.environ, post=None) -> tuple[int, str]:
    """(exit code, message) for one PreToolUse event."""
    if env.get("SKIPPY_CORE_EYES") != "1" or event.get("tool_name") != "Read":
        return 0, ""
    path = str((event.get("tool_input") or {}).get("file_path") or "")
    if Path(path).suffix.lower() not in IMAGE_TYPES:
        return 0, ""
    if not Path(path).is_absolute():
        path = str(Path(env.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".") / path)
    if not Path(path).is_file():
        return 0, ""                                             # let Read say the file isn't there
    url, key = env.get("ANTHROPIC_BASE_URL", ""), env.get("ANTHROPIC_API_KEY", "")
    task = task_of(str(event.get("transcript_path") or ""))
    if task:
        try:
            seen = review(url, key, path, task, post=post)
            return 2, (f"You can't see pictures, so this Read was not done (it would fail the whole run). Skippy Core's big model "
                       f"looked at {path} for you instead, with your task in mind. What it sees, and what it found wrong:\n\n{seen}")
        except Exception:                                        # noqa: BLE001 — the vision slot's description is still worth having
            pass
    try:
        seen = describe(url, key, path, post=post, timeout=60)
    except Exception as exc:                                     # noqa: BLE001 — still refuse: the coder can't take the picture either way
        return 2, (f"You can't see pictures, and Skippy Core's vision model couldn't look at {path} for you "
                   f"({type(exc).__name__}: {exc}). Don't open image files; say what you needed from it.")
    return 2, (f"You can't see pictures, so this Read was not done (it would fail the whole run). Skippy Core's vision model "
               f"looked at {path} for you instead. What it sees:\n\n{seen}")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    code, message = check(event)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
