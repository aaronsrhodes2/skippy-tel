"""The face's emotion codes for a line are kept (esphome_link._model_emotion): a filler is said while the real answer is being written on the
same model, so its codes came back after the 8 s limit and were lost (12 timeouts on 2026-11-01). Now the late answer is kept for next time."""
import asyncio
import threading
from types import SimpleNamespace as NS

import esphome_link


def link(create):
    seen, logs = [], []
    brain = NS(model="skippy-chat", client=NS(messages=NS(create=create)))
    me = NS(room=NS(brain=brain, name="theater"), asked="shuffle bilby", keys={"emote": 1}, set_text=lambda k, v: seen.append(v),
            others=lambda: [], log=logs.append)
    return me, seen, logs


def answer(text):
    return NS(content=[NS(text=text)])


def test_a_late_answer_is_kept_and_the_next_time_is_instant(monkeypatch):
    esphome_link.EMOTION_CACHE.clear()
    release, calls = threading.Event(), []

    def slow(**kw):
        calls.append(1)
        release.wait(5)
        return answer("mb")
    me, seen, logs = link(slow)
    line = "Bear with me. I am translating from genius to monkey."

    async def run():
        real_wait = asyncio.wait_for

        async def short(aw, timeout):                           # the 8 s limit, made short for the test
            return await real_wait(aw, 0.05)
        monkeypatch.setattr(esphome_link.asyncio, "wait_for", short)
        await esphome_link.EsphomeLink._model_emotion(me, line, 2.0)
        assert seen == [] and not any("TimeoutError" in m for m in logs)      # too late this time: the word rules stay, no error line
        release.set()
        for _ in range(100):                                     # the answer still lands, in the background
            if esphome_link.EMOTION_CACHE:
                break
            await asyncio.sleep(0.02)
        await esphome_link.EsphomeLink._model_emotion(me, line, 2.0)
    asyncio.run(run())
    assert len(calls) == 1 and len(seen) == 1                    # the second time: from the cache, no model call
