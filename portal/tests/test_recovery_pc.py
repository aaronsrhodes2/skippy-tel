"""The Admin page's skippy-pc card (recovery_web.py `pc`): each button queues one host_op job for the home reporter with an
argument taken from the allow-lists only, waits for its answer and shows it; nothing is sent while home is silent."""
import threading

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import recovery  # noqa: E402
from test_app import ADMIN, login  # noqa: E402
from test_recovery import admin_page, make_app, press  # noqa: E402


class Skippypc:
    """Plays the home reporter: picks up host_op jobs and finishes them with `outcome`, recording what it was sent."""

    def __init__(self, home, outcome=None):
        self.home, self.seen, self.stop = home, [], threading.Event()
        self.outcome = outcome or {"result": {"ok": True, "summary": "video-gate restarted.", "output": "Up 2 seconds"}}
        self.t = threading.Thread(target=self.loop, daemon=True)
        self.t.start()

    def loop(self):
        while not self.stop.is_set():
            for job in self.home.jobs_reply()["jobs"]:
                if job["kind"] == "host_op":
                    self.seen.append({k: job[k] for k in ("op", "arg")})
                    self.home.finish(job["id"], self.outcome)
            self.stop.wait(0.02)


@pytest.fixture
def pc():
    app, client, _ = make_app()
    home = app.extensions["home"]
    fake = Skippypc(home)
    login(client, ADMIN)
    yield app, client, fake
    fake.stop.set()


def test_the_card_lists_the_services_and_the_private_skippys_projects(pc):
    _, client, _ = pc
    page = admin_page(client)
    assert 'id="pc-recovery"' in page
    for s in recovery.PC_SERVICES:
        assert f'<option value="{s}">' in page
    assert '<option value="skippytel">SkippyTel</option>' in page
    for a in ("pc-restart-bridge", "pc-funnel-on", "pc-funnel-off", "pc-funnel-status"):
        assert f"/admin/recovery/{a}" in page


def test_a_restart_sends_one_job_and_shows_skippy_pcs_answer_and_output(pc):
    _, client, fake = pc
    assert press(client, "pc-restart-service", service="video-gate").status_code == 302
    assert fake.seen == [{"op": "restart-service", "arg": "video-gate"}]
    page = admin_page(client)
    assert "video-gate restarted." in page and "Up 2 seconds" in page


@pytest.mark.parametrize("action,fields,expect", [
    ("pc-restart-bridge", {}, {"op": "restart-bridge", "arg": ""}),
    ("pc-reset-workshop", {"project": "skippytel"}, {"op": "reset-workshop", "arg": "skippytel"}),
    ("pc-funnel-on", {}, {"op": "funnel", "arg": "on"}),
    ("pc-funnel-off", {}, {"op": "funnel", "arg": "off"}),
    ("pc-funnel-status", {}, {"op": "funnel", "arg": "status"}),
])
def test_each_button_maps_to_its_operation(pc, action, fields, expect):
    _, client, fake = pc
    press(client, action, **fields)
    assert fake.seen == [expect]


@pytest.mark.parametrize("action,fields", [("pc-restart-service", {"service": "postgres"}),
                                           ("pc-restart-service", {}),
                                           ("pc-reset-workshop", {"project": "../../etc"})])
def test_a_name_off_the_list_sends_nothing(pc, action, fields):
    _, client, fake = pc
    press(client, action, **fields)
    assert fake.seen == [] and "Nothing was sent" in admin_page(client)


def test_a_refusal_from_skippy_pc_is_shown_as_an_error(pc):
    _, client, fake = pc
    fake.outcome = {"result": {"ok": False, "summary": "The private Skippy is in the middle of a run"}}
    press(client, "pc-restart-bridge")
    assert 'class="flash error">The private Skippy is in the middle of a run' in admin_page(client)


def test_nothing_is_sent_while_home_is_silent_and_the_card_says_so(monkeypatch):
    app, client, _ = make_app()
    home = app.extensions["home"]
    monkeypatch.setattr(home, "online", lambda: False)
    login(client, ADMIN)
    assert "isn't reporting, so these buttons can't reach it" in admin_page(client)
    press(client, "pc-funnel-off")
    assert home.jobs_reply()["jobs"] == [] and "isn't reporting" in admin_page(client)


def test_no_answer_in_time_says_sent(monkeypatch):
    app, client, _ = make_app()
    monkeypatch.setattr(recovery, "PC_WAIT_S", 0.05)
    login(client, ADMIN)
    press(client, "pc-funnel-status")
    assert "no answer within" in admin_page(client)


# A friend pressing any of these is a 403: test_recovery.test_only_the_captain_can_press_them walks every ACTIONS entry.


def test_a_slip_or_a_silent_home_doesnt_use_up_the_buttons_rate_limit(pc):
    _, client, fake = pc
    for _ in range(5):
        press(client, "pc-reset-workshop", project="nope")             # the limit is 3 per 5 minutes
    press(client, "pc-reset-workshop", project="skippytel")
    assert fake.seen == [{"op": "reset-workshop", "arg": "skippytel"}]
