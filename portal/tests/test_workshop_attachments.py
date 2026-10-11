"""Pictures in the private Skippy's chat, the portal's half: the page's JPEG data URLs are checked (type, first bytes, size, count)
and travel in the job to skippy-pc; they are never kept in the portal's own task record."""
import base64

import pytest

pytest.importorskip("flask")

import home as home_mod  # noqa: E402
import workshop as workshop_mod  # noqa: E402
from test_workshop import ADMIN, CONV, Clock, FakeVm  # noqa: E402

JPG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 30).decode()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30).decode()


@pytest.fixture
def shop():
    clock = Clock()
    home = home_mod.Home(FakeVm(), clock=clock)
    home.accept({"capabilities": {"workshop": {"ready": True, "model": "claude-sonnet-5-5"}}})
    return workshop_mod.Workshop(home, clock=clock), home, clock


def test_good_pictures_go_in_the_job_and_not_in_the_task_record(shop):
    ws, home, _ = shop
    task = ws.send(ADMIN, CONV, "see this", images=[f"data:image/jpeg;base64,{JPG}", f"data:image/png;base64,{PNG}"])
    [job] = home.jobs_reply()["jobs"]
    assert job["images"] == [{"mime": "image/jpeg", "b64": JPG}, {"mime": "image/png", "b64": PNG}]
    assert JPG not in repr(ws._tasks[task])


def test_a_picture_alone_is_enough_to_send_but_nothing_at_all_is_not(shop):
    ws, home, _ = shop
    ws.send(ADMIN, CONV, "", images=[f"data:image/jpeg;base64,{JPG}"])
    assert len(home.jobs_reply()["jobs"]) == 1
    with pytest.raises(ValueError):
        ws.send(ADMIN, CONV, "   ", images=[])


def test_everything_that_is_not_a_small_real_picture_is_refused():
    bad = [["data:image/svg+xml;base64," + JPG], ["data:image/jpeg;base64," + PNG], ["data:image/jpeg;base64,!!!"], ["nope"],
           [f"data:image/png;base64,{PNG}"] * 5, ["data:image/jpeg;base64," + "A" * (workshop_mod.MAX_IMAGE_B64 + 4)],
           {"not": "a list"}, [1], [f"data:image/jpeg;base64,{PNG}"]]
    for images in bad:
        with pytest.raises(ValueError):
            workshop_mod.clean_images(images)
    assert workshop_mod.clean_images([]) == [] and workshop_mod.clean_images(None) == []


def test_the_pictures_together_have_a_limit_too():
    big = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 800_000).decode()
    with pytest.raises(ValueError):
        workshop_mod.clean_images([big] * 4)
