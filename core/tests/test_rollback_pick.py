"""core/cd/rollback_pick.py: which Cloud Run revision a rollback serves (cd.py rollback; it replaced the `rollback` workflow).

The cloud steps can't be run from here, but the one decision in them can: given the JSON of
`gcloud run revisions list`, which revision does a blank "Run workflow" go back to? The rule it must keep: one step back from
the revision SERVING NOW, never "the newest other than serving". After a failed deploy rolls itself back the newest revision is
the broken one, and a Captain who taps rollback on a phone must not be sent onto it.
"""
from __future__ import annotations

import json

import pytest

from core_testlib import load

rp = load("cd/rollback_pick.py", "core_rollback_pick")

IMG = "us-west1-docker.pkg.dev/skippy-tel/skippy/portal"


def rev(n: int, created: str | None = None, ready: bool | None = True, sha: str | None = "a" * 40) -> dict:
    """A revision the way `gcloud run revisions list --format=json` prints it (a Knative v1 Revision)."""
    r = {"apiVersion": "serving.knative.dev/v1", "kind": "Revision",
         "metadata": {"name": f"skippy-portal-{n:05d}-abc", "creationTimestamp": created or f"2026-09-{n:02d}T10:00:00.000000Z"},
         "spec": {"containers": [{"image": f"{IMG}:{sha}" if sha else IMG}]},
         "status": {"conditions": [{"type": "Active", "status": "True"}]}}
    if ready is not None:
        r["status"]["conditions"].append({"type": "Ready", "status": "True" if ready else "False"})
    return r


def name(n: int) -> str:
    return f"skippy-portal-{n:05d}-abc"


def listing(*revs):
    return rp.newest_first(list(revs))


def test_blank_steps_back_one_from_what_is_serving():
    revs = listing(*(rev(n) for n in range(1, 6)))
    assert rp.pick(revs, name(5)) == name(4)
    assert rp.pick(revs, name(3)) == name(2)


def test_after_a_failed_deploy_rolled_itself_back_blank_never_returns_to_the_broken_newest():
    """Serving 5 (the old good one, pinned by deploy.yml's rollback); 6 is the broken deploy and is the newest."""
    revs = listing(*(rev(n) for n in range(1, 6)), rev(6, ready=False))
    assert rp.pick(revs, name(5)) == name(4)
    revs = listing(*(rev(n) for n in range(1, 7)))              # even if 6 was Ready, blank means "older than serving"
    assert rp.pick(revs, name(5)) == name(4)


def test_a_revision_that_never_became_ready_is_not_a_place_to_go_back_to():
    revs = listing(rev(1), rev(2), rev(3, ready=False), rev(4, ready=False), rev(5))
    assert rp.pick(revs, name(5)) == name(2)


def test_a_revision_whose_health_the_json_does_not_state_is_still_a_candidate():
    revs = listing(rev(1), rev(2, ready=None), rev(3))
    assert rp.pick(revs, name(3)) == name(2)


def test_the_oldest_has_nothing_to_go_back_to_and_says_so():
    revs = listing(rev(1), rev(2))
    with pytest.raises(rp.Nothing, match="nothing earlier"):
        rp.pick(revs, name(1))
    revs = listing(rev(1, ready=False), rev(2))
    with pytest.raises(rp.Nothing, match="nothing earlier"):
        rp.pick(revs, name(2))


def test_when_it_cannot_tell_what_is_serving_it_refuses_to_guess():
    revs = listing(rev(1), rev(2), rev(3))
    with pytest.raises(rp.Nothing, match="Could not tell which revision is serving"):
        rp.pick(revs, "")
    with pytest.raises(rp.Nothing, match="not in the revision list"):
        rp.pick(revs, "skippy-portal-00099-zzz")


def test_a_named_revision_is_served_whatever_its_age_and_latest_means_go_forward_again():
    revs = listing(*(rev(n) for n in range(1, 6)))
    assert rp.pick(revs, name(5), name(2)) == name(2)
    assert rp.pick(revs, name(2), name(5)) == name(5)           # roll forward by name
    assert rp.pick(revs, name(5), "  latest ") == "latest"
    assert rp.pick(revs, name(5), name(5)) == name(5)           # already serving: the workflow says so and moves nothing


@pytest.mark.parametrize("bad", ["skippy-portal-00002-abc; rm -rf /", "$(id)", "../x", "Skippy-Portal", "x y", "-rf", "a" * 70])
def test_a_typed_revision_that_is_not_a_revision_name_is_refused(bad):
    revs = listing(rev(1), rev(2))
    with pytest.raises(rp.Nothing, match="not a revision name"):
        rp.pick(revs, name(2), bad)


def test_a_typed_revision_that_does_not_exist_is_refused_with_the_way_out():
    revs = listing(rev(1), rev(2))
    with pytest.raises(rp.Nothing, match="No revision called skippy-portal-00077-abc exists.*blank"):
        rp.pick(revs, name(2), "skippy-portal-00077-abc")


def test_age_comes_from_the_timestamp_down_to_the_microsecond_not_from_the_name():
    a = rev(7, "2026-09-30T10:00:00.100000Z")
    b = rev(3, "2026-09-30T10:00:00.900000Z")                   # a lower number, but created a moment later
    c = rev(1, "2026-09-30T09:59:59.999999Z")
    revs = listing(a, b, c)
    assert [r["name"] for r in revs] == [name(3), name(7), name(1)]
    assert rp.pick(revs, name(3)) == name(7)


def test_without_timestamps_the_generation_number_in_the_name_orders_them():
    blank = lambda n: {"metadata": {"name": name(n)}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}  # noqa: E731
    revs = rp.newest_first([blank(2), blank(10), blank(1)])
    assert [r["name"] for r in revs] == [name(10), name(2), name(1)]
    assert rp.pick(revs, name(10)) == name(2)


def test_the_table_lists_five_newest_first_marks_the_serving_one_and_shows_the_commit():
    revs = listing(*(rev(n, sha=f"{n:x}" * 40) for n in range(1, 10)), rev(10, ready=False, sha=None))
    md = rp.table(revs, name(9), limit=5)
    rows = [ln for ln in md.splitlines() if ln.startswith("| ") and "`skippy-portal-" in ln]
    assert [r.split("`")[1] for r in rows] == [name(10), name(9), name(8), name(7), name(6)]
    assert "**serving**" in rows[1] and "**serving**" not in rows[0]
    assert "`9999999`" in rows[1]                               # the 7-character commit each was built from
    assert "| NO |" in rows[0]                                  # the unhealthy one is flagged
    assert "2026-09-09 10:00:00" in rows[1]


def test_the_commit_column_is_blank_for_an_image_that_is_not_a_commit_tag():
    revs = listing(rev(1, sha="latest"), rev(2, sha="sha256:" + "b" * 64))
    assert [r["sha"] for r in revs] == ["", ""]


# ── the command line the workflow runs ───────────────────────────────────────

def run_cli(tmp_path, revisions, *args, capsys):
    f = tmp_path / "revisions.json"
    f.write_text(revisions if isinstance(revisions, str) else json.dumps(revisions), encoding="utf-8")
    summary = tmp_path / "summary.md"
    code = rp.main([f"--revisions={f}", f"--summary={summary}", *args])
    out = capsys.readouterr()
    return code, out.out.strip(), out.err.strip(), (summary.read_text(encoding="utf-8") if summary.exists() else "")


def test_cli_prints_only_the_revision_name_on_stdout_and_writes_the_summary(tmp_path, capsys):
    code, out, err, summary = run_cli(tmp_path, [rev(n) for n in range(1, 4)], f"--serving={name(3)}", "--want=", capsys=capsys)
    assert (code, out, err) == (0, name(2), "")
    assert name(3) in summary and "**serving**" in summary


def test_cli_writes_the_summary_first_so_a_phone_still_sees_the_list_when_nothing_is_safe(tmp_path, capsys):
    code, out, err, summary = run_cli(tmp_path, [rev(1)], f"--serving={name(1)}", "--want=", capsys=capsys)
    assert code == 2 and out == "" and "nothing earlier" in err
    assert name(1) in summary


def test_cli_passes_a_value_that_starts_with_a_dash_through_as_a_value_not_an_option(tmp_path, capsys):
    code, out, err, _ = run_cli(tmp_path, [rev(1), rev(2)], f"--serving={name(2)}", "--want=--help", capsys=capsys)
    assert code == 2 and "not a revision name" in err


def test_cli_survives_garbage_json(tmp_path, capsys):
    code, out, err, _ = run_cli(tmp_path, "{not json", f"--serving={name(2)}", capsys=capsys)
    assert code == 2 and "Could not read the revision list" in err
    code, out, err, _ = run_cli(tmp_path, '{"revisions": []}', f"--serving={name(2)}", capsys=capsys)   # valid JSON, not a list
    assert code == 2 and "Could not tell which revision is serving" not in err and "not in the revision list" in err
