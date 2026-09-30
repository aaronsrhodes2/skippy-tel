"""One-click copy for everything people are expected to copy (portal/static/copy.js, loaded by base.html).

The script's behaviour is checked in a real browser (Platinum section 11); these tests pin the contract around it:
what is marked as copyable, that every page loads the script, and that the values worth copying are ONE paste."""
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import chat  # noqa: E402
import friends  # noqa: E402
from test_app import ADMIN, FRIEND, env, login  # noqa: E402,F401  (the fixtures and helpers the app tests use)

STATIC = Path(__file__).resolve().parents[1] / "static"


def running(vm, game):
    vm.state.status, vm.state.games = "RUNNING", [game]


def join_block(client, gid):
    page = client.get(f"/games/{gid}").data.decode()
    join = page[page.index('<div class="join">'):]
    return join[:join.index("</div>")]


def test_the_script_is_served_and_every_kind_of_page_loads_it(env):
    client, vm, _ = env
    script = client.get("/static/copy.js")
    assert script.status_code == 200 and b"code.code, pre.copy" in script.data
    login(client, ADMIN)
    running(vm, "valheim")
    for url in ("/games/valheim", "/games", "/me", "/admin", "/admin/logs"):
        page = client.get(url).data.decode()
        assert page.count('<script src="/static/copy.js" defer></script>') == 1, url


def test_the_script_covers_what_it_promises():
    js = (STATIC / "copy.js").read_text(encoding="utf-8")
    for promise in ('"code.code, pre.copy, pre[data-copy-button]"',     # chips and blocks get a visible Copy button
                    "data-copy",                                        # buttons and links that copy other text
                    "navigator.clipboard", "document.execCommand",      # the API, and the fallback for plain http / old browsers
                    "window.prompt",                                    # the last resort for text that isn't on screen
                    "MutationObserver"):                                # chips that appear later (the join code drops in by itself)
        assert promise in js, promise


def test_no_page_carries_its_own_copy_script_any_more(env):
    """Two copy behaviours on one page would copy twice or fight over the button."""
    client, vm, _ = env
    login(client, ADMIN)
    running(vm, "valheim")
    client.post("/me/pair-home")
    pair = client.post("/me/pair-home").data.decode()
    assert "SKIPPY_HOME_TOKEN=" in pair and 'class="code" id="line"' in pair            # the shared chip, with its own Copy button
    assert "navigator.clipboard" not in pair and 'id="copy"' not in pair                 # the one-off script and button are gone
    for url in ("/games/valheim", "/me", "/admin", "/admin/logs"):
        assert "navigator.clipboard" not in client.get(url).data.decode(), url


def test_a_factorio_address_is_one_paste_with_the_port_in_it(env):
    """The reported 'chuggy copy-paste fest': copy the IP, then hunt down the port and copy that too."""
    client, vm, _ = env
    login(client, ADMIN)
    running(vm, "factorio")
    join = join_block(client, "factorio")
    assert '<code class="code">35.1.2.3:34197</code>' in join
    assert len(re.findall(r'<code class="code">', join)) == 1                            # nothing else to paste
    page = client.get("/games/factorio").data.decode()
    assert "Connect to address" in page


def test_one_box_one_chip(env):
    """The Captain's rule: what goes in ONE box is one chip (`ip:port`, never two copy operations); a dialog with
    separate Address and Port boxes gets one chip per box. Which games have two boxes is checked against the real
    dialog, not the docs: Satisfactory's Server Manager (2026-09-30: 'it's ip and port separate')."""
    from catalog import GAMES  # noqa: PLC0415
    client, vm, _ = env
    login(client, ADMIN)
    two_boxes = {gid for gid, game in GAMES.items() if game.direct_port}
    assert two_boxes == {"satisfactory"}
    for gid, game in GAMES.items():
        running(vm, gid)
        vm.state.env["VALHEIM_CROSSPLAY"] = "false"
        join = join_block(client, gid)
        address = game.direct("35.1.2.3", vm.state.env)
        assert f'<code class="code">{address}</code>' in join
        if gid in two_boxes:
            assert address == "35.1.2.3" and f'<b>Port</b> <code class="code">{game.direct_port}</code>' in join
        else:
            assert re.search(r"^35\.1\.2\.3:\d+$", address), f"{gid}'s one box should get one chip with the port in it: {address}"
            assert "<b>Port</b>" not in join, f"{gid} shows the port as a separate item"
    running(vm, "satisfactory")
    join = join_block(client, "satisfactory")
    assert 'data-copy="Satisfactory: address 35.1.2.3, port 7777"' in join              # one click for the whole message

    vm.state.env["VALHEIM_CROSSPLAY"] = "true"
    vm.serial[2] = "=== skippy-diag t ===\nJOIN_CODE valheim=106969\n=== end ===\n"
    running(vm, "valheim")
    join = join_block(client, "valheim")
    assert 'data-copy="Valheim: join code 106969, password hunter22"' in join           # code and password, one message
    running(vm, "dragonwilds")
    vm.serial[2] = "=== skippy-diag t ===\nJOIN_CODE dragonwilds=YTKM-EKL1\n=== end ===\n"
    assert 'data-copy="RuneScape: Dragonwilds: join code YTKM-EKL1, address 35.1.2.3:7778, password dragonpw"' \
        in join_block(client, "dragonwilds")


def test_the_share_message_follows_the_live_code(env):
    client, vm, _ = env
    login(client, FRIEND)
    running(vm, "dragonwilds")
    page = client.get("/games/dragonwilds").data.decode()
    assert "share.setAttribute(\"data-copy\"" in page and "join code" in page           # the poll keeps the message in step


def test_the_admin_page_offers_the_server_address_and_the_invite_message_to_copy(env):
    client, vm, _ = env
    login(client, ADMIN)
    running(vm, "valheim")
    client.post("/admin/friends", data={"email": "pal@example.com", "portals": ["valheim"]})
    page = client.get("/admin").data.decode()
    assert '<code class="code">35.1.2.3</code>' in page
    button = re.search(r'<button type="button" data-copy="([^"]*)"[^>]*>Copy invite</button>', page)
    assert button, "the friend row has no Copy invite button"
    import html  # noqa: PLC0415
    message = html.unescape(button.group(1))
    assert message.startswith("You're invited: Valheim\n\nHey!") and "pal@example.com" in message
    assert "Go to http" in message and "See you in there," in message                   # the same words the email link carries


def test_the_invite_text_and_the_email_link_say_the_same_thing():
    from urllib.parse import parse_qs, urlparse  # noqa: PLC0415
    subject, body = friends.invite_text("pal@example.com", ["valheim"], "Aaron", "https://portal.example/", {})
    link = urlparse(friends.invite_mailto("pal@example.com", ["valheim"], "Aaron", "https://portal.example/", {}))
    query = parse_qs(link.query)
    assert query["subject"] == [subject] and query["body"] == [body]


def test_logs_are_copied_whole_with_a_button_but_stay_selectable(env):
    client, vm, _ = env
    login(client, ADMIN)
    running(vm, "valheim")
    page = client.get("/admin/logs").data.decode()
    assert page.count("<pre class=\"log\" data-copy-button>") == 2 and 'class="log copy"' not in page


def test_chat_turns_backticked_text_into_chips_and_asks_skippy_to_use_them():
    page = (Path(__file__).resolve().parents[1] / "templates" / "_chat.html").read_text(encoding="utf-8")
    assert "function fill(el, text)" in page and 'chip.className = "code"' in page
    assert "fill(pending," in page and "fill(div," in page                               # both the history and the replies go through it
    prompt = chat.system_prompt("Pal")
    assert "backticks" in prompt and "`106969`" in prompt and "one-click copy" in prompt
