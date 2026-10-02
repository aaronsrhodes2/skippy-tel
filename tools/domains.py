"""Hook the portal's own domains up to Cloud Run, end to end and idempotent (run by .github/workflows/domains.yml).

The Captain owns makerportal.live and makerportal.studio (registered at Squarespace, which has no DNS API). Their DNS
lives in Cloud DNS in this project, so the only hand step is pointing the registrar at Google's nameservers. Every run
walks the same ladder and stops at the first rung that isn't ready yet, saying what it's waiting for:

  1. the Cloud DNS and Site Verification APIs are on
  2. each domain has a managed zone        -> prints the nameservers to paste at Squarespace
  3. the zone holds Google's ownership TXT record
  4. the registrar points at those nameservers (checked through public DNS)
  5. Google has verified the deployer as the domain's owner
  6. a Cloud Run domain mapping per host    -> its A/AAAA/CNAME records are written into the zone
  7. the managed certificate is issued      -> https://<host>/version answers

Talks to the REST APIs with one access token (env TOKEN) that carries the cloud-platform and siteverification scopes.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "skippy-tel"
REGION = "us-west1"
SERVICE = "skippy-portal"
DOMAINS = ["makerportal.live", "makerportal.studio"]
# Every host the portal answers on. The portal sends all but the first to https://makerportal.live (portal/app.py).
HOSTS = ["makerportal.live", "www.makerportal.live", "makerportal.studio", "www.makerportal.studio"]

DNS = f"https://dns.googleapis.com/dns/v1/projects/{PROJECT}"
RUN = f"https://{REGION}-run.googleapis.com/apis/domains.cloudrun.com/v1/namespaces/{PROJECT}/domainmappings"
VERIFY = "https://www.googleapis.com/siteVerification/v1"
USAGE = f"https://serviceusage.googleapis.com/v1/projects/{PROJECT}/services"


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body


def call(method: str, url: str, body: dict | None = None, token: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, e.read().decode("utf-8", "replace")) from None
    return json.loads(raw) if raw else {}


def zone_name(domain: str) -> str:
    return domain.replace(".", "-")


def fqdn(name: str) -> str:
    return name if name.endswith(".") else name + "."


def domain_of(host: str) -> str:
    return next(d for d in DOMAINS if host == d or host.endswith("." + d))


def public_ns(domain: str) -> set[str]:
    """The nameservers the world sees for a domain (Google's public resolver), lower-case with the trailing dot."""
    q = urllib.parse.urlencode({"name": domain, "type": "NS"})
    try:
        ans = call("GET", f"https://dns.google/resolve?{q}").get("Answer") or []
    except (ApiError, OSError):
        return set()
    return {fqdn(a["data"].lower()) for a in ans if a.get("type") == 2}


def records_wanted(mapping: dict) -> list[tuple[str, str, list[str]]]:
    """(name, type, rrdatas) the mapping asks for, merged per name+type. Cloud Run gives the A/AAAA set for an apex and
    one CNAME for a subdomain; `name` is relative to the domain ("" or "@" = apex)."""
    host = mapping["metadata"]["name"]
    domain = domain_of(host)
    merged: dict[tuple[str, str], list[str]] = {}
    for r in (mapping.get("status") or {}).get("resourceRecords") or []:
        rel = r.get("name") or ""
        name = domain if rel in ("", "@") else f"{rel}.{domain}"
        if host != domain and rel in ("", "@"):
            name = host
        merged.setdefault((fqdn(name), r["type"]), []).append(r["rrdata"])
    return [(n, t, sorted(set(d))) for (n, t), d in merged.items()]


class Domains:
    def __init__(self, token: str):
        self.token = token
        self.waiting: list[str] = []

    def api(self, method, url, body=None):
        return call(method, url, body, self.token)

    # 1 ─ APIs
    def enable_apis(self):
        for svc in ("dns.googleapis.com", "siteverification.googleapis.com"):
            state = self.api("GET", f"{USAGE}/{svc}").get("state")
            if state != "ENABLED":
                print(f"enabling {svc}")
                self.api("POST", f"{USAGE}/{svc}:enable", {})
        for _ in range(30):
            if all(self.api("GET", f"{USAGE}/{s}").get("state") == "ENABLED"
                   for s in ("dns.googleapis.com", "siteverification.googleapis.com")):
                return
            time.sleep(5)

    # 2 ─ zones
    def zone(self, domain: str) -> dict:
        try:
            return self.api("GET", f"{DNS}/managedZones/{zone_name(domain)}")
        except ApiError as e:
            if e.status != 404:
                raise
        print(f"creating the Cloud DNS zone for {domain}")
        return self.api("POST", f"{DNS}/managedZones", {
            "name": zone_name(domain), "dnsName": fqdn(domain),
            "description": "The Skippy portal's domain (tools/domains.py)", "visibility": "public"})

    def rrsets(self, domain: str) -> dict[tuple[str, str], dict]:
        out = self.api("GET", f"{DNS}/managedZones/{zone_name(domain)}/rrsets")
        return {(r["name"], r["type"]): r for r in out.get("rrsets", [])}

    def put_rrset(self, domain: str, name: str, rtype: str, rrdatas: list[str], ttl: int = 300):
        have = self.rrsets(domain).get((name, rtype))
        want = {"name": name, "type": rtype, "ttl": ttl, "rrdatas": rrdatas}
        if have and sorted(have["rrdatas"]) == sorted(rrdatas):
            return
        change = {"additions": [want], "deletions": [have] if have else []}
        self.api("POST", f"{DNS}/managedZones/{zone_name(domain)}/changes", change)
        print(f"  {rtype:5} {name} -> {', '.join(rrdatas)}")

    # 3 + 5 ─ ownership
    def verified(self, domain: str) -> bool:
        try:
            res = self.api("GET", f"{VERIFY}/webResource/{urllib.parse.quote('dns://' + domain, safe='')}")
            return bool(res.get("owners"))
        except ApiError as e:
            if e.status in (403, 404):
                return False
            raise

    def ownership_record(self, domain: str):
        tok = self.api("POST", f"{VERIFY}/token", {
            "site": {"type": "INET_DOMAIN", "identifier": domain}, "verificationMethod": "DNS_TXT"})["token"]
        have = self.rrsets(domain).get((fqdn(domain), "TXT"))
        datas = set(have["rrdatas"]) if have else set()
        datas.add(f'"{tok}"')
        self.put_rrset(domain, fqdn(domain), "TXT", sorted(datas))

    def verify(self, domain: str) -> bool:
        try:
            self.api("POST", f"{VERIFY}/webResource?verificationMethod=DNS_TXT",
                     {"site": {"type": "INET_DOMAIN", "identifier": domain}})
            return True
        except ApiError as e:
            print(f"  Google couldn't verify {domain} yet: {e}")
            return False

    # 6 ─ mappings
    def mapping(self, host: str) -> dict:
        try:
            return self.api("GET", f"{RUN}/{host}")
        except ApiError as e:
            if e.status != 404:
                raise
        print(f"creating the Cloud Run domain mapping for {host}")
        return self.api("POST", RUN, {
            "apiVersion": "domains.cloudrun.com/v1", "kind": "DomainMapping",
            "metadata": {"name": host, "namespace": PROJECT},
            "spec": {"routeName": SERVICE, "certificateMode": "AUTOMATIC"}})

    def run(self) -> int:
        self.enable_apis()
        ready: list[str] = []
        for domain in DOMAINS:
            print(f"\n== {domain}")
            z = self.zone(domain)
            ns = [fqdn(n.lower()) for n in z.get("nameServers", [])]
            print("  nameservers (Squarespace -> Domains -> DNS -> Nameservers -> custom):")
            for n in ns:
                print(f"    {n.rstrip('.')}")
            if self.verified(domain):
                print("  ownership: verified")
                ready.append(domain)
                continue
            self.ownership_record(domain)
            seen = public_ns(domain)
            # Nothing seen = the change is mid-flight (resolvers drop the old answer before they learn the new one):
            # try anyway, Google's verifier asks the registry itself. Only Squarespace's old servers mean "not yet".
            if seen and not seen <= set(ns):
                self.waiting.append(f"{domain}: the registrar still points at {sorted(seen) or 'nothing'}; "
                                    f"set its nameservers to the four above")
                continue
            if self.verify(domain):
                print("  ownership: verified just now")
                ready.append(domain)
            else:
                self.waiting.append(f"{domain}: nameservers are right; Google hasn't seen the TXT record yet (retry)")

        for host in HOSTS:
            if domain_of(host) not in ready:
                continue
            m = self.mapping(host)
            wanted = records_wanted(m)
            if not wanted:   # a brand-new mapping fills its records in a few seconds
                time.sleep(10)
                m = self.mapping(host)
                wanted = records_wanted(m)
            for name, rtype, datas in wanted:
                self.put_rrset(domain_of(host), name, rtype, datas)
            conds = {c["type"]: c for c in (m.get("status") or {}).get("conditions", [])}
            ok = conds.get("Ready", {}).get("status") == "True"
            cert = conds.get("CertificateProvisioned", {})
            print(f"  {host}: {'READY' if ok else 'not ready'}"
                  + ("" if ok else f" (certificate: {cert.get('status', '?')} {cert.get('message', '')})".rstrip()))
            if not ok:
                self.waiting.append(f"{host}: certificate being issued (usually 15-60 min after the records resolve)")

        print()
        if self.waiting:
            print("WAITING ON:")
            for w in self.waiting:
                print(f"  - {w}")
            return 0
        print("ALL HOSTS READY: https://" + HOSTS[0])
        return 0


def main() -> int:
    token = os.environ.get("TOKEN", "")
    if not token:
        print("TOKEN (an access token with the cloud-platform and siteverification scopes) is required", file=sys.stderr)
        return 2
    return Domains(token).run()


if __name__ == "__main__":
    sys.exit(main())
