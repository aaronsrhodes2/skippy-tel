"""core/ready.py: the readiness contract every service's /health follows (PLATINUM section 24)."""
from core_testlib import load

ready = load("ready.py", "core_ready")


def test_an_idle_service_says_ready_accepting_and_no_jobs():
    now = [1000.0]
    r = ready.Readiness("mentat", clock=lambda: now[0])
    now[0] += 90
    rep = r.report()
    assert rep["spec"] == 1 and rep["service"] == "mentat" and rep["ready"] and rep["accepting"] and rep["detail"] == ""
    assert rep["jobs"] == {"running": 0, "queued": 0, "oldest_s": None} and rep["stuck_s"] == 0 and rep["uptime_s"] == 90
    assert ready.status_code(rep) == 200


def test_jobs_are_counted_aged_and_a_job_with_no_progress_is_stuck_not_just_busy():
    now = [1000.0]
    r = ready.Readiness("tts", clock=lambda: now[0])
    with r.job("sentence") as token:
        now[0] += 30
        rep = r.report()
        assert rep["jobs"]["running"] == 1 and rep["jobs"]["oldest_s"] == 30.0 and rep["stuck_s"] == 30      # nothing finished or moved in 30 s
        r.progress()
        assert r.report()["stuck_s"] == 0                                                                    # it got somewhere: not hung
        now[0] += 200
        assert r.report()["stuck_s"] == 200 and token == 1
    assert r.report()["jobs"]["running"] == 0 and r.report()["stuck_s"] == 0                                 # done: idle again


def test_a_job_that_raises_still_ends():
    r = ready.Readiness("x")
    try:
        with r.job():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert r.report()["jobs"]["running"] == 0


def test_not_ready_says_why_and_returns_503_and_a_dependency_that_is_down_makes_it_not_ready():
    r = ready.Readiness("mentat")
    rep = r.report(ready=False, detail="the router model isn't loaded")
    assert rep["ready"] is False and rep["accepting"] is False and rep["detail"] == "the router model isn't loaded" and ready.status_code(rep) == 503
    dep = r.report(deps={"llm": {"ready": False, "detail": "skippy-core is away"}, "data": {"ready": True}})
    assert dep["ready"] is False and dep["detail"] == "waiting on llm (skippy-core is away)" and dep["deps"]["llm"]["detail"] == "skippy-core is away"
    full = r.report(ready=True, accepting=False)                                                              # ready but refusing new work (draining)
    assert full["ready"] is True and full["accepting"] is False and ready.status_code(full) == 200


def test_queued_work_is_reported_and_extras_ride_along():
    r = ready.Readiness("image-studio")
    r.queued(3)
    rep = r.report(model="sdxl", loaded=False)
    assert rep["jobs"]["queued"] == 3 and rep["model"] == "sdxl" and rep["loaded"] is False
    r.queued(-5)
    assert r.report()["jobs"]["queued"] == 0
