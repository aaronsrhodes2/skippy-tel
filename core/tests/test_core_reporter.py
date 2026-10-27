"""core/reporter/reporter.py: what skippy-core tells the portal's Status map, with docker and llama-swap faked."""
import json

from core_testlib import load

rep = load("reporter/reporter.py", "core_reporter")


def fake_run(cmd, timeout=20):
    if cmd[:2] == ["docker", "ps"]:
        return "\n".join(json.dumps(r) for r in (
            {"Names": "skippycore-llm-1", "State": "running", "Status": "Up 3 hours (healthy)", "Image": "llama-swap"},
            {"Names": "skippycore-mentat-1", "State": "restarting", "Status": "Restarting", "Image": "mentat"}, {"bad": 1})) + "\nnot json\n"
    if cmd[:2] == ["docker", "stats"]:
        return json.dumps({"Name": "skippycore-llm-1", "CPUPerc": "12.5%", "MemUsage": "1.5GiB / 62GiB"}) + "\n"
    if cmd[:2] == ["docker", "inspect"]:
        return "/skippycore-llm-1 0 healthy\n/skippycore-mentat-1 6 \n"
    return ""


def test_containers_carry_state_restarts_health_cpu_and_memory():
    got = {c["name"]: c for c in rep.containers(fake_run)}
    llm, mentat = got["skippycore-llm-1"], got["skippycore-mentat-1"]
    assert llm["state"] == "running" and llm["health"] == "healthy" and llm["cpu"] == 12.5 and llm["mem_mb"] == 1536.0
    assert mentat["restarts"] == 6 and mentat["cpu"] is None


def test_models_come_from_llama_swaps_running_list_and_a_dead_swap_is_just_empty():
    ok = rep.models(lambda url, key: {"running": [{"model": "chat", "state": "ready"}, {"model": "coder", "state": "starting"}, "junk"]}, "k")
    assert [(m["name"], m["state"]) for m in ok] == [("chat", "ready"), ("coder", "starting")]

    def down(url, key):
        raise OSError("refused")
    assert rep.models(down, "k") == []


def test_gather_reads_the_deployers_and_doctors_files_and_survives_missing_ones(tmp_path):
    (tmp_path / "deployer").mkdir()
    (tmp_path / "deployer" / "status.json").write_text(json.dumps({"state": "deferred", "commit": "a" * 40, "detail": "holding"}))
    (tmp_path / "deployer" / "doctor.json").write_text(json.dumps({"fails": 3, "last": {"did": "restarted llama-swap"}}))
    got = rep.gather(fake_run, lambda u, k: {"running": []}, tmp_path)
    assert got["deployer"] == {"state": "deferred", "commit": "a" * 10, "detail": "holding"}
    assert got["doctor"]["ok"] is False and got["doctor"]["last"] == "restarted llama-swap"
    empty = rep.gather(lambda c, timeout=20: "", lambda u, k: {}, tmp_path / "nowhere")
    assert empty["containers"] == [] and empty["deployer"]["state"] == "" and empty["doctor"]["ok"] is True


def test_journal_lines_become_records_once_and_the_cursor_moves_on(tmp_path):
    lines = [json.dumps({"MESSAGE": "deploy failed: smoke red", "PRIORITY": "3", "_SYSTEMD_UNIT": "skippy-core-deployer.service", "__CURSOR": "c1"}),
             json.dumps({"MESSAGE": "slow disk", "PRIORITY": "4", "_SYSTEMD_UNIT": "docker.service", "__CURSOR": "c2"}), "junk", json.dumps({"nomessage": 1})]
    seen = []

    def run(cmd, timeout=20):
        seen.append(cmd)
        return "\n".join(lines) if "--since" in cmd or "--after-cursor" in cmd and "journal-units" in str(cmd) else ""
    rows = rep.journal_records(lambda cmd, timeout=20: "\n".join(lines) if "-p" in cmd else "", tmp_path)
    assert [(r["source"], r["level"], r["kind"]) for r in rows] == [("journal/skippy-core-deployer", "error", "error"), ("journal/docker", "warn", "warn")]
    assert (tmp_path / "journal-units.cursor").read_text() == "c2"
    cmds = []
    rep.journal_records(lambda cmd, timeout=20: cmds.append(cmd) or "", tmp_path)
    assert any("--after-cursor" in c and "c2" in c for c in cmds)                          # the next round starts where this one ended


def test_ship_logs_sends_a_crash_and_survives_the_portal_being_away(tmp_path):
    sent = []
    calls = {"up": False}

    def post(rows):
        if calls["up"]:
            sent.extend(rows)
        return calls["up"]

    def run(cmd, timeout=20):
        if cmd[:2] == ["docker", "logs"] and "--tail" in cmd:
            return "boom\nfatal: gone"
        return ""
    rep.ship_logs("https://x", "t", [{"name": "m", "state": "running", "restarts": 0}], run, tmp_path, post)
    rep.ship_logs("https://x", "t", [{"name": "m", "state": "running", "restarts": 1}], run, tmp_path, post)       # a restart, portal still away
    assert sent == []
    calls["up"] = True
    rep.ship_logs("https://x", "t", [{"name": "m", "state": "running", "restarts": 1}], run, tmp_path, post)
    assert sent and sent[0]["kind"] == "crash" and sent[0]["source"] == "docker/m"
