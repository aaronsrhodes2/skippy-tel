"""The recency-pruned ledger (ledger.py): phrases kept word for word while the work keeps referring to them, dropped (never summarised) when it stops."""
import ledger as lg
import run_state as rs


def test_phrases_and_keys_are_plain_text_matching():
    assert lg.phrases_of("Fixed it. The cache lives in portal/static/bilby-app.js and is rebuilt each time.\nok") == [
        "The cache lives in portal/static/bilby-app.js and is rebuilt each time."]
    k = lg.keys_of("edit portal/static/bilby-app.js, call fav_url, commit 3cd7ff93")
    assert {"portal/static/bilby-app.js", "fav_url", "3cd7ff93"} <= k


def test_a_phrase_the_work_keeps_naming_stays_and_an_untouched_one_goes():
    led = lg.Ledger()
    led.say("The favorite button is wired in portal/static/bilby-app.js near the transport keys.")
    led.say("The old parser in services/legacy/parse_old.py is unused and can be ignored later.")
    for _ in range(lg.STALE_EVENTS + 5):                     # a long stretch of work that only ever names the first file
        led.do("Read", "portal/static/bilby-app.js")
    kept = " ".join(led.carry())
    assert "bilby-app.js near the transport keys" in kept
    assert "parse_old.py" not in kept
    assert any("parse_old.py" in e.text for e in led.dropped())      # dropped from the handoff, still in the ledger (and so in the archive)


def test_young_phrases_are_kept_before_they_had_a_chance_to_be_referred_to():
    led = lg.Ledger()
    led.say("The deploy needs the bridge hand-over before the new guard applies.")
    assert led.carry()


def test_the_carry_fits_its_budget_newest_referenced_first():
    led = lg.Ledger()
    for i in range(200):
        led.say(f"Decision number {i} concerns services/claude-bridge/module_{i}.py and nothing else at all.")
    out = led.carry(budget=1200)
    assert sum(len(x) for x in out) <= 1200 + 40 * len(out) and 0 < len(out) < 200


def test_the_handoff_carries_the_ledger_and_archives_every_phrase(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    j = rs.Journal()
    j.said("The stop gate must re-read the goal in services/claude-bridge/goal.py at every stop.")
    for _ in range(lg.STALE_EVENTS + 30):
        j.did("Read", "services/claude-bridge/goal.py")
    j.said("A stray idea about services/other/unrelated_thing.py that nothing ever mentioned again.")
    for _ in range(lg.STALE_EVENTS + 30):
        j.did("Bash", "python -m pytest services/claude-bridge/goal.py")
    text = rs.handoff("Do it.", 2, str(tmp_path), j)
    assert "Earlier in this job, word for word" in text and "must re-read the goal" in text
    section = text.split("Earlier in this job")[1].split("\n\n")[0]
    assert "unrelated_thing.py" not in section and "left out, not summarised" in section
    archive = (tmp_path / ".purple-out" / "ledger.jsonl").read_text(encoding="utf-8")
    assert "unrelated_thing.py" in archive and "must re-read the goal" in archive
