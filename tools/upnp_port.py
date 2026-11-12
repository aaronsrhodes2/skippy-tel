#!/usr/bin/env python3
"""One port forward on the home router, kept by UPnP: the direct front door's external TCP 443 -> this PC's 47443 (docs/direct-door.md).

Stdlib only; it runs on the Windows host (the starter's keeper imports it too), because only the host knows its own LAN address. The router
here is a TP-Link running MiniUPnPd; any Internet Gateway Device that answers SSDP and offers WANIPConnection or WANPPPConnection works.

    python tools/upnp_port.py discover                  the router's description URL (SSDP), read only
    python tools/upnp_port.py list                      the router's external address and every forward it holds, read only
    python tools/upnp_port.py ip                        the router's external address, read only (says when it is a private one: no door can open then)
    python tools/upnp_port.py ensure [--dry-run]        add 443 -> this PC:47443 if missing, replace it if it points elsewhere, leave it alone if right
    python tools/upnp_port.py remove [--dry-run]        delete that forward, only if it is this PC's own

`ensure` and `remove` change the router, so look first with --dry-run (PLATINUM section 5). Defaults: --external 443 --internal 47443 --proto TCP
--desc skippy-edge --lease 0 (0 = until the router forgets it). A router that refuses lease 0 gets 86400 instead (and a router that only takes
permanent leases gets 0 when a timed one is asked for); the output says which. Exit status: 0 when the forward is in place afterwards (remove: when
none of this PC's remains), 1 when not, with the reason; 2 for a bad command line.

The library part takes a `Net` (SSDP, HTTP and the LAN address); tests hand it a fake router, so nothing here needs a real one to be checked.
Only addresses on a private network are ever spoken to: a Location or control URL that points anywhere else is ignored.
"""
from __future__ import annotations

import argparse
import html
import http.client
import ipaddress
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape

SSDP_ADDR = ("239.255.255.250", 1900)
IGD_ST = "urn:schemas-upnp-org:device:InternetGatewayDevice:1"
WAN_SERVICES = ("WANIPConnection", "WANPPPConnection")        # the services that hold port mappings (IP first: it is the common one)
DEFAULT_EXTERNAL, DEFAULT_INTERNAL, DEFAULT_PROTO, DEFAULT_DESC = 443, 47443, "TCP", "skippy-edge"
FALLBACK_LEASE = 86400            # when a router refuses "until the router forgets it"
REFRESH_BELOW = 3600              # a timed lease with less than this left is renewed
SSDP_WINDOW_S = 2.5               # how long one interface listens for the router's answer
HTTP_TIMEOUT_S = 5.0
MAX_LIST = 256

# UPnP error numbers the logic reads (the rest are quoted back as the router said them)
NO_SUCH_ENTRY, INDEX_INVALID, NOT_FOUND = 714, 713, 730
CONFLICT = 718
HINTS = {606: "606 means the router's UPnP does not allow this: some only open ports above 1023, or only for clients on a list; the forward can be made by hand in the router's own page (docs/direct-door.md)",
         402: "402 means the router did not like the arguments: try --lease 3600, or make the forward by hand in the router's own page (docs/direct-door.md)"}
PORT_PROBLEMS = {724: "SamePortValuesRequired", 727: "ExternalPortOnlySupportsWildcard", 728: "NoPortMapsAvailable", 729: "ConflictWithOtherMechanisms"}


class UpnpError(Exception):
    """The router (or the network to it) said no. `code` is the UPnP error number when the router sent one."""

    def __init__(self, message: str, code: int | None = None, description: str = ""):
        super().__init__(message)
        self.code, self.description = code, description


class UpnpUnreachable(UpnpError):
    """Nothing answered at all (the router is off, the address is wrong, the network is down): different from the router saying no."""


# ── the network, behind one small interface so a test can stand a fake router in its place ─────────────────────────────

class Net:
    """What the library needs from the network. SocketNet is the real one; tests have their own."""

    def ssdp(self, bind_ip: str | None, timeout: float) -> list[tuple[str, str]]:
        """Send the IGD M-SEARCH out of the interface with this address (None: the system's choice); [(sender ip, raw reply)]."""
        raise NotImplementedError

    def get(self, url: str, timeout: float) -> str:
        raise NotImplementedError

    def post(self, url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, str]:
        """(status, body) for ANY HTTP answer (a UPnP error comes as a 500 with the reason in the body); OSError when nothing answered."""
        raise NotImplementedError

    def lan_ip_toward(self, host: str) -> str:
        """This machine's address on the interface that reaches `host`; "" when there is none."""
        raise NotImplementedError

    def local_ips(self) -> list[str]:
        """This machine's private IPv4 addresses: the interfaces worth asking, the default route's first."""
        raise NotImplementedError


class SocketNet(Net):
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # a system proxy must never see the router's LAN address

    def ssdp(self, bind_ip, timeout):
        msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: " + IGD_ST + "\r\n\r\n").encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        found: list[tuple[str, str]] = []
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if bind_ip:
                sock.bind((bind_ip, 0))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))
            sock.settimeout(0.4)
            end, resend = time.monotonic() + timeout, 0.0
            while time.monotonic() < end:
                if time.monotonic() >= resend:                       # twice, a second apart: UDP gets lost
                    sock.sendto(msg, SSDP_ADDR)
                    resend = time.monotonic() + 1.0
                try:
                    data, addr = sock.recvfrom(4096)
                except (socket.timeout, ConnectionResetError):          # (Windows reports an earlier "port unreachable" on the next receive)
                    continue
                found.append((addr[0], data.decode("utf-8", "replace")))
        except OSError:
            pass                                                      # this interface can't do multicast: the caller tries the next
        finally:
            sock.close()
        return found

    def get(self, url, timeout):
        with self.opener.open(urllib.request.Request(url, headers={"User-Agent": "skippy-upnp/1"}), timeout=timeout) as r:
            return r.read(1_000_000).decode("utf-8", "replace")

    def post(self, url, headers, body, timeout):
        req = urllib.request.Request(url, data=body, headers={"User-Agent": "skippy-upnp/1", **headers}, method="POST")
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, r.read(1_000_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:                           # a UPnP fault: the body holds the error number
            return e.code, e.read(100_000).decode("utf-8", "replace")

    def lan_ip_toward(self, host):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((host, 1900))                                # UDP: nothing is sent, the system just picks the interface it would use
            ip = sock.getsockname()[0]
            return "" if ip in ("0.0.0.0", "") else ip
        except OSError:
            return ""
        finally:
            sock.close()

    def local_ips(self):
        ips = []
        first = self.lan_ip_toward("8.8.8.8")                         # the default route's interface (no packet is sent)
        if first:
            ips.append(first)
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ips.append(info[4][0])
        except OSError:
            pass
        out = []
        for ip in ips:
            try:
                a = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if is_lan_address(a) and not a.is_loopback and not a.is_link_local and ip not in out:
                out.append(ip)
        return out


# ── finding the router ─────────────────────────────────────────────────────────────────────────────────────────────────

# a home LAN, and nothing else (Python's own is_private also takes the documentation and reserved ranges)
LAN_NETWORKS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8", "fc00::/7", "fe80::/10", "::1/128")]


def is_lan_address(addr) -> bool:
    return any(addr.version == n.version and addr in n for n in LAN_NETWORKS)


def is_lan_url(url: str) -> bool:
    """http(s) to an address on a home network (an IP literal, or a name that resolves only to such addresses). Nothing else is ever spoken to."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        return is_lan_address(ipaddress.ip_address(parts.hostname))
    except ValueError:
        pass
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(parts.hostname, None)}
    except OSError:
        return False
    return bool(addrs) and all(is_lan_address(ipaddress.ip_address(a.split("%")[0])) for a in addrs)


def parse_ssdp(text: str) -> dict:
    """A raw M-SEARCH reply as {header: value} (names lower-cased)."""
    out = {}
    for line in text.replace("\r", "").split("\n")[1:]:
        name, sep, value = line.partition(":")
        if sep:
            out[name.strip().lower()] = value.strip()
    return out


def parse_description(xml: str, location: str) -> tuple[str, str] | None:
    """(service type, absolute control URL) of the router's WAN connection service, from its device description; None if it has none.
    WANIPConnection is preferred over WANPPPConnection, and a newer version over an older."""
    base = (re.search(r"<URLBase>\s*([^<\s]+)\s*</URLBase>", xml) or [None, location])[1]
    found = []
    for blob in re.findall(r"<service>(.*?)</service>", xml, re.S):
        stype = re.search(r"<serviceType>\s*([^<\s]+)\s*</serviceType>", blob)
        ctl = re.search(r"<controlURL>\s*([^<\s]+)\s*</controlURL>", blob)
        if not stype or not ctl:
            continue
        for rank, name in enumerate(WAN_SERVICES):
            if f":service:{name}:" in stype.group(1):
                version = re.search(r":(\d+)$", stype.group(1))
                found.append((rank, -(int(version.group(1)) if version else 0), stype.group(1), urljoin(base, ctl.group(1))))
    if not found:
        return None
    _, _, stype, control = min(found)
    return stype, control


@dataclass
class Gateway:
    location: str
    control_url: str
    service_type: str
    server: str = ""
    prefers_timed: bool = False             # set once the router has refused a permanent lease

    @property
    def host(self) -> str:
        return urlsplit(self.location).hostname or ""


def gateway_from(net: Net, location: str, server: str = "") -> Gateway:
    """Read the description at `location` and make the Gateway; UpnpError says why not."""
    if not is_lan_url(location):
        raise UpnpError(f"{location} is not an address on a private network: ignored")
    try:
        xml = net.get(location, HTTP_TIMEOUT_S)
    except (OSError, http.client.HTTPException) as e:
        raise UpnpUnreachable(f"could not read the router's description at {location}: {e}") from e
    got = parse_description(xml, location)
    if not got:
        raise UpnpError(f"the device at {location} has no WANIPConnection or WANPPPConnection service (not a router that opens ports)")
    stype, control = got
    if not is_lan_url(control):
        raise UpnpError(f"the control URL {control} is not an address on a private network: ignored")
    return Gateway(location, control, stype, server)


def discover(net: Net, timeout: float = SSDP_WINDOW_S, location: str = "") -> tuple[list[Gateway], list[str]]:
    """(gateways, notes). `location` skips SSDP (a router whose multicast is off, or a known address). Otherwise each interface is asked in turn
    (the default route's first, then the others, then the system's choice) and the search stops at the first that gets an answer."""
    notes: list[str] = []
    if location:
        try:
            return [gateway_from(net, location)], notes
        except UpnpError as e:
            return [], [str(e)]
    gateways: list[Gateway] = []
    seen: set[str] = set()
    for bind in [*net.local_ips(), None]:
        replies = net.ssdp(bind, timeout)
        for sender, text in replies:
            head = parse_ssdp(text)
            loc = head.get("location", "")
            if not loc or loc in seen:
                continue
            kind = head.get("st", "") + head.get("usn", "")
            if kind and "InternetGatewayDevice" not in kind:
                seen.add(loc)
                continue
            if sender and is_lan_url(loc) and urlsplit(loc).hostname != sender:           # a router describes ITSELF: another device on the LAN must not be able to name one for us
                notes.append(f"a reply from {sender} pointed at {loc}, which is not that device: ignored")     # (not marked seen: the real answer from that address still counts)
                continue
            seen.add(loc)
            try:
                gateways.append(gateway_from(net, loc, head.get("server", "")))
            except UpnpError as e:
                notes.append(str(e))
        if gateways:
            break
    if not gateways and not notes:
        notes.append("no router answered the UPnP search: UPnP may be switched off in the router, or multicast is blocked on this network")
    return gateways, notes


# ── talking SOAP to it ─────────────────────────────────────────────────────────────────────────────────────────────────

def envelope(service_type: str, action: str, args: list[tuple[str, object]]) -> bytes:
    inner = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args)
    return (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{service_type}">{inner}</u:{action}></s:Body></s:Envelope>').encode("utf-8")


def parse_values(text: str) -> dict[str, str]:
    return {k: html.unescape(v) for k, v in re.findall(r"<(New\w+)>([^<]*)</\1>", text)}


def parse_fault(text: str) -> tuple[int | None, str]:
    code = re.search(r"<errorCode>\s*(\d+)\s*</errorCode>", text)
    desc = re.search(r"<errorDescription>\s*([^<]*?)\s*</errorDescription>", text)
    return (int(code.group(1)) if code else None), (html.unescape(desc.group(1)) if desc else "")


def soap(net: Net, gw: Gateway, action: str, args: list[tuple[str, object]] | None = None) -> dict[str, str]:
    headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{gw.service_type}#{action}"'}
    try:
        status, text = net.post(gw.control_url, headers, envelope(gw.service_type, action, args or []), HTTP_TIMEOUT_S)
    except (OSError, http.client.HTTPException) as e:
        raise UpnpUnreachable(f"{action}: the router did not answer ({e})") from e
    if status == 200:
        return parse_values(text)
    code, desc = parse_fault(text)
    what = f"{code} {desc}".strip() if code else f"HTTP {status}"
    raise UpnpError(f"{action}: the router refused ({what})", code=code, description=desc)


@dataclass
class Mapping:
    external_port: int
    protocol: str
    internal_port: int
    internal_client: str
    enabled: bool
    description: str
    lease: int                 # seconds left; 0 = permanent

    def text(self) -> str:
        return f"{self.external_port}/{self.protocol} -> {self.internal_client}:{self.internal_port}"


def mapping_from(values: dict[str, str], external_port: int, protocol: str) -> Mapping:
    def num(key: str, default: int = 0) -> int:
        try:
            return int(values.get(key, default))
        except ValueError:
            return default
    return Mapping(num("NewExternalPort", external_port), values.get("NewProtocol", protocol).upper() or protocol, num("NewInternalPort"),
                   values.get("NewInternalClient", ""), values.get("NewEnabled", "1").strip() not in ("0", "false", "False"),
                   values.get("NewPortMappingDescription", ""), num("NewLeaseDuration"))


def get_mapping(net: Net, gw: Gateway, external: int, proto: str) -> Mapping | None:
    """The forward for this external port, None when the router says there is none. Other trouble raises UpnpError."""
    try:
        values = soap(net, gw, "GetSpecificPortMappingEntry", [("NewRemoteHost", ""), ("NewExternalPort", external), ("NewProtocol", proto)])
    except UpnpError as e:
        if e.code in (NO_SUCH_ENTRY, NOT_FOUND):
            return None
        raise
    return mapping_from(values, external, proto)


def list_mappings(net: Net, gw: Gateway, limit: int = MAX_LIST) -> list[Mapping]:
    out = []
    for i in range(limit):
        try:
            values = soap(net, gw, "GetGenericPortMappingEntry", [("NewPortMappingIndex", i)])
        except UpnpUnreachable:
            raise
        except UpnpError as e:
            if e.code in (INDEX_INVALID, NO_SUCH_ENTRY) or i:          # the end of the list (routers say it in different ways); an empty list at index 0
                break
            raise
        out.append(mapping_from(values, 0, ""))
    return out


def external_ip(net: Net, gw: Gateway) -> str:
    return soap(net, gw, "GetExternalIPAddress").get("NewExternalIPAddress", "").strip()


def external_ip_note(ip: str) -> str:
    """A warning when the router's own outside address is not a public one: then nothing from the internet can reach this PC through it."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return f"the router reports no usable external address ({ip or 'empty'}): is its internet link up?"
    if a in ipaddress.ip_network("100.64.0.0/10"):
        return f"the router's external address {ip} is carrier-grade NAT (100.64.0.0/10): the provider shares one public address, so no port forward here is reachable from outside"
    if is_lan_address(a) or a.is_unspecified:
        return f"the router's external address {ip} is a private one: another router (double NAT) or the provider sits in front, so a forward here is not reachable from outside"
    return ""


def add_mapping(net: Net, gw: Gateway, external: int, internal: int, client: str, proto: str, desc: str, lease: int) -> None:
    soap(net, gw, "AddPortMapping", [("NewRemoteHost", ""), ("NewExternalPort", external), ("NewProtocol", proto), ("NewInternalPort", internal),
                                      ("NewInternalClient", client), ("NewEnabled", 1), ("NewPortMappingDescription", desc), ("NewLeaseDuration", lease)])


def delete_mapping(net: Net, gw: Gateway, external: int, proto: str) -> None:
    soap(net, gw, "DeletePortMapping", [("NewRemoteHost", ""), ("NewExternalPort", external), ("NewProtocol", proto)])


# ── the logic: ensure / remove ───────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Result:
    ok: bool
    action: str                # unchanged | added | replaced | refreshed | removed | absent | left | would-... | failed
    message: str
    mapping: Mapping | None = None
    lease: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.ok and self.action in ("added", "replaced", "refreshed", "removed")

    def lines(self) -> list[str]:
        return [self.message, *[f"note: {n}" for n in self.notes]]


def lease_text(lease: int) -> str:
    return "until the router forgets it" if not lease else f"lease {lease} s"


def lookup(net: Net, gw: Gateway, external: int, proto: str) -> tuple[Mapping | None, str]:
    """(mapping or None, "" | why it could not be read). A router that cannot answer the question is not a failure yet: the add still gets its
    try. A router that does not answer at all (UpnpUnreachable) is: that is raised, so nobody waits out a second timeout."""
    try:
        return get_mapping(net, gw, external, proto), ""
    except UpnpUnreachable:
        raise
    except UpnpError as e:
        return None, str(e)


def is_ours(m: Mapping, lan: str, internal: int) -> bool:
    return m.internal_client == lan and m.internal_port == internal


def ensure(net: Net, gw: Gateway, external: int = DEFAULT_EXTERNAL, internal: int = DEFAULT_INTERNAL, proto: str = DEFAULT_PROTO,
           desc: str = DEFAULT_DESC, lease: int = 0, dry_run: bool = False) -> Result:
    """Make `external/proto -> this PC:internal` exist on the router. Idempotent: a right forward is left untouched (no write at all).
    A forward that points elsewhere is deleted first (routers answer 718 to an add over another client's), the lease falls back as the
    module docstring says, and the result is read back from the router before it says ok."""
    proto = proto.upper()
    lan = net.lan_ip_toward(gw.host)
    if not lan:
        return Result(False, "failed", f"{external}/{proto} is NOT in place: this PC has no address on the network that reaches the router at {gw.host}")
    want = f"{external}/{proto} -> {lan}:{internal}"
    try:
        cur, unread = lookup(net, gw, external, proto)
    except UpnpUnreachable as e:
        return Result(False, "failed", f"{want} is NOT in place: {e}")
    notes: list[str] = [f"the router would not say what it holds on {external}/{proto} ({unread})"] if unread else []
    replacing: Mapping | None = None
    refreshing = False
    if cur is not None and cur.enabled and is_ours(cur, lan, internal):
        if not cur.lease or cur.lease >= REFRESH_BELOW:
            return Result(True, "unchanged", f"{want} is in place ({lease_text(cur.lease)})", cur, cur.lease, notes)
        refreshing = True
    elif cur is not None:
        replacing = cur
    if dry_run:
        if refreshing:
            what, kind = f"renew the lease of {want} ({cur.lease} s left)", "refresh"
        elif replacing is not None:
            what, kind = f"replace {replacing.text()} '{replacing.description}' with {want}", "replace"
        else:
            what, kind = f"add {want}", "add"
        return Result(True, "would-" + kind, f"dry run: would {what}", cur, None, notes)
    if replacing is not None:
        try:
            delete_mapping(net, gw, external, proto)
        except UpnpError as e:
            return Result(False, "failed", f"{want} is NOT in place: {replacing.text()} '{replacing.description}' is in the way and the router would not delete it ({e})", cur, None, notes)
    leases = [FALLBACK_LEASE, 0] if lease == 0 and gw.prefers_timed else [lease, FALLBACK_LEASE if lease == 0 else 0]
    refused: list[tuple[int, UpnpError]] = []
    used: int | None = None
    unseen_cleared = False
    for attempt in leases:
        try:
            add_mapping(net, gw, external, internal, lan, proto, desc, attempt)
            used = attempt
            break
        except UpnpUnreachable as e:
            return Result(False, "failed", f"{want} is NOT in place: {e}", cur, None, notes)
        except UpnpError as e:
            fault = e
        if fault.code == CONFLICT and cur is None and not unseen_cleared:
            # a forward the router did not show us sits on that port (or it would not show us any): clear it once and try this lease again
            unseen_cleared = True
            try:
                delete_mapping(net, gw, external, proto)
                add_mapping(net, gw, external, internal, lan, proto, desc, attempt)
                used = attempt
                break
            except UpnpError as e2:
                fault = e2
        if fault.code == CONFLICT or fault.code in PORT_PROBLEMS:
            name = fault.description or PORT_PROBLEMS.get(fault.code, "ConflictInMappingEntry")
            return Result(False, "failed", f"{want} is NOT in place: {external}/{proto} is taken on the router and it will not hand it over ({fault.code} {name})", cur, None, notes)
        refused.append((attempt, fault))                                   # a lease it did not like (725, 402, 501 ...): the other kind is tried next
    if used is None:
        last = refused[-1][1] if refused else None
        if last is not None and last.code in HINTS:
            notes.append(HINTS[last.code])
        return Result(False, "failed", f"{want} is NOT in place: the router refused every lease it was offered; its last answer: {last}", cur, None, notes)
    for attempt, fault in refused:
        notes.append(f"the router refused {lease_text(attempt)} ({fault.code or 'no code'} {fault.description or str(fault)}), so {lease_text(used)} was used".replace("  ", " "))
    if lease == 0 and used != 0:
        gw.prefers_timed = True
        notes.append("the router will not keep a forward until it forgets it: this one lasts a day, and the starter's keeper renews it (or run ensure again before it ends)")
    if unseen_cleared:
        notes.append(f"something on {external}/{proto} that the router did not list was removed to make room")
    try:
        after, unreadable = lookup(net, gw, external, proto)
    except UpnpUnreachable as e:
        after, unreadable = None, str(e)
    if after is not None and not (after.enabled and is_ours(after, lan, internal)):
        return Result(False, "failed", f"{want} is NOT in place: the router took the request but now lists {after.text()} '{after.description}' instead", after, used, notes)
    if after is None and not unreadable:
        return Result(False, "failed", f"{want} is NOT in place: the router said yes and then lists no such forward", None, used, notes)
    if unreadable:
        notes.append(f"it could not be read back ({unreadable})")
    try:
        warn = external_ip_note(external_ip(net, gw))
    except UpnpError:
        warn = ""
    if warn:
        notes.append(warn)
    if unread or unreadable:
        return Result(True, "ensured", f"{want} was sent to the router ({lease_text(used)}); it cannot be read back to confirm", after, used, notes)
    if replacing is not None or unseen_cleared:
        was = f"{replacing.text()} '{replacing.description}'" if replacing is not None else f"an unlisted forward on {external}/{proto}"
        return Result(True, "replaced", f"replaced {was} with {want} '{desc}' ({lease_text(used)})", after, used, notes)
    if refreshing:
        return Result(True, "refreshed", f"renewed {want} ({lease_text(used)})", after, used, notes)
    return Result(True, "added", f"added {want} '{desc}' ({lease_text(used)})", after, used, notes)


def remove(net: Net, gw: Gateway, external: int = DEFAULT_EXTERNAL, internal: int = DEFAULT_INTERNAL, proto: str = DEFAULT_PROTO,
           dry_run: bool = False) -> Result:
    """Delete `external/proto` from the router, only when it is THIS PC's own forward (to `internal`); anyone else's is left alone."""
    proto = proto.upper()
    lan = net.lan_ip_toward(gw.host)
    try:
        cur, unread = lookup(net, gw, external, proto)
    except UpnpUnreachable as e:
        return Result(False, "failed", f"could not read {external}/{proto} on the router, so nothing was removed ({e})")
    if unread:
        return Result(False, "failed", f"could not read {external}/{proto} on the router, so nothing was removed ({unread})")
    if cur is None:
        return Result(True, "absent", f"{external}/{proto} is not forwarded: nothing to remove")
    if not lan or not is_ours(cur, lan, internal):
        return Result(True, "left", f"{cur.text()} '{cur.description}' is not this PC's forward (this PC is {lan or 'unknown'}, wanted port {internal}): left alone", cur)
    if dry_run:
        return Result(True, "would-remove", f"dry run: would remove {cur.text()} '{cur.description}'", cur)
    try:
        delete_mapping(net, gw, external, proto)
    except UpnpError as e:
        return Result(False, "failed", f"{cur.text()} is still there: the router would not delete it ({e})", cur)
    try:
        after, _ = lookup(net, gw, external, proto)
    except UpnpUnreachable:
        after = None
    if after is not None and is_ours(after, lan, internal):
        return Result(False, "failed", f"{cur.text()} is still there after the delete", after)
    return Result(True, "removed", f"removed {cur.text()} '{cur.description}'")


# ── the keeper: one forward held across rounds, speaking only when something changes ──────────────────────────────────────

class Keeper:
    """What the starter runs every few minutes. It remembers the router between rounds and returns/says a line only when the answer changed:
    the forward was added or replaced or renewed, a failure episode began (once, however long it lasts), or it ended. Never raises."""

    def __init__(self, net: Net | None = None, external: int = DEFAULT_EXTERNAL, internal: int = DEFAULT_INTERNAL, proto: str = DEFAULT_PROTO,
                 desc: str = DEFAULT_DESC, location: str = "", say=None):
        self.net, self.external, self.internal, self.proto, self.desc, self.location = net or SocketNet(), external, internal, proto, desc, location
        self.say = say or (lambda text: print(text, flush=True))
        self.gw: Gateway | None = None
        self.state: str | None = None             # None before the first round, then "ok" or "failed"
        self.last: Result | None = None

    def round(self) -> Result:
        try:
            if self.gw is None:
                found, notes = discover(self.net, location=self.location)
                if not found:
                    raise UpnpError("; ".join(notes) or "no router found")
                self.gw = found[0]
            res = ensure(self.net, self.gw, self.external, self.internal, self.proto, self.desc)
        except Exception as e:                    # noqa: BLE001 — the starter's loop must never see this
            res = Result(False, "failed", f"{self.external}/{self.proto} is NOT in place: {e}" if isinstance(e, UpnpError) else
                         f"{self.external}/{self.proto} is NOT in place: {type(e).__name__}: {e}")
        self.last = res
        if res.ok:
            if self.state == "failed":
                self.say("edge upnp: back in order: " + "; ".join(res.lines()))
            elif res.changed or self.state is None:
                self.say("edge upnp: " + "; ".join(res.lines()))
            self.state = "ok"
        else:
            if self.state != "failed":
                self.say("edge upnp: FAILED: " + "; ".join(res.lines()))
            self.state = "failed"
            self.gw = None                        # look for the router again next round (it may have restarted on another port)
        return res


# ── the command line ─────────────────────────────────────────────────────────────────────────────────────────────────────

def pick_gateway(net: Net, args, out, err) -> Gateway | None:
    found, notes = discover(net, timeout=args.timeout, location=args.location)
    if not found:
        print("no UPnP router found: " + "; ".join(notes), file=err)
        return None
    return found[0]


def cmd_discover(net: Net, args, out, err) -> int:
    found, notes = discover(net, timeout=args.timeout, location=args.location)
    if not found:
        print("no UPnP router found: " + "; ".join(notes), file=err)
        return 1
    for gw in found:
        print(gw.location, file=out)
        print(f"  server: {gw.server or '-'}  service: {gw.service_type.split(':service:')[-1]}  control: {gw.control_url}  this PC: {net.lan_ip_toward(gw.host) or '?'}", file=out)
    for n in notes:
        print(f"  note: {n}", file=out)
    return 0


def cmd_list(net: Net, args, out, err) -> int:
    gw = pick_gateway(net, args, out, err)
    if gw is None:
        return 1
    lan = net.lan_ip_toward(gw.host)
    try:
        ip = external_ip(net, gw)
        rows = list_mappings(net, gw)
    except UpnpError as e:
        print(str(e), file=err)
        return 1
    print(f"router: {gw.location}  this PC: {lan or '?'}  external address: {ip or '?'}", file=out)
    note = external_ip_note(ip)
    if note:
        print(f"note: {note}", file=out)
    print(f"{len(rows)} forward(s):", file=out)
    for m in rows:
        mine = "   <- this PC's" if lan and m.internal_client == lan else ""
        print(f"  {m.protocol:<3} {m.external_port:>5} -> {m.internal_client}:{m.internal_port}  {'on ' if m.enabled else 'OFF'}  {lease_text(m.lease) if m.lease else 'permanent':<14} '{m.description}'{mine}", file=out)
    return 0


def cmd_ip(net: Net, args, out, err) -> int:
    gw = pick_gateway(net, args, out, err)
    if gw is None:
        return 1
    try:
        ip = external_ip(net, gw)
    except UpnpError as e:
        print(str(e), file=err)
        return 1
    print(ip, file=out)
    note = external_ip_note(ip)
    if note:
        print(f"note: {note}", file=err)
    return 0


def cmd_ensure(net: Net, args, out, err) -> int:
    gw = pick_gateway(net, args, out, err)
    if gw is None:
        return 1
    res = ensure(net, gw, args.external, args.internal, args.proto, args.desc, args.lease, args.dry_run)
    for line in res.lines():
        print(line, file=out if res.ok else err)
    return 0 if res.ok else 1


def cmd_remove(net: Net, args, out, err) -> int:
    gw = pick_gateway(net, args, out, err)
    if gw is None:
        return 1
    res = remove(net, gw, args.external, args.internal, args.proto, args.dry_run)
    for line in res.lines():
        print(line, file=out if res.ok else err)
    return 0 if res.ok else 1


def build_parser() -> argparse.ArgumentParser:
    def common(parser: argparse.ArgumentParser, inherit: bool = False) -> None:      # given before or after the command, either works
        keep = argparse.SUPPRESS if inherit else None                                   # (a subcommand must not overwrite what came before it)
        parser.add_argument("--location", default=keep if inherit else "", help="the router's description URL; skips the SSDP search (see: discover)")
        parser.add_argument("--timeout", type=float, default=keep if inherit else SSDP_WINDOW_S,
                            help=f"seconds each interface listens for the router (default {SSDP_WINDOW_S})")

    p = argparse.ArgumentParser(prog="upnp_port.py", description="One port forward on the home router, by UPnP (docs/direct-door.md).")
    common(p)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, text in (("discover", "print the router's description URL (read only)"), ("list", "print the external address and every forward (read only)"),
                       ("ip", "print the router's external address (read only)")):
        common(sub.add_parser(name, help=text), inherit=True)
    for name, text in (("ensure", "make the forward exist (changes the router)"), ("remove", "delete this PC's own forward (changes the router)")):
        s = sub.add_parser(name, help=text)
        common(s, inherit=True)
        s.add_argument("--external", type=int, default=DEFAULT_EXTERNAL, help="external port (default %(default)s)")
        s.add_argument("--internal", type=int, default=DEFAULT_INTERNAL, help="this PC's port (default %(default)s)")
        s.add_argument("--proto", default=DEFAULT_PROTO, choices=["TCP", "UDP", "tcp", "udp"], help="protocol (default %(default)s)")
        s.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
        if name == "ensure":
            s.add_argument("--desc", default=DEFAULT_DESC, help="the label the router shows (default %(default)s)")
            s.add_argument("--lease", type=int, default=0, help="seconds; 0 = until the router forgets it, with 86400 as the fallback (default %(default)s)")
    return p


COMMANDS = {"discover": cmd_discover, "list": cmd_list, "ip": cmd_ip, "ensure": cmd_ensure, "remove": cmd_remove}


def main(argv: list[str] | None = None, net: Net | None = None, out=None, err=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](net or SocketNet(), args, out or sys.stdout, err or sys.stderr)
    except UpnpError as e:
        print(str(e), file=err or sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
