"""Workshop.hold(): host_ops' maintenance hold. While held, start() refuses with the reason; a hold isn't taken while a run
is going, and the check and the hold are one step, so a run can't slip in between."""
import pytest

import workshop_engine as workshop


@pytest.fixture
def shop(tmp_path):
    return workshop.Workshop(["claude"], repo=str(tmp_path / "SkippyTel-workshop"), primary=str(tmp_path / "SkippyTel"),
                             sessions=str(tmp_path / "sessions.json"))


def test_a_hold_refuses_new_runs_with_its_reason_until_released(shop):
    assert shop.hold("being reset")
    with pytest.raises(workshop.Busy, match="being reset"):
        shop.start("a" * 12, "c" * 12, "hello")
    assert not shop.hold("again")                     # one hold at a time
    shop.release()
    assert shop.hold("again")


def test_no_hold_while_a_run_is_going(shop):
    run = workshop.Run("t" * 12, "c" * 12, "hi", "")
    shop._runs[run.task] = run
    assert not shop.hold("reset")
    run.done = True
    assert shop.hold("reset")
