"""purple Skippy's texts_latest_code (portal/texts.py): one GET to the portal's home door; the code and who sent it in words, 'no recent code',
or why his texts can't be read; a failing portal is an error in words."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import house_portal_tools as tools  # noqa: E402


def ask(answer, status=200):
    seen = []

    def request(method, url, body=None, **kw):
        seen.append((method, url, body))
        return status, answer
    return request, seen


def test_a_fresh_code_is_given_with_who_sent_it_how_long_ago_and_digit_by_digit():
    request, seen = ask({"connected": True, "why": "", "code": "482913", "sender": "22000", "at": 1, "age_s": 130})
    out = tools.texts_latest_code({}, request=request)
    assert seen == [("GET", tools.PORTAL_URL + "/home/texts/code", None)]
    assert out == "The code is 482913 (from 22000, 2 min ago). Digit by digit: 4 8 2 9 1 3."
    assert "just now" in tools.texts_latest_code({}, request=ask({"code": "7788", "sender": "Bob", "age_s": 12})[0])
    assert "an unknown sender" in tools.texts_latest_code({}, request=ask({"code": "7788", "age_s": 0})[0])


def test_no_code_is_said_plainly_and_a_connected_but_stumbling_gmail_is_mentioned():
    out = tools.texts_latest_code({}, request=ask({"connected": True, "why": "", "code": None})[0])
    assert out.startswith("No recent code") and "last five minutes" in out
    out = tools.texts_latest_code({}, request=ask({"connected": True, "why": "Gmail didn't answer just now", "code": None})[0])
    assert out.startswith("No recent code") and "Gmail didn't answer just now" in out


def test_texts_that_cannot_be_read_say_why():
    out = tools.texts_latest_code({}, request=ask({"connected": False, "why": "not connected: press Connect Google for Skippy in Admin", "code": None})[0])
    assert out.startswith("His texts can't be read yet") and "Connect Google for Skippy" in out


def test_a_portal_that_refuses_is_an_error_in_words(monkeypatch):
    with pytest.raises(ValueError, match="answered 401"):
        tools.texts_latest_code({}, request=ask({}, status=401)[0])
    monkeypatch.setenv("PURPLE_TOOLS_ONLY", "texts_latest_code")        # (only this tool: the pop-up registry is not asked)
    monkeypatch.setitem(tools.TOOLS["texts_latest_code"], "run", lambda args: tools.texts_latest_code(args, request=ask({}, status=502)[0]))
    text, err = tools.call("texts_latest_code", {})
    assert err is True and "answered 502" in text


def test_it_is_offered_by_the_house_mcp_and_takes_no_arguments():
    import house_mcp
    tool = tools.TOOLS["texts_latest_code"]
    assert tool["input_schema"]["properties"] == {} and "verification code" in tool["description"]
    listed = {t["name"] for t in house_mcp.handle({"id": 1, "method": "tools/list"}, lambda: None)["result"]["tools"]}
    assert {"texts_latest_code", "bilby_favorite", "vision_flash", "vision_moment", "spotify_account", "home_control"} <= listed
