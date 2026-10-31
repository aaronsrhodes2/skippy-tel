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


def test_a_work_calendar_that_is_not_shared_is_said(tmp_path):
    gg = g(tmp_path, {"GOOGLE_WORK_CALENDAR": "someone@else.com"})
    (tmp_path / "tok.json").write_text("{}")
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
    assert "sign-in hasn't been done" in asyncio.run(ex["calendar"]())
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
