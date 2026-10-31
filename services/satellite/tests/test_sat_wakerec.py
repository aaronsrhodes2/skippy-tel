"""wakerec: a prompt per clip, the clip is what the pod streamed around his phrase, every 5th held out, numbering resumes."""
import asyncio
import wave

from wakerec import BLOCK_MS, Recorder, plan, prompt_text

LOUD, QUIET = [0.2] * 800, [0.0005] * 800


def test_positive_plan_alternates_the_two_phrasings_and_cycles_distances():
    p = plan("positive", 6)
    assert [w for w, _ in p] == ["Skippy", "Hey Skippy"] * 3
    assert p[0][1] == p[1][1] and p[0][1] != p[2][1]
    assert prompt_text("positive", "Hey Skippy", "close and normal") == 'Say "Hey Skippy", close and normal.'


def test_negative_plan_covers_his_own_name_and_the_echo_word():
    said = [w for w, _ in plan("negative", 30)]
    assert "Skipper" in said and "Computer" in said and len(set(said)) == 30


def run_recorder(tmp_path, count, speech_each=True):
    spoken, logs = [], []
    holder = {}

    async def speak(text):
        spoken.append(text)
        rec = holder["rec"]

        async def voice():                                      # the Captain answers 0.7 s after the prompt: quiet, 6 loud blocks, quiet
            await asyncio.sleep(0.7)
            for block in [QUIET] * 4 + [LOUD if speech_each else QUIET] * 6 + [QUIET] * 20:
                rec.feed(block)
                await asyncio.sleep(0.001)
        asyncio.ensure_future(voice())

    async def go():
        rec = holder["rec"] = Recorder("positive", count, tmp_path, speak, log=logs.append, window_s=1.5)
        return rec, await rec.run()
    rec, done = asyncio.run(go())
    return rec, done, spoken, logs


def test_each_prompt_gets_a_clip_with_the_audio_from_before_the_voice(tmp_path):
    rec, done, spoken, _ = run_recorder(tmp_path, 2)
    assert done == 2 and len(spoken) == 3 and spoken[-1].startswith("That is all 2")
    clips = sorted((tmp_path / "positive").glob("*.wav"))
    assert len(clips) == 2
    with wave.open(str(clips[0])) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert w.getnframes() > 6 * 800 + 3 * 800                 # the speech plus lead-in and the pause that ended it
    assert (tmp_path / "index.jsonl").read_text().count("\n") == 2


def test_every_fifth_clip_is_held_out_and_numbering_resumes(tmp_path):
    (tmp_path / "positive").mkdir()
    for i in range(3):
        (tmp_path / "positive" / f"old{i}.wav").write_bytes(b"")
    rec, done, _, _ = run_recorder(tmp_path, 2)                   # clips number 3 and 4: the fifth overall is held out
    assert len(list((tmp_path / "test" / "positive").glob("*.wav"))) == 1
    assert rec.existing() == 5


def test_silence_is_skipped_and_said(tmp_path):
    rec, done, spoken, logs = run_recorder(tmp_path, 1, speech_each=False)
    assert done == 0 and "heard no phrase" in "".join(logs) and list((tmp_path / "skipped").glob("*.wav")) and "microphone" in spoken[-1]
