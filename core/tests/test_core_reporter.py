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
