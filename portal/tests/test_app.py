"""Flask app tests with a fake VM and a fake Claude. Need the real Flask +
Authlib (installed in CI); skipped where they aren't installed."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import chat  # noqa: E402
from gcp import VmStatus  # noqa: E402

ADMIN = "aaron.s.rhodes@gmail.com"
FRIEND = "friend@example.com"


class FakeVm:
    def __init__(self, state="TERMINATED"):
        self.state = VmStatus(status=state, machine_type="e2-standard-4", external_ip="35.1.2.3",
                              games=["valheim"], idle_minutes="30",
                              env={"VALHEIM_PASSWORD": "hunter22", "DISCORD_GUILD_ID": "g1",
                                   "DISCORD_CHANNEL_VALHEIM": "c1", "DISCORD_VOICE_VALHEIM": "v1"})
        self.calls = []

    def status(self):
        return self.state

    def start(self, games, idle_minutes=30):
        self.calls.append(("start", games, idle_minutes))
        self.state.status = "RUNNING"
        return self.state

    def stop(self):
        self.calls.append(("stop",))
        self.state.status = "TERMINATED"
        return self.state

    def restart(self):
        self.calls.append(("restart",))
        return self.state

    def update_env(self, updates):
        self.calls.append(("update_env", updates))
        self.state.env.update(updates)


class FakeSkippy:
    def __init__(self):
        self.seen = None

    def reply(self, system, messages):
        self.seen = (system, messages)
        return "Obviously."


SETTINGS = {
    "SECRET_KEY": "test", "SESSION_COOKIE_SECURE": False, "SESSION_COOKIE_SAMESITE": "Lax",
    "GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret",
    "ALLOWED_EMAILS": frozenset({ADMIN, FRIEND}), "ADMIN_EMAILS": frozenset({ADMIN}),
    "GCP_PROJECT": "p", "GCP_ZONE": "z", "GCP_INSTANCE": "i",
    "CHAT_MODEL": "claude-opus-5", "CHAT_ENABLED": True,
    "DISCORD_INVITE": "gPTVS7Hmj", "DISCORD_BOT_TOKEN": "",
}


@pytest.fixture
def env():
    vm, skippy = FakeVm(), FakeSkippy()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=skippy)
    return app.test_client(), vm, skippy


def login(client, email):
    with client.session_transaction() as s:
        s["user"] = {"email": email, "name": "Captain", "picture": None}


def test_signed_out_sees_login(env):
    client, _, _ = env
    assert b"Sign in with Google" in client.get("/").data
    assert client.get("/dashboard").status_code == 302
    assert client.get("/healthz").data == b"ok"


def test_unlisted_email_is_bounced(env):
    client, _, _ = env
    login(client, "stranger@example.com")
    resp = client.get("/dashboard")
    assert resp.status_code == 302 and "/unauthorized" in resp.location


def test_dashboard_shows_valheim_with_discord_links(env):
    client, _, _ = env
    login(client, FRIEND)
    page = client.get("/dashboard").data.decode()
    assert "Valheim" in page
    assert "https://discord.com/channels/g1/c1" in page
    assert "https://discord.com/channels/g1/v1" in page
    assert "Admin" not in page            # friends don't get the admin link


def test_start_refuses_when_settings_missing(env):
    client, vm, _ = env
    login(client, FRIEND)
    vm.state.env.pop("VALHEIM_PASSWORD")
    client.post("/games/start", data={"games": ["valheim"], "idle_minutes": "45"})
    assert vm.calls == []


def test_start_and_stop(env):
    client, vm, _ = env
    login(client, FRIEND)
    client.post("/games/start", data={"games": ["valheim"], "idle_minutes": "45"})
    assert vm.calls[-1] == ("start", ["valheim"], 45)
    client.post("/games/stop")
    assert vm.calls[-1] == ("stop",)


def test_admin_is_admins_only(env):
    client, _, _ = env
    login(client, FRIEND)
    assert client.get("/admin").status_code == 403
    assert client.post("/admin/restart").status_code == 403
    login(client, ADMIN)
    assert client.get("/admin").status_code == 200


def test_admin_settings_keep_blank_secrets(env):
    client, vm, _ = env
    login(client, ADMIN)
    client.post("/admin/settings", data={"VALHEIM_SERVER_NAME": " Skippy ", "VALHEIM_PASSWORD": ""})
    updates = vm.calls[-1][1]
    assert updates["VALHEIM_SERVER_NAME"] == "Skippy"
    assert "VALHEIM_PASSWORD" not in updates           # blank secret = unchanged
    page = client.get("/admin").data.decode()
    assert "hunter22" not in page                      # secrets never rendered


def test_admin_restart(env):
    client, vm, _ = env
    login(client, ADMIN)
    client.post("/admin/restart")
    assert vm.calls[-1] == ("restart",)


def test_chat_round_trip(env):
    client, _, skippy = env
    login(client, FRIEND)
    resp = client.post("/chat/send", json={"message": "status?",
                                           "history": [{"role": "user", "content": "hi"},
                                                       {"role": "assistant", "content": "yo"}]})
    assert resp.get_json() == {"reply": "Obviously."}
    system, messages = skippy.seen
    assert "Captain" in system
    assert messages[-1] == {"role": "user", "content": "status?"}


def test_chat_errors_are_shown_not_raised(env):
    client, _, skippy = env
    login(client, FRIEND)

    def boom(system, messages):
        raise chat.ChatError("Skippy is rate-limited right now.")
    skippy.reply = boom
    resp = client.post("/chat/send", json={"message": "hi"})
    assert resp.status_code == 502
    assert "rate-limited" in resp.get_json()["error"]
