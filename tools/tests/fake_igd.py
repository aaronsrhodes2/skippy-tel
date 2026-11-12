"""A stand-in router for the UPnP tests (not a test file): it answers SSDP, serves a MiniUPnPd-style description and speaks the Internet
Gateway Device SOAP actions the tools use, with the quirks that matter (718 over another client's forward, 725 for a timed lease on a
permanent-only router, lease 0 refused by routers that want a timed one, no lookup at all). It is a `upnp_port.Net`, so the library runs
against it with no network; `serve()` also puts it behind a real HTTP server on 127.0.0.1 to exercise the real transport."""
import html
import re
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import upnp_port

DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><specVersion><major>1</major><minor>0</minor></specVersion>
<device><deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType><friendlyName>TP-LINK Router</friendlyName>
<serviceList><service><serviceType>urn:schemas-upnp-org:service:Layer3Forwarding:1</serviceType><serviceId>urn:upnp-org:serviceId:L3Forwarding1</serviceId>
<controlURL>/obatk/ctl/L3F</controlURL></service></serviceList>
<deviceList><device><deviceType>urn:schemas-upnp-org:device:WANDevice:1</deviceType>
<deviceList><device><deviceType>urn:schemas-upnp-org:device:WANConnectionDevice:1</deviceType>
<serviceList>
<service><serviceType>urn:schemas-upnp-org:service:WANPPPConnection:1</serviceType><serviceId>urn:upnp-org:serviceId:WANPPPConn1</serviceId><controlURL>/obatk/ctl/PPPConn</controlURL></service>
<service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><serviceId>urn:upnp-org:serviceId:WANIPConn1</serviceId><controlURL>%(control)s</controlURL></service>
</serviceList></device></deviceList></device></deviceList></device></root>"""

FAULT = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
         '<s:Fault><faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
         '<errorCode>%d</errorCode><errorDescription>%s</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>')
OK = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
      '<u:%(action)sResponse xmlns:u="%(stype)s">%(values)s</u:%(action)sResponse></s:Body></s:Envelope>')


class FakeRouter(upnp_port.Net):
    def __init__(self, lan_ip="192.168.0.164", router_ip="192.168.0.1", external_ip="203.0.113.7", location=None, **quirks):
        self.lan_ip, self.router_ip, self.external = lan_ip, router_ip, external_ip
        self.location = location or f"http://{router_ip}:1900/obatk/rootDesc.xml"
        self.control_path = "/obatk/ctl/IPConn"
        self.mappings: dict[tuple[int, str], dict] = {}         # (external port, proto) -> {client, port, enabled, desc, lease}
        self.calls: list[tuple[str, dict]] = []                 # every SOAP action the library sent, with its arguments
        self.ssdp_binds: list = []
        self.down = False                                       # nothing answers (connection refused)
        self.answers_on: set | None = None                      # SSDP answers only on these interfaces (None: all)
        self.ssdp_text = None                                   # override the SSDP reply
        self.description = DESCRIPTION
        # quirks
        self.permanent_only = False                             # a timed lease is refused with 725
        self.refuse_permanent = None                            # lease 0 is refused with this error number (402, 501 ...)
        self.conflict_on_other_client = True                    # an add over another client's forward is 718 (MiniUPnPd does)
        self.lookup_works = True                                # GetSpecificPortMappingEntry answers; False: 501 for every lookup
        self.inject_before_add = None                           # (client, external port): another client takes the port just before our add arrives
        self.forbidden = {}                                     # external port -> error number the router answers an add with
        self.lies_after_add = False                             # the add says yes and the forward never appears
        self.rewrites_client = None                             # the router stores another client than the one asked for
        self.external_text = None
        self.__dict__.update(quirks)

    # the other side of the wire ─────────────────────────────────────────────────────────────────────────────────────────
    def put(self, ext, proto="TCP", client=None, port=None, desc="x", lease=0, enabled=True):
        self.mappings[(ext, proto)] = {"client": client or self.lan_ip, "port": port or ext, "enabled": enabled, "desc": desc, "lease": lease}

    def tick(self, seconds):
        """Time passes: timed leases run down, and a forward whose lease ended is gone."""
        for key in list(self.mappings):
            m = self.mappings[key]
            if m["lease"]:
                m["lease"] -= seconds
                if m["lease"] <= 0:
                    del self.mappings[key]

    def actions(self, *names):
        return [c for c in self.calls if c[0] in names]

    # upnp_port.Net ─────────────────────────────────────────────────────────────────────────────────────────────────────
    def ssdp(self, bind_ip, timeout):
        self.ssdp_binds.append(bind_ip)
        if self.answers_on is not None and bind_ip not in self.answers_on:
            return []
        text = self.ssdp_text or ("HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=120\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
                                  "USN: uuid:1234::urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\nEXT:\r\nSERVER: TP-LINK/TP-LINK UPnP/1.1 MiniUPnPd/2.2.2\r\n"
                                  f"LOCATION: {self.location}\r\n\r\n")
        return [(self.router_ip, text)]

    def get(self, url, timeout):
        if self.down or url != self.location:
            raise ConnectionRefusedError(f"cannot get {url}")
        return self.description % {"control": self.control_path}

    def post(self, url, headers, body, timeout):
        if self.down:
            raise ConnectionRefusedError("connection refused")
        if not url.endswith(self.control_path):
            return 404, "not here"
        return self.handle(headers.get("SOAPAction", ""), body)

    def lan_ip_toward(self, host):
        return self.lan_ip

    def local_ips(self):
        return [self.lan_ip, "172.20.0.1"]

    # the router ────────────────────────────────────────────────────────────────────────────────────────────────────────
    def handle(self, soap_action, body):
        root = ET.fromstring(body)                                # the library's envelope must be well-formed XML
        action_el = root[0][0]
        action = action_el.tag.split("}")[-1]
        stype = action_el.tag[1:].split("}")[0] if action_el.tag.startswith("{") else ""
        assert soap_action == f'"{stype}#{action}"', (soap_action, stype, action)
        args = {c.tag.split("}")[-1]: (c.text or "") for c in action_el}
        self.calls.append((action, args))
        fn = getattr(self, "do_" + action, None)
        if fn is None:
            return self.fault(401, "Invalid Action")
        try:
            values = fn(args)
        except Fault as f:
            return self.fault(f.code, f.desc)
        return 200, OK % {"action": action, "stype": stype, "values": "".join(f"<{k}>{html.escape(str(v), quote=False)}</{k}>" for k, v in values.items())}

    @staticmethod
    def fault(code, desc):
        return 500, FAULT % (code, desc)

    def do_GetExternalIPAddress(self, a):
        return {"NewExternalIPAddress": self.external if self.external_text is None else self.external_text}

    def _entry(self, key):
        m = self.mappings[key]
        return {"NewInternalPort": m["port"], "NewInternalClient": m["client"], "NewEnabled": 1 if m["enabled"] else 0,
                "NewPortMappingDescription": m["desc"], "NewLeaseDuration": m["lease"]}

    def do_GetSpecificPortMappingEntry(self, a):
        if not self.lookup_works:
            raise Fault(501, "Action Failed")
        key = (int(a["NewExternalPort"]), a["NewProtocol"])
        if key not in self.mappings:
            raise Fault(714, "NoSuchEntryInArray")
        return self._entry(key)

    def do_GetGenericPortMappingEntry(self, a):
        keys = sorted(self.mappings)
        i = int(a["NewPortMappingIndex"])
        if i >= len(keys):
            raise Fault(713, "SpecifiedArrayIndexInvalid")
        ext, proto = keys[i]
        return {"NewRemoteHost": "", "NewExternalPort": ext, "NewProtocol": proto, **self._entry(keys[i])}

    def do_AddPortMapping(self, a):
        ext, proto, lease = int(a["NewExternalPort"]), a["NewProtocol"], int(a["NewLeaseDuration"])
        if self.inject_before_add:
            client, port = self.inject_before_add
            self.inject_before_add = None
            self.put(port, client=client, port=port, desc="snuck in")
        if ext in self.forbidden:
            raise Fault(self.forbidden[ext], "Action not authorized" if self.forbidden[ext] == 606 else "Error")
        if lease and self.permanent_only:
            raise Fault(725, "OnlyPermanentLeasesSupported")
        if not lease and self.refuse_permanent:
            raise Fault(self.refuse_permanent, "Invalid Args" if self.refuse_permanent == 402 else "Action Failed")
        old = self.mappings.get((ext, proto))
        if old and old["client"] != a["NewInternalClient"] and self.conflict_on_other_client:
            raise Fault(718, "ConflictInMappingEntry")
        if self.lies_after_add:
            return {}
        self.mappings[(ext, proto)] = {"client": self.rewrites_client or a["NewInternalClient"], "port": int(a["NewInternalPort"]),
                                       "enabled": a["NewEnabled"] == "1", "desc": a["NewPortMappingDescription"], "lease": lease}
        return {}

    def do_DeletePortMapping(self, a):
        key = (int(a["NewExternalPort"]), a["NewProtocol"])
        if key not in self.mappings:
            raise Fault(714, "NoSuchEntryInArray")
        del self.mappings[key]
        return {}

    # the same router behind a real HTTP server on 127.0.0.1 ───────────────────────────────────────────────────────────────
    def serve(self):
        router = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/obatk/rootDesc.xml":
                    body = (router.description % {"control": router.control_path}).encode()
                    self.send_response(200)
                else:
                    body = b"no"
                    self.send_response(404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path != router.control_path:
                    status, text = 404, "not here"
                else:
                    status, text = router.handle(self.headers.get("SOAPAction", ""), body)
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", 'text/xml; charset="utf-8"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.location = f"http://127.0.0.1:{httpd.server_address[1]}/obatk/rootDesc.xml"
        return httpd


class Fault(Exception):
    def __init__(self, code, desc):
        self.code, self.desc = code, desc


def mapping_summary(router, ext=443, proto="TCP"):
    m = router.mappings.get((ext, proto))
    return None if m is None else (m["client"], m["port"], m["lease"])


def strip_ns(text):
    return re.sub(r"\s+xmlns(:\w+)?=\"[^\"]*\"", "", text)
