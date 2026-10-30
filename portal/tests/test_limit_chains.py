"""The limits along each tool chain, checked against each other (the Captain's rule, 2026-10-24: a request one hop accepts must not
be refused, cut short or timed out by the next). Each test reads the real numbers from every hop of one chain and fails when they
stop nesting: a downstream hop takes at least what the upstream one can send (after base64), and an outer wait outlasts the work
inside it. Change one hop and this tells you which others must move with it."""
import json
import re
from pathlib import Path

import attachments
import chat_history
import media
import mentat_client
import patient
import popup_in
import popups
import workshop

ROOT = Path(__file__).resolve().parents[2]


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def const(rel: str, name: str) -> float:
    """A module-level number from a file this test can't import (the bridge, the reporter): `NAME = 12 * 1024` style."""
    m = re.search(rf"^{name}\s*=\s*([0-9_*\s.]+?)\s*(?:#|$)", text(rel), re.M)
    assert m, f"{name} not found in {rel}"
    return eval(m.group(1).replace("_", ""), {"__builtins__": {}})   # noqa: S307 — digits and * only, from our own repo


def page_const(name: str) -> int:
    m = re.search(rf"\b{name} = (\d+)", text("portal/templates/_chat.html"))
    assert m, f"{name} not found in _chat.html"
    return int(m.group(1))


B64 = 4 / 3


def test_a_message_to_the_agent_fits_every_hop():
    page, portal = page_const("AGENT_TEXT"), workshop.MAX_MESSAGE
    bridge = const("services/claude-bridge/workshop_engine.py", "MAX_MESSAGE")
    log = const("services/claude-bridge/conversation_log.py", "MAX_MESSAGE")
    assert page <= portal == bridge == log
    # ...and fits the coder's window: ~30K tokens of Claude Code's own prompt + the reply's reserve + the message (~4 chars a token)
    context = int(re.search(r'"WORKSHOP_CORE_CONTEXT", "(\d+)"', text("services/claude-bridge/workshop_engine.py")).group(1))
    reserve = int(re.search(r'CORE_MAX_OUTPUT = "(\d+)"', text("services/claude-bridge/workshop_engine.py")).group(1))
    assert 30_000 + reserve + portal / 4 < context


def test_pictures_and_files_the_page_sends_fit_both_chats():
    each, total = page_const("FILE_B64"), page_const("FILES_B64")
    assert each <= min(workshop.MAX_IMAGE_B64, attachments.MAX_B64_EACH)
    assert total <= min(workshop.MAX_IMAGES_B64, attachments.MAX_B64_TOTAL)
    assert "3100000" in text("portal/templates/_workshop.html") and 3_100_000 <= workshop.MAX_IMAGES_B64     # the agent's own composer
    assert workshop.MAX_IMAGE_B64 * 3 / 4 <= const("services/claude-bridge/workshop_engine.py", "MAX_IMAGE_BYTES")   # ...and the bridge


def test_a_picture_the_agent_shows_fits_every_hop():
    shot = const("services/claude-bridge/workshop_engine.py", "MAX_SHOT_BYTES")
    assert const("tools/workshop_screenshot.py", "MAX_BYTES") <= shot <= workshop.MAX_IMAGE_BYTES
    assert shot * B64 + 100_000 <= workshop.MAX_PUSH < 32 * 1024 * 1024                      # one push, under Cloud Run's 32 MB
    assert const("services/claude-bridge/eyes.py", "MAX_BYTES") >= shot                      # a Core run can still look at it
    assert "20 + len(req.data) / 200_000" in text("services/home-reporter/reporter.py")     # the push's timeout grows with it


def test_a_finished_picture_or_song_is_taken_wherever_it_is_kept():
    assert set(patient.TYPES) == media.TYPES == set(popups.OUTPUT_TYPES)       # pop-up results too: 3D models, videos, JSON, text
    assert const("services/home-reporter/reporter.py", "JSON_MAX") >= 1_000_000


def test_the_waits_for_mentat_nest_outside_its_work():
    call = int(re.search(r'MENTAT_CALL_TIMEOUT=(\d+)', text("core/compose.core.yml")).group(1))     # the one Mentat, on skippy-core
    assert call + 5 <= mentat_client.TIMEOUT < mentat_client.ASK_TIMEOUT                # a tool call, then a question (Qwen reads it first)
    assert "/ask" not in text("services/home-reporter/reporter.py")                    # no second road to Mentat through skippy-pc


def test_the_captains_long_chats_keep_saving():
    assert chat_history.MAX_CHAT_BYTES_FOREVER * 4 <= chat_history.MAX_FILE_BYTES_FOREVER


def test_the_portal_has_a_thread_for_every_request_cloud_run_sends_it():
    threads = int(re.search(r"--threads (\d+)", text("portal/Dockerfile")).group(1))
    flags = json.loads(text("core/cd/deploy.json"))["portal"]["flags"]
    concurrency = int(flags[flags.index("--concurrency") + 1])
    assert threads == concurrency >= 32


def test_a_report_probe_finishes_inside_the_report():
    budget = float(re.search(r'"GATHER_BUDGET", "([\d.]+)"', text("services/home-reporter/reporter.py")).group(1))
    probe = int(re.search(r'fetch\(f"\{API\}/status.json", timeout=(\d+)\)', text("services/home-reporter/reporter.py")).group(1))
    assert probe < budget


def test_a_popup_job_and_its_file_fit_every_hop():
    reporter = "services/home-reporter/reporter.py"
    # the file a person attaches (or the agent names) rides in the job: the reporter reads the whole job list, the service takes it
    assert workshop.MAX_POPUP_INPUT_B64 <= attachments.MAX_B64_EACH
    assert const("services/claude-bridge/workshop_engine.py", "MAX_POPUP_ARGS") == workshop.MAX_POPUP_ARGS
    assert attachments.MAX_B64_EACH + 100_000 <= const(reporter, "JSON_MAX")
    assert attachments.MAX_B64_EACH + 100_000 <= const("services/popups/echo/echo.py", "MAX_BODY")
    assert workshop.MAX_POPUP_INPUT_B64 + 100_000 <= workshop.MAX_PUSH
    # its result: what the reporter sends, the portal holds; the types and the longest wait agree on both sides
    assert const(reporter, "CLIP_MAX") <= media.MAX_ONE
    types = re.search(r"POPUP_TYPES = \{(.*?)\}", text(reporter), re.S).group(1)
    assert set(re.findall(r'"([a-z]+/[a-z0-9.+-]+)"', types)) == set(popups.OUTPUT_TYPES)
    assert const(reporter, "POPUP_MAX_TIMEOUT") == popups.MAX_TIMEOUT_S <= patient.JOB_TTL


def test_a_big_file_for_a_popup_is_taken_at_every_hop_home():
    """docs/popups.md "Big files": the page, the portal's pass, the gate, the reporter and the agent's bridge agree on the one cap."""
    cap = popups.MAX_INPUT_MB * 1024 * 1024
    assert popup_in.MAX_BYTES == cap == const("services/popup-gate/gate.py", "MAX_BYTES")
    assert const("services/home-reporter/reporter.py", "POPUP_INPUT_MAX") == cap
    assert const("services/claude-bridge/workshop_engine.py", "MAX_POPUP_INPUT") == cap
    assert "BIG_MAX = 64 * 1024 * 1024" in text("portal/templates/_chat.html") and popups.MAX_INPUT_MB == 64
    assert popup_in.DAILY_GUEST_BYTES >= cap                                  # a guest can send at least one whole file a day
    assert const("services/popup-gate/gate.py", "KEEP_S") >= patient.JOB_TTL # the file outlives the longest wait for its job
    assert int(re.search(r'"--timeout", "(\d+)"', text("services/popup-gate/Dockerfile")).group(1)) >= 600   # a slow phone upload
