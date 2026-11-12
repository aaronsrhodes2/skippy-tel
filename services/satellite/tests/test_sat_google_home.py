"""His Google calendar, mail and contacts, read only (google_home.py): what the routines get from each, against canned API answers."""
import asyncio
import json
from datetime import date

import google_home


def api(url):
    if "calendarList" in url:
        return {"items": [{"id": "me@gmail.com", "summary": "Aaron", "primary": True, "selected": True},
                          {"id": "aaron@workco.com", "summary": "Work", "selected": True},
                          {"id": "en.usa#holiday@group.v.calendar.google.com", "summary": "Holidays", "selected": True}]}
    if "/events" in url and "workco" in url:
        return {"items": [{"summary": "Standup", "start": {"dateTime": "2026-11-02T09:00:00-08:00"}, "end": {"dateTime": "2026-11-02T09:15:00-08:00"}},
                          {"summary": "Declined", "start": {"dateTime": "2026-11-02T10:00:00-08:00"}, "end": {"dateTime": "2026-11-02T11:00:00-08:00"},
                           "attendees": [{"self": True, "responseStatus": "declined"}]}]}
    if "/events" in url:
        return {"items": [{"summary": "Dentist", "location": "12 Main St", "start": {"dateTime": "2026-11-02T08:00:00-08:00"},
                           "end": {"dateTime": "2026-11-02T08:30:00-08:00"}}, {"summary": "Trash day", "start": {"date": "2026-11-02"}, "end": {"date": "2026-11-03"}}]}
    if "holiday" in url:
        raise AssertionError("holiday calendars are not read")
    if "connections" in url:
        return {"connections": [{"names": [{"displayName": "Mom"}], "emailAddresses": [{"value": "Mom@Example.com"}], "birthdays": [{"date": {"month": 11, "day": 2}}]}]}
    if "/messages?" in url:
        return {"messages": [{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}]}
    msgs = {"1": ("Mom <mom@example.com>", "Dinner Sunday?", "Are you coming"), "2": ("PG&E <bills@pge.com>", "Your bill is ready", "Amount due Nov 20"),
            "3": ("Amazon <ship@amazon.com>", "Out for delivery: cables", "Arriving today by 8pm"), "4": ("Deals <x@shop.com>", "Sale!", "50% off")}
    for k, (f, s, sn) in msgs.items():
        if f"/messages/{k}?" in url:
            return {"snippet": sn, "payload": {"headers": [{"name": "From", "value": f}, {"name": "Subject", "value": s}]}}
    if "/users/@me/lists" in url:
        return {"items": [{"id": "L"}]}
    if "/lists/L/tasks" in url:
        return {"items": [{"title": "Renew passport", "due": "2026-11-03T00:00:00.000Z"}, {"title": "Someday", "due": "2026-12-01T00:00:00.000Z"}]}
    raise AssertionError(url)


def g(tmp_path, env=None):
    return google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "tok.json"), "GOOGLE_HOME_CLIENT": str(tmp_path / "client.json"), **(env or {})},
                              opener=api, log=lambda m: None)


def test_events_mail_contacts_and_todos(tmp_path):
    gg = g(tmp_path)
    ev = gg.events(date(2026, 11, 2))
    assert [e["summary"] for e in ev] == ["Trash day", "Dentist", "Standup"]          # all-day first, then by time; declined and holidays left out
    assert ev[2]["work"] and not ev[1]["work"]
    assert gg.work_calendar()["summary"] == "Work"
    kinds = {m["subject"]: m["kind"] for m in gg.mail()}
    assert kinds == {"Dinner Sunday?": "personal", "Your bill is ready": "bill", "Out for delivery: cables": "package", "Sale!": "other"}
    assert gg.birthdays(date(2026, 11, 2)) == ["Mom"]
    assert gg.todos(date(2026, 11, 3)) == ["Renew passport (due 2026-11-03)"]


def test_a_free_busy_only_work_calendar_gives_its_busy_times_without_titles(tmp_path):
    # 2026-11-01: JFrog shares only free/busy with his Gmail account; the briefing still knows when he's in meetings
    gg = g(tmp_path, {"GOOGLE_WORK_CALENDAR": "aaronr@jfrog.com", "SKIPPY_HOME_TOKEN": "home"})
    gg.get_json = lambda url, headers: {"connected": True, "access_token": "a", "expires_in": 3500}
    gg.post_json = lambda url, body: {"calendars": {"aaronr@jfrog.com": {"busy": [{"start": "2026-11-02T15:30:00Z", "end": "2026-11-02T18:30:00Z"}]}}}
    ev = gg.events(date(2026, 11, 2))
    work = [e for e in ev if e["work"]]
    assert len(work) == 1 and work[0]["summary"].startswith("busy (a work meeting") and gg.work_busy is True
    asked = []

    async def word(task, material, n=400):
        asked.append(material)
        return "ok"
    gg.clock = lambda: 1793606400.0
    asyncio.run(google_home.extras(gg, word)["calendar"]())
    assert "titles aren't shared" in asked[0] and "NOT shared" not in asked[0]


def test_a_work_calendar_that_is_not_shared_is_said(tmp_path):
    gg = g(tmp_path, {"GOOGLE_WORK_CALENDAR": "someone@else.com", "SKIPPY_HOME_TOKEN": "home"})
    gg.post_json = lambda url, body: {"calendars": {"someone@else.com": {"errors": [{"reason": "notFound"}]}}}
    gg.get_json = lambda url, headers: {"connected": True, "access_token": "a", "expires_in": 3500}
    gg.clock = lambda: 1793606400.0                                        # 2026-11-02, local
    asked = []

    async def word(task, material, n=400):
        asked.append(material)
        return "ok"
    ex = google_home.extras(gg, word)
    asyncio.run(ex["calendar"]())
    assert "NOT shared" in asked[0]


def test_not_signed_in_yet_says_so_and_the_rest_stays_quiet(tmp_path):
    gg = google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "none.json"), "GOOGLE_HOME_CLIENT": str(tmp_path / "c.json")}, log=lambda m: None)

    async def word(*a):
        return "x"
    ex = google_home.extras(gg, word, log=lambda m: None)
    said = asyncio.run(ex["calendar"]())
    assert "Google isn't connected" in said and "Connect Google for Skippy in Admin" in said
    assert asyncio.run(ex["mail"]()) is None and asyncio.run(ex["birthdays"]()) is None


def test_the_token_is_refreshed_and_kept_only_in_the_vault_file(tmp_path):
    (tmp_path / "client.json").write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "cs"}}))
    (tmp_path / "tok.json").write_text(json.dumps({"refresh_token": "r1"}))
    posts = []

    def post_form(url, data):
        posts.append(data)
        return {"access_token": "a1", "expires_in": 3600}
    gg = google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "tok.json"), "GOOGLE_HOME_CLIENT": str(tmp_path / "client.json")}, post_form=post_form)
    assert gg.access_token() == "a1" and gg.access_token() == "a1" and len(posts) == 1
    assert posts[0]["grant_type"] == "refresh_token" and posts[0]["refresh_token"] == "r1"


def test_without_a_local_grant_the_token_is_borrowed_from_the_portal_with_the_home_token(tmp_path):
    asked = []

    def portal(url, headers):
        asked.append((url, headers))
        return {"connected": True, "access_token": "pa1", "expires_in": 3500, "expires_at": 0}
    gg = google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "none.json"), "GOOGLE_HOME_CLIENT": str(tmp_path / "none-c.json"),
                             "SKIPPY_HOME_TOKEN": "hometok", "PORTAL_URL": "https://portal.example/"}, get_json=portal,
                            post_form=lambda *a: (_ for _ in ()).throw(AssertionError("no local refresh")), log=lambda m: None)
    assert not gg.local and gg.connected
    assert gg.access_token() == "pa1" and len(asked) == 1                               # cached while it lasts
    assert asked[0] == ("https://portal.example/home/google/access", {"Authorization": "Bearer hometok"})


def test_the_portal_saying_not_connected_is_not_connected_and_is_asked_again_after_a_minute(tmp_path):
    now, asked = [1000.0], []

    def portal(url, headers):
        asked.append(url)
        return {"connected": False, "why": "not connected: press Connect Google for Skippy in Admin"}
    gg = google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "n.json"), "GOOGLE_HOME_CLIENT": str(tmp_path / "c.json"), "SKIPPY_HOME_TOKEN": "h"},
                            get_json=portal, clock=lambda: now[0], log=lambda m: None)
    assert not gg.connected and not gg.connected and len(asked) == 1
    now[0] += 61
    assert not gg.connected and len(asked) == 2


def test_a_portal_that_cannot_be_reached_is_not_called_not_connected(tmp_path):
    def down(url, headers):
        raise OSError("no route")
    gg = google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "n.json"), "GOOGLE_HOME_CLIENT": str(tmp_path / "c.json"), "SKIPPY_HOME_TOKEN": "h"},
                            get_json=down, log=lambda m: None)
    assert gg.connected                                                                 # unknown: the read that follows is logged instead


def test_the_local_grant_still_wins_when_it_is_there(tmp_path):
    (tmp_path / "client.json").write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "cs"}}))
    (tmp_path / "tok.json").write_text(json.dumps({"refresh_token": "r1"}))
    gg = google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "tok.json"), "GOOGLE_HOME_CLIENT": str(tmp_path / "client.json"), "SKIPPY_HOME_TOKEN": "h"},
                            post_form=lambda url, data: {"access_token": "local", "expires_in": 3600},
                            get_json=lambda *a: (_ for _ in ()).throw(AssertionError("the portal isn't asked")))
    assert gg.local and gg.access_token() == "local"
