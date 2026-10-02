"""tools/domains.py: which zone a host belongs to, and how a Cloud Run mapping's records land in that zone."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domains  # noqa: E402


def mapping(host, records):
    return {"metadata": {"name": host}, "status": {"resourceRecords": records}}


def test_every_host_belongs_to_one_of_the_domains_and_the_canonical_host_comes_first():
    assert domains.HOSTS[0] == "makerportal.live"
    for host in domains.HOSTS:
        assert domains.domain_of(host) in domains.DOMAINS
    assert domains.zone_name("makerportal.live") == "makerportal-live"


def test_an_apex_mapping_merges_its_a_and_aaaa_records_into_one_set_each():
    m = mapping("makerportal.live", [
        {"type": "A", "rrdata": "216.239.32.21"}, {"type": "A", "rrdata": "216.239.34.21"},
        {"type": "AAAA", "rrdata": "2001:4860:4802:32::15"}])
    got = {(n, t): d for n, t, d in domains.records_wanted(m)}
    assert got == {("makerportal.live.", "A"): ["216.239.32.21", "216.239.34.21"],
                   ("makerportal.live.", "AAAA"): ["2001:4860:4802:32::15"]}


def test_a_www_mapping_becomes_a_cname_on_the_www_name():
    m = mapping("www.makerportal.studio", [{"name": "www", "type": "CNAME", "rrdata": "ghs.googlehosted.com."}])
    assert domains.records_wanted(m) == [("www.makerportal.studio.", "CNAME", ["ghs.googlehosted.com."])]


def test_a_mapping_without_records_yet_asks_for_nothing():
    assert domains.records_wanted({"metadata": {"name": "makerportal.live"}, "status": {}}) == []
