"""Blocking work off the gevent event loop (app/offload.py). On 2026-11-02 one slow SQLite summary on the Windows bind mount froze the
whole worker: /health queued behind it, Docker marked the container unhealthy 45 times and the doctor recreated it three times an
hour. Run on their own: `python -m pytest services/flask-api/tests -q`."""
import threading
import time

import pytest

from app.offload import off_loop


def test_off_loop_returns_what_the_function_returns_and_passes_its_arguments():
    assert off_loop(lambda a, b=0: a + b, 40, b=2) == 42


def test_an_exception_comes_back_out_as_it_went_in():
    def boom():
        raise ValueError("no")
    with pytest.raises(ValueError, match="no"):
        off_loop(boom)


def test_the_work_runs_in_a_real_thread_and_the_loop_keeps_serving_while_it_blocks():
    gevent = pytest.importorskip("gevent")
    seen = {}
    ticks = []

    def ticker():                                         # another request, on the same event loop
        for _ in range(5):
            ticks.append(1)
            gevent.sleep(0.01)

    def blocking():                                       # SQLite on the bind mount: a call that does not yield
        seen["thread"] = threading.get_ident()
        time.sleep(0.3)                                   # the real, blocking sleep
        return "done"

    other = gevent.spawn(ticker)
    assert off_loop(blocking) == "done"
    assert seen["thread"] != threading.get_ident()        # not on the loop's own thread
    assert len(ticks) == 5                                # and the other request got on with it meanwhile (it would be 0 if the loop froze)
    other.join()
