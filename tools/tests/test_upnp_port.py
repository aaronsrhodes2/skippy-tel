"""tools/upnp_port.py against a stand-in router (tools/tests/fake_igd.py): finding the router, the SOAP it speaks, and ensure/remove doing the
right thing in every state the real router can be in: nothing there, the right forward, a stale one pointing at another client, a router that
answers 718 or 725 or refuses a permanent lease, one that cannot be asked, one that is off. Nothing here touches a network but 127.0.0.1."""
import io
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_igd  # noqa: E402
import upnp_port as up  # noqa: E402

LAN = "192.168.0.164"


def connect(router=None, **quirks):
    router = router or fake_igd.FakeRouter(**quirks)
    found, notes = up.discover(router)
    assert found, notes
    return router, found[0]


# ── finding the router ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_discovery_reads_the_ssdp_reply_and_the_description():
    router, gw = connect()
    assert gw.location == "http://192.168.0.1:1900/obatk/rootDesc.xml"
    assert gw.control_url == "http://192.168.0.1:1900/obatk/ctl/IPConn"           # the relative controlURL, made absolute
    assert gw.service_type == "urn:schemas-upnp-org:service:WANIPConnection:1"       # not the WANPPPConnection listed first, not Layer3Forwarding
    assert "MiniUPnPd" in gw.server and gw.host == "192.168.0.1"


def test_discovery_asks_the_default_route_interface_first_and_stops_at_the_first_answer():
    router, _ = connect()
    assert router.ssdp_binds == [LAN]


def test_discovery_goes_on_to_the_next_interface_when_one_hears_nothing():
    router = fake_igd.FakeRouter(answers_on={"172.20.0.1"})
    found, _ = up.discover(router)
    assert found and router.ssdp_binds == [LAN, "172.20.0.1"]
    router = fake_igd.FakeRouter(answers_on={None})                                # only the system's own choice hears it
    found, _ = up.discover(router)
    assert found and router.ssdp_binds == [LAN, "172.20.0.1", None]


def test_a_known_location_skips_the_search_altogether():
    router = fake_igd.FakeRouter()
    found, notes = up.discover(router, location=router.location)
    assert found and router.ssdp_binds == [] and not notes


def test_no_answer_says_what_to_look_at():
    router = fake_igd.FakeRouter(answers_on=set())
    found, notes = up.discover(router)
    assert found == [] and "UPnP may be switched off" in notes[0] and "multicast" in notes[0]


def test_a_reply_that_points_outside_the_lan_is_ignored():
    evil = ("HTTP/1.1 200 OK\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\nSERVER: x\r\nLOCATION: http://203.0.113.9:1900/desc.xml\r\n\r\n")
    router = fake_igd.FakeRouter(ssdp_text=evil)
    found, notes = up.discover(router)
    assert found == [] and "not an address on a private network" in notes[0]


def test_a_reply_that_names_another_device_as_the_router_is_ignored_and_the_real_one_still_wins():
    """Anything on the LAN can answer the search; it must not be able to make the keeper send its SOAP posts to some other private address (review 2026-11-05)."""
    liar = ("HTTP/1.1 200 OK\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\nSERVER: x\r\nLOCATION: http://192.168.0.1:1900/obatk/rootDesc.xml\r\n\r\n")
    router = fake_igd.FakeRouter()
    real = router.ssdp
    router.ssdp = lambda bind, timeout: [("192.168.0.77", liar), *real(bind, timeout)]       # the liar answers first, from 192.168.0.77
    found, notes = up.discover(router)
    assert len(found) == 1 and found[0].host == "192.168.0.1"
    assert not any("192.168.0.77" in n for n in notes if "ignored" not in n)
    alone = fake_igd.FakeRouter()
    alone.ssdp = lambda bind, timeout: [("192.168.0.77", liar)]
    found, notes = up.discover(alone)
    assert found == [] and "192.168.0.77" in notes[0] and "ignored" in notes[0]


def test_a_reply_for_some_other_kind_of_device_is_ignored():
    other = "HTTP/1.1 200 OK\r\nST: urn:schemas-upnp-org:device:MediaServer:1\r\nLOCATION: http://192.168.0.7:8200/desc.xml\r\n\r\n"
    found, _ = up.discover(fake_igd.FakeRouter(ssdp_text=other))
    assert found == []


def test_a_device_without_a_wan_connection_service_is_not_a_router_for_this():
    router = fake_igd.FakeRouter()
    router.description = "<root><device><serviceList><service><serviceType>urn:schemas-upnp-org:service:Layer3Forwarding:1</serviceType><controlURL>/l3</controlURL></service></serviceList></device></root>"
    found, notes = up.discover(router)
    assert found == [] and "no WANIPConnection or WANPPPConnection" in notes[0]


@pytest.mark.parametrize("xml, location, want", [
    ("<root><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><controlURL>/ctl/IP</controlURL></service></root>",
     "http://192.168.0.1:1900/d.xml", ("urn:schemas-upnp-org:service:WANIPConnection:1", "http://192.168.0.1:1900/ctl/IP")),
    ("<root><URLBase>http://192.168.1.1:5000/</URLBase><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:2</serviceType><controlURL>ctl/IP2</controlURL></service></root>",
     "http://192.168.1.1:49152/d.xml", ("urn:schemas-upnp-org:service:WANIPConnection:2", "http://192.168.1.1:5000/ctl/IP2")),
    ("<root><service><serviceType>urn:schemas-upnp-org:service:WANPPPConnection:1</serviceType><controlURL>http://192.168.0.1/ppp</controlURL></service></root>",
     "http://192.168.0.1:80/d.xml", ("urn:schemas-upnp-org:service:WANPPPConnection:1", "http://192.168.0.1/ppp")),
    ("<root><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><controlURL>/a</controlURL></service>"
     "<service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:2</serviceType><controlURL>/b</controlURL></service></root>",
     "http://192.168.0.1/d.xml", ("urn:schemas-upnp-org:service:WANIPConnection:2", "http://192.168.0.1/b")),
    ("<root><service><serviceType>urn:schemas-upnp-org:service:Layer3Forwarding:1</serviceType><controlURL>/l3</controlURL></service></root>", "http://192.168.0.1/d.xml", None),
])
def test_the_description_names_the_service_and_its_control_url(xml, location, want):
    assert up.parse_description(xml, location) == want


@pytest.mark.parametrize("url, lan", [
    ("http://192.168.0.1:1900/x", True), ("http://10.0.0.1/x", True), ("http://172.16.5.5/x", True), ("http://127.0.0.1:9/x", True),
    ("http://203.0.113.9/x", False), ("http://8.8.8.8/x", False), ("ftp://192.168.0.1/x", False), ("http:///x", False), ("not a url", False),
])
def test_only_private_addresses_are_ever_spoken_to(url, lan):
    assert up.is_lan_url(url) is lan


# ── the wire ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_envelope_is_well_formed_and_escapes_what_it_carries():
    body = up.envelope("urn:schemas-upnp-org:service:WANIPConnection:1", "AddPortMapping",
                       [("NewRemoteHost", ""), ("NewPortMappingDescription", "a <b> & \"c\""), ("NewLeaseDuration", 0)])
    root = ET.fromstring(body)
    action = root[0][0]
    assert action.tag == "{urn:schemas-upnp-org:service:WANIPConnection:1}AddPortMapping"
    assert [(c.tag, c.text) for c in action] == [("NewRemoteHost", None), ("NewPortMappingDescription", 'a <b> & "c"'), ("NewLeaseDuration", "0")]


@pytest.mark.parametrize("text, want", [
    ('<UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>718</errorCode><errorDescription>ConflictInMappingEntry</errorDescription></UPnPError>', (718, "ConflictInMappingEntry")),
    ("<errorCode> 725 </errorCode><errorDescription>OnlyPermanentLeasesSupported</errorDescription>", (725, "OnlyPermanentLeasesSupported")),
    ("<errorCode>402</errorCode>", (402, "")),
    ("Internal Server Error", (None, "")),
])
def test_a_fault_gives_its_number_and_words(text, want):
    assert up.parse_fault(text) == want


# ── ensure ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_ensure_adds_the_forward_when_it_is_missing_and_reads_it_back():
    router, gw = connect()
    res = up.ensure(router, gw)
    assert res.ok and res.action == "added" and res.changed
    assert fake_igd.mapping_summary(router) == (LAN, 47443, 0)                    # this PC, the internal port, permanent
    add = router.actions("AddPortMapping")[0][1]
    assert add == {"NewRemoteHost": "", "NewExternalPort": "443", "NewProtocol": "TCP", "NewInternalPort": "47443", "NewInternalClient": LAN,
                   "NewEnabled": "1", "NewPortMappingDescription": "skippy-edge", "NewLeaseDuration": "0"}
    assert "added 443/TCP -> 192.168.0.164:47443 'skippy-edge'" in res.message
    assert [c[0] for c in router.calls][-2:] == ["GetSpecificPortMappingEntry", "GetExternalIPAddress"]              # it looked, added, then looked again


def test_ensure_leaves_the_right_forward_alone_with_no_write_at_all():
    router, gw = connect()
    router.put(443, client=LAN, port=47443, desc="whatever the label is")
    res = up.ensure(router, gw)
    assert res.ok and res.action == "unchanged" and not res.changed and "is in place" in res.message
    assert [c[0] for c in router.calls] == ["GetSpecificPortMappingEntry"]                    # one question, nothing written


def test_ensure_twice_changes_nothing_the_second_time():
    router, gw = connect()
    first, second = up.ensure(router, gw), up.ensure(router, gw)
    assert first.action == "added" and second.action == "unchanged"
    assert len(router.actions("AddPortMapping")) == 1


def test_a_timed_lease_with_plenty_left_is_left_alone_and_a_nearly_finished_one_is_renewed():
    router, gw = connect()
    router.put(443, client=LAN, port=47443, lease=80000)
    assert up.ensure(router, gw).action == "unchanged"
    router.tick(77000)                                                              # 3,000 s left: under the hour
    res = up.ensure(router, gw)
    assert res.action == "refreshed" and res.changed and fake_igd.mapping_summary(router)[0] == LAN
    assert router.actions("DeletePortMapping") == []                                # same client: no delete, just the add that renews it


def test_a_forward_that_points_at_another_client_is_deleted_first_and_replaced():
    router, gw = connect()
    router.put(443, client="192.168.0.50", port=443, desc="old nas")
    res = up.ensure(router, gw)
    assert res.ok and res.action == "replaced"
    assert "replaced 443/TCP -> 192.168.0.50:443 'old nas' with 443/TCP -> 192.168.0.164:47443" in res.message
    assert [c[0] for c in router.calls if c[0] in ("DeletePortMapping", "AddPortMapping")] == ["DeletePortMapping", "AddPortMapping"]   # a naive add would be 718
    assert fake_igd.mapping_summary(router) == (LAN, 47443, 0)


def test_a_forward_to_another_port_on_this_pc_is_replaced_too():
    router, gw = connect()
    router.put(443, client=LAN, port=8443)
    res = up.ensure(router, gw)
    assert res.action == "replaced" and fake_igd.mapping_summary(router) == (LAN, 47443, 0)


def test_a_disabled_forward_is_put_back_in_force():
    router, gw = connect()
    router.put(443, client=LAN, port=47443, enabled=False)
    assert up.ensure(router, gw).action == "replaced"
    assert router.mappings[(443, "TCP")]["enabled"] is True


def test_a_conflict_over_a_forward_that_appeared_after_the_look_clears_it_once_and_goes_on():
    router, gw = connect(inject_before_add=("192.168.0.50", 443))                     # someone takes the port between our look and our add
    res = up.ensure(router, gw)
    assert res.ok and res.action == "replaced" and "replaced an unlisted forward on 443/TCP" in res.message
    assert fake_igd.mapping_summary(router) == (LAN, 47443, 0)
    assert [c[0] for c in router.calls if c[0] in ("AddPortMapping", "DeletePortMapping")] == ["AddPortMapping", "DeletePortMapping", "AddPortMapping"]


def test_a_conflict_that_will_not_go_away_fails_and_says_why():
    router, gw = connect()
    router.put(443, client="192.168.0.50", port=443)
    router.do_DeletePortMapping = lambda a: {}                                       # the router "deletes" and keeps the forward
    res = up.ensure(router, gw)
    assert not res.ok and res.action == "failed" and "718" in res.message and "taken on the router" in res.message
    assert fake_igd.mapping_summary(router)[0] == "192.168.0.50"                     # and nothing of ours is claimed to be in place


def test_a_router_that_forbids_the_port_says_so():
    router, gw = connect(forbidden={443: 606})
    res = up.ensure(router, gw)
    assert not res.ok and "refused every lease" in res.message and "606" in res.message


def test_a_timed_lease_on_a_permanent_only_router_is_tried_as_permanent_725():
    router, gw = connect(permanent_only=True)
    res = up.ensure(router, gw, lease=3600)
    assert res.ok and fake_igd.mapping_summary(router)[2] == 0
    assert any("OnlyPermanentLeasesSupported" in n and "lease 3600 s" in n for n in res.notes)
    assert [c[1]["NewLeaseDuration"] for c in router.actions("AddPortMapping")] == ["3600", "0"]


def test_a_router_that_refuses_a_permanent_lease_gets_a_day_instead_and_says_so_and_remembers():
    router, gw = connect(refuse_permanent=402)
    res = up.ensure(router, gw)
    assert res.ok and res.action == "added" and fake_igd.mapping_summary(router)[2] == 86400
    assert any("refused until the router forgets it" in n and "lease 86400 s" in n for n in res.notes) and any("a day" in n for n in res.notes)
    assert gw.prefers_timed
    assert up.ensure(router, gw).action == "unchanged"                               # and the next round does not even try 0 again
    router.tick(83000)                                                              # 3,400 s left
    router.calls.clear()
    res = up.ensure(router, gw)
    assert res.action == "refreshed" and [c[1]["NewLeaseDuration"] for c in router.actions("AddPortMapping")] == ["86400"]


def test_a_router_that_refuses_every_lease_fails_with_its_last_answer():
    router, gw = connect(refuse_permanent=402, permanent_only=True)
    res = up.ensure(router, gw)
    assert not res.ok and "refused every lease" in res.message and "OnlyPermanentLeasesSupported" in res.message
    assert fake_igd.mapping_summary(router) is None


def test_a_router_that_cannot_be_asked_still_gets_the_add_and_it_is_not_counted_as_a_change():
    router, gw = connect(lookup_works=False)
    res = up.ensure(router, gw)
    assert res.ok and res.action == "ensured" and not res.changed and "cannot be read back" in res.message
    assert any("would not say what it holds" in n for n in res.notes)
    assert fake_igd.mapping_summary(router) == (LAN, 47443, 0)
    assert up.ensure(router, gw).action == "ensured"                                 # the same every round: nothing for a keeper to announce


def test_a_router_that_says_yes_and_has_no_such_forward_is_a_failure():
    router, gw = connect(lies_after_add=True)
    res = up.ensure(router, gw)
    assert not res.ok and "said yes and then lists no such forward" in res.message


def test_a_router_that_stores_another_client_is_a_failure():
    router, gw = connect(rewrites_client="192.168.0.99")
    res = up.ensure(router, gw)
    assert not res.ok and "now lists 443/TCP -> 192.168.0.99:47443" in res.message


def test_a_dead_router_is_reported_as_unreachable():
    router, gw = connect()
    router.down = True
    res = up.ensure(router, gw)
    assert not res.ok and "did not answer" in res.message


def test_no_lan_address_toward_the_router_is_a_failure():
    router, gw = connect(lan_ip="")
    res = up.ensure(router, gw)
    assert not res.ok and "no address on the network that reaches the router" in res.message


def test_another_port_and_protocol_and_label_are_honoured():
    router, gw = connect()
    res = up.ensure(router, gw, external=8443, internal=9000, proto="udp", desc="x & y")
    assert res.ok and router.mappings[(8443, "UDP")] == {"client": LAN, "port": 9000, "enabled": True, "desc": "x & y", "lease": 0}


def test_a_public_external_address_adds_no_warning_and_a_private_one_does():
    router, gw = connect()
    assert up.ensure(router, gw).notes == []
    router2, gw2 = connect(external="10.1.2.3")
    assert any("double NAT" in n for n in up.ensure(router2, gw2).notes)
    router3, gw3 = connect(external="100.72.1.1")
    assert any("carrier-grade NAT" in n for n in up.ensure(router3, gw3).notes)


@pytest.mark.parametrize("ip, word", [("203.0.113.7", ""), ("192.168.1.2", "private"), ("100.64.0.9", "carrier-grade"), ("", "no usable external address"), ("0.0.0.0", "private")])
def test_the_external_address_note(ip, word):
    note = up.external_ip_note(ip)
    assert (word in note) if word else note == ""


# ── dry run ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_dry_run_says_what_it_would_do_and_writes_nothing():
    router, gw = connect()
    res = up.ensure(router, gw, dry_run=True)
    assert res.ok and res.action == "would-add" and "would add 443/TCP -> 192.168.0.164:47443" in res.message
    router.put(443, client="192.168.0.50", port=443, desc="old")
    res = up.ensure(router, gw, dry_run=True)
    assert res.action == "would-replace" and "replace 443/TCP -> 192.168.0.50:443 'old'" in res.message
    router.put(443, client=LAN, port=47443, lease=100)
    assert up.ensure(router, gw, dry_run=True).action == "would-refresh"
    router.put(443, client=LAN, port=47443)
    assert up.ensure(router, gw, dry_run=True).action == "unchanged"
    assert router.actions("AddPortMapping", "DeletePortMapping") == []


# ── remove ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_remove_deletes_this_pcs_forward_and_checks_it_is_gone():
    router, gw = connect()
    router.put(443, client=LAN, port=47443)
    res = up.remove(router, gw)
    assert res.ok and res.action == "removed" and res.changed and fake_igd.mapping_summary(router) is None


def test_remove_with_nothing_there_is_fine():
    router, gw = connect()
    res = up.remove(router, gw)
    assert res.ok and res.action == "absent" and router.actions("DeletePortMapping") == []


def test_remove_never_takes_anyone_elses_forward():
    router, gw = connect()
    router.put(443, client="192.168.0.50", port=443, desc="the nas")
    res = up.remove(router, gw)
    assert res.ok and res.action == "left" and "not this PC's forward" in res.message
    router.put(443, client=LAN, port=9999)                                              # this PC, but not the door's port
    assert up.remove(router, gw).action == "left"
    assert router.actions("DeletePortMapping") == []


def test_remove_dry_run_and_a_router_that_will_not_delete():
    router, gw = connect()
    router.put(443, client=LAN, port=47443)
    assert up.remove(router, gw, dry_run=True).action == "would-remove" and fake_igd.mapping_summary(router) is not None
    router.do_DeletePortMapping = lambda a: {}                                          # says yes, keeps it
    res = up.remove(router, gw)
    assert not res.ok and "still there" in res.message


def test_remove_that_cannot_read_the_router_removes_nothing():
    router, gw = connect(lookup_works=False)
    router.put(443, client=LAN, port=47443)
    res = up.remove(router, gw)
    assert not res.ok and "nothing was removed" in res.message and fake_igd.mapping_summary(router) is not None


# ── list / ip ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_list_is_every_forward_and_an_empty_router_is_an_empty_list():
    router, gw = connect()
    assert up.list_mappings(router, gw) == []                                           # 713 at index 0 is "none", not an error
    router.put(443, client=LAN, port=47443, desc="skippy-edge")
    router.put(5000, "UDP", client="192.168.0.9", port=5000, desc="game", lease=500)
    rows = up.list_mappings(router, gw)
    assert [(m.protocol, m.external_port, m.internal_client, m.internal_port, m.description, m.lease) for m in rows] == [
        ("TCP", 443, LAN, 47443, "skippy-edge", 0), ("UDP", 5000, "192.168.0.9", 5000, "game", 500)]
    assert up.external_ip(router, gw) == "203.0.113.7"


# ── the command line ─────────────────────────────────────────────────────────────────────────────────────────────────────

def run(argv, router):
    out, err = io.StringIO(), io.StringIO()
    code = up.main(argv, net=router, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_discover_prints_the_description_url_first():
    code, out, err = run(["discover"], fake_igd.FakeRouter())
    assert code == 0 and out.splitlines()[0] == "http://192.168.0.1:1900/obatk/rootDesc.xml" and "MiniUPnPd" in out and err == ""
    code, out, err = run(["discover"], fake_igd.FakeRouter(answers_on=set()))
    assert code == 1 and out == "" and "no UPnP router found" in err


def test_cli_ensure_exit_codes_and_words():
    router = fake_igd.FakeRouter()
    code, out, err = run(["ensure", "--external", "443", "--internal", "47443"], router)
    assert code == 0 and "added 443/TCP -> 192.168.0.164:47443" in out and err == ""
    code, out, err = run(["ensure"], router)
    assert code == 0 and "is in place" in out                                           # defaults are the door's own numbers
    router2 = fake_igd.FakeRouter(forbidden={443: 606})
    code, out, err = run(["ensure"], router2)
    assert code == 1 and out == "" and "NOT in place" in err and "606" in err           # the reason goes to stderr, exit 1
    code, out, err = run(["ensure"], fake_igd.FakeRouter(answers_on=set()))
    assert code == 1 and "no UPnP router found" in err


def test_cli_dry_run_changes_nothing_and_remove_only_removes_its_own():
    router = fake_igd.FakeRouter()
    code, out, _ = run(["ensure", "--dry-run"], router)
    assert code == 0 and "dry run: would add" in out and router.mappings == {}
    run(["ensure"], router)
    code, out, _ = run(["remove", "--dry-run"], router)
    assert code == 0 and "would remove" in out and router.mappings
    code, out, _ = run(["remove"], router)
    assert code == 0 and "removed" in out and router.mappings == {}
    code, out, _ = run(["remove"], router)
    assert code == 0 and "nothing to remove" in out


def test_cli_list_and_ip():
    router = fake_igd.FakeRouter()
    router.put(443, client=LAN, port=47443, desc="skippy-edge")
    code, out, _ = run(["list"], router)
    assert code == 0 and "external address: 203.0.113.7" in out and "1 forward(s)" in out and "TCP   443 -> 192.168.0.164:47443" in out and "this PC's" in out
    code, out, err = run(["ip"], router)
    assert code == 0 and out.strip() == "203.0.113.7" and err == ""
    code, out, err = run(["ip"], fake_igd.FakeRouter(external="192.168.5.5"))
    assert code == 0 and "192.168.5.5" in out and "double NAT" in err


def test_cli_rejects_a_bad_command_line_with_status_2():
    for argv in (["frobnicate"], ["ensure", "--external", "abc"], ["ensure", "--proto", "icmp"], []):
        with pytest.raises(SystemExit) as stopped:
            up.main(argv, net=fake_igd.FakeRouter(), out=io.StringIO(), err=io.StringIO())
        assert stopped.value.code == 2


def test_cli_location_skips_the_search():
    router = fake_igd.FakeRouter()
    code, out, _ = run(["--location", router.location, "ensure"], router)
    assert code == 0 and router.ssdp_binds == []


# ── the real transport, against the same router behind a real HTTP server on 127.0.0.1 ─────────────────────────────────────

def test_the_real_transport_speaks_to_a_router_over_http():
    router = fake_igd.FakeRouter(lan_ip="127.0.0.1", refuse_permanent=402)
    httpd = router.serve()
    try:
        net = up.SocketNet()
        gw = up.gateway_from(net, router.location)
        assert gw.control_url.endswith("/obatk/ctl/IPConn") and gw.host == "127.0.0.1"
        assert net.lan_ip_toward("127.0.0.1") == "127.0.0.1"
        res = up.ensure(net, gw)                                                        # a 500 fault (402) comes back as a status and body, not an exception
        assert res.ok and res.action == "added" and router.mappings[(443, "TCP")]["lease"] == 86400
        assert up.ensure(net, gw).action == "unchanged"
        assert up.remove(net, gw).action == "removed" and router.mappings == {}
        with pytest.raises(up.UpnpError) as stopped:
            up.get_mapping(net, up.Gateway(router.location, router.location.replace("rootDesc.xml", "ctl/NoSuch"), gw.service_type), 443, "TCP")
        assert isinstance(stopped.value, up.UpnpError)                                  # a 404 from the router is an error with no UPnP number
    finally:
        httpd.shutdown()
    gone = up.SocketNet()
    with pytest.raises(up.UpnpUnreachable):
        up.soap(gone, gw, "GetExternalIPAddress")                                       # nothing listening any more


def test_the_real_transports_local_addresses_are_private_and_not_loopback():
    for ip in up.SocketNet().local_ips():
        assert up.is_lan_url(f"http://{ip}/") and not ip.startswith("127.") and not ip.startswith("169.254.")


# ── the keeper ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def keeper(router, said):
    return up.Keeper(net=router, say=said.append)


def test_the_keeper_says_one_line_when_it_starts_then_nothing_while_all_is_well():
    router, said = fake_igd.FakeRouter(), []
    k = keeper(router, said)
    assert k.round().action == "added" and len(said) == 1 and said[0].startswith("edge upnp: added 443/TCP")
    for _ in range(5):
        k.round()
    assert len(said) == 1 and k.state == "ok"                                           # five quiet rounds, one line
    assert len(router.actions("GetSpecificPortMappingEntry")) == 7 and len(router.actions("AddPortMapping")) == 1       # (the first round also reads it back)


def test_the_keeper_that_finds_the_forward_already_there_says_so_once():
    router, said = fake_igd.FakeRouter(), []
    router.put(443, client=LAN, port=47443)
    k = keeper(router, said)
    k.round(); k.round()
    assert len(said) == 1 and "is in place" in said[0]


def test_the_keeper_announces_a_change_the_router_makes_to_it():
    router, said = fake_igd.FakeRouter(), []
    k = keeper(router, said)
    k.round()
    router.mappings.clear()                                                             # the router rebooted and forgot
    k.round()
    router.put(443, client="192.168.0.77", port=443)                                    # and now someone else took the port
    k.round()
    assert [s.split(":")[1].split()[0] for s in said] == ["added", "added", "replaced"]


def test_a_failure_is_one_line_per_episode_and_the_end_of_it_is_one_more():
    router, said = fake_igd.FakeRouter(forbidden={443: 606}), []
    k = keeper(router, said)
    for _ in range(6):
        k.round()
    assert len(said) == 1 and said[0].startswith("edge upnp: FAILED:") and "606" in said[0] and k.state == "failed"
    router.forbidden.clear()
    k.round(); k.round()
    assert len(said) == 2 and said[1].startswith("edge upnp: back in order: added") and k.state == "ok"
    router.down = True
    k.round(); k.round()
    assert len(said) == 3 and "FAILED" in said[2] and "did not answer" in said[2]            # a new episode is a new line


def test_the_keeper_looks_for_the_router_again_after_a_failure():
    router, said = fake_igd.FakeRouter(), []
    k = keeper(router, said)
    k.round()
    router.down = True
    k.round()
    assert k.gw is None
    router.down = False
    k.round()
    assert k.state == "ok" and router.ssdp_binds.count(LAN) == 2                        # found it by SSDP again


def test_the_keeper_never_raises_whatever_happens():
    class Broken(up.Net):
        def local_ips(self):
            raise RuntimeError("the network stack is gone")
    said = []
    res = up.Keeper(net=Broken(), say=said.append).round()
    assert not res.ok and said and "RuntimeError" in said[0] and "the network stack is gone" in said[0]
    res = up.Keeper(net=fake_igd.FakeRouter(answers_on=set()), say=said.append).round()
    assert not res.ok and "no router answered" in said[-1]


def test_the_keeper_with_a_router_that_cannot_be_asked_is_quiet_after_the_first_line():
    router, said = fake_igd.FakeRouter(lookup_works=False), []
    k = keeper(router, said)
    for _ in range(4):
        k.round()
    assert len(said) == 1 and k.state == "ok"
