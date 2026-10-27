"""core/reporter/reporter.py mentat_usage: how Mentat is used, read from its own research log, with the tool list's size asked about once an hour."""
import json

from core_testlib import load

rep = load("reporter/reporter.py", "core_reporter_usage")


def write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")


def test_plain_questions_and_named_tools_are_counted_apart(tmp_path):
    write(tmp_path / "2026-10.jsonl", [{"endpoint": "/ask"}] * 5 + [{"endpoint": "/tools/solar_system_body"}] * 2 + [{"endpoint": "/tools/range"}])
    rep._usage_cache.update(sig=None, usage={}, schema_at=0.0, schema=None)
    got = rep.mentat_usage(tmp_path, post=lambda url, body: {"result": {"tools": [{"name": "a"}, {"name": "b"}]}})
    assert got["asks"] == 5 and got["direct"] == 3 and got["tools_called"] == 2 and got["since"] == "2026-10"
    assert got["tools_total"] == 2 and got["schema_tokens"] > 0


def test_no_log_is_no_numbers_and_an_unreachable_mentat_only_loses_the_schema_size(tmp_path):
    assert rep.mentat_usage(tmp_path / "nowhere") == {}
    write(tmp_path / "2026-10.jsonl", [{"endpoint": "/ask"}])
    rep._usage_cache.update(sig=None, usage={}, schema_at=0.0, schema=None)

    def down(url, body):
        raise OSError("refused")
    got = rep.mentat_usage(tmp_path, post=down)
    assert got["asks"] == 1 and "schema_tokens" not in got
