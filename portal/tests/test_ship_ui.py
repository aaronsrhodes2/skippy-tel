"""The ship card in the page's REAL script (node, fake browser from test_workshop_ui.py): a `ship` event becomes one card
that polls every 10 s, amber while the deploy runs, green only when final and live, red with the run and Roll back
links when it failed; it stops polling when final, shows only safe links, and the commit's subject only as text."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

from test_workshop_ui import S, drive, env, needs_node, page  # noqa: E402,F401

SHA = "a" * 40
SHIP = {"t": "ship", "project": "skippytel", "repo": "aaronsrhodes2/skippy-tel", "branch": "main", "sha": SHA,
        "subject": "<img src=x onerror=alert(1)> make it green", "workflow": "deploy.yml"}
COMMIT = f"https://github.com/aaronsrhodes2/skippy-tel/commit/{SHA}"
RUN = "https://github.com/aaronsrhodes2/skippy-tel/actions/runs/7"
STAGES = [{"name": "tests", "state": "done"}, {"name": "build", "state": "running"}, {"name": "deploy", "state": "waiting"}]


def deploying():
    return {"final": False, "tone": "wait", "headline": "Deploying aaaaaaa: build…", "note": "", "stages": STAGES,
            "links": {"commit": COMMIT, "run": RUN}, "short": "aaaaaaa", "subject": SHIP["subject"]}


def live():
    return {"final": True, "tone": "ok", "headline": "Live: the portal serves aaaaaaa.", "note": "",
            "stages": [{"name": n, "state": "done"} for n in ("tests", "build", "deploy")],
            "links": {"commit": COMMIT}, "short": "aaaaaaa", "subject": SHIP["subject"]}


@needs_node
def test_a_ship_event_becomes_a_card_that_polls_until_it_is_live_then_stops(page):
    out = drive(page, [{"adv": 1500}, {"snap": "first"}, {"adv": 10 * S}, {"snap": "second"}, {"adv": 10 * S}, {"snap": "live"},
                       {"adv": 60 * S}, {"snap": "later"}],
                tasks={"t1": {"state": "done", "events": [{"t": "text", "text": "Pushed."}, SHIP]}}, follow="t1",
                ship={SHA: [deploying(), deploying(), live()]})
    s = out["snaps"]
    [card] = s["first"]["ships"]
    assert card["cls"] == "ship wait" and "Deploying aaaaaaa: build…" in card["text"]
    assert "✓ tests | … build | · deploy" in card["text"] and SHIP["subject"] in card["text"]   # text, never markup
    assert card["links"] == [{"text": "Commit aaaaaaa", "href": COMMIT, "target": "_blank"},
                             {"text": "Deploy run", "href": RUN, "target": "_blank"}]
    [card] = s["live"]["ships"]
    assert card["cls"] == "ship ok" and "Live: the portal serves aaaaaaa." in card["text"]
    assert s["live"]["shipPolls"] == 3 and s["later"]["shipPolls"] == 3                           # final: no more polls
    assert len(s["later"]["ships"]) == 1                                                          # one card, not one per redraw


@needs_node
def test_a_failed_deploy_is_red_with_the_run_and_roll_back_and_bad_links_are_dropped(page):
    failed = {**deploying(), "final": True, "tone": "bad", "headline": "The deploy of aaaaaaa failed at deploy.",
              "links": {"commit": COMMIT, "run": RUN, "rollback": "/admin#recovery", "workflow": "javascript:alert(1)"}}
    out = drive(page, [{"adv": 1500}, {"snap": "s"}, {"adv": 30 * S}, {"snap": "t"}],
                tasks={"t1": {"state": "done", "events": [SHIP]}}, follow="t1", ship={SHA: [failed]})
    [card] = out["snaps"]["s"]["ships"]
    assert card["cls"] == "ship bad"
    assert [link["href"] for link in card["links"]] == [COMMIT, RUN, "/admin#recovery"]          # the javascript: one is gone
    assert card["links"][-1]["target"] == ""                                                      # Roll back: same tab
    assert out["snaps"]["t"]["shipPolls"] == 1


@needs_node
def test_a_card_gives_up_after_40_minutes_but_keeps_the_commit_link(page):
    out = drive(page, [{"adv": 41 * 60 * S}, {"snap": "s"}, {"adv": 60 * S}, {"snap": "t"}],
                tasks={"t1": {"state": "done", "events": [SHIP]}}, follow="t1", ship={SHA: [deploying()]})
    [card] = out["snaps"]["s"]["ships"]
    assert "Stopped watching after 40 min." in card["text"] and card["links"][0]["href"] == COMMIT
    assert out["snaps"]["s"]["shipPolls"] == out["snaps"]["t"]["shipPolls"]


@needs_node
def test_a_push_the_portal_no_longer_knows_says_so_once_and_keeps_its_commit_link(page):
    out = drive(page, [{"adv": 1500}, {"snap": "s"}, {"adv": 60 * S}, {"snap": "t"}],
                tasks={"t1": {"state": "done", "events": [SHIP]}}, follow="t1", ship={SHA: [{"__status": 404}]})
    [card] = out["snaps"]["s"]["ships"]
    assert "redeployed since" in card["text"] and card["links"][0]["href"] == COMMIT
    assert out["snaps"]["t"]["shipPolls"] == 1                                                   # final: asked once


@needs_node
def test_a_screenshot_shows_inline_from_the_portal_and_never_from_the_event(page):
    out = drive(page, [{"adv": 1500}, {"snap": "s"}],
                tasks={"abcd1234": {"state": "done", "events": [{"t": "text", "text": "Here it is."},
                                                                {"t": "image", "mime": "image/png", "name": "me.png", "n": 0},
                                                                {"t": "image", "n": "0 onerror=alert(1)"}]}},
                follow="abcd1234")
    s = out["snaps"]["s"]
    assert s["imgs"] == [{"src": "/me/workshop/image?task=abcd1234&n=0", "alt": "Screenshot of the change"}]
    assert s["log"][-1]["text"].startswith("Here it is.")


@needs_node
def test_a_ship_event_with_a_bad_sha_is_just_a_line(page):
    out = drive(page, [{"adv": 1500}, {"snap": "s"}],
                tasks={"t1": {"state": "done", "events": [{**SHIP, "sha": "nope"}]}}, follow="t1")
    assert out["snaps"]["s"]["ships"] == [] and out["snaps"]["s"]["shipPolls"] == 0
    assert out["snaps"]["s"]["log"][-1]["text"] == "› pushed"
