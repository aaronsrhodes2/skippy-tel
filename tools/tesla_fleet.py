#!/usr/bin/env python3
"""Tesla Fleet API onboarding for the Captain's app (docs/tesla.md, phase 2). Standard library only. Run on skippy-pc, where the vault is.

    python tools/tesla_fleet.py register   # partner token, register makerportal.live as the app's domain, then read back the key Tesla saw
    python tools/tesla_fleet.py check      # read back only: is makerportal.live registered, and does Tesla hold OUR public key?

TESLA_CLIENT_ID and TESLA_SECRET come from the vault (VAULT_ENV, default G:/My Drive/vault/.env) and are never printed. The public key Tesla
must find is portal/tesla-fleet-public-key.pem, served at https://makerportal.live/.well-known/appspecific/com.tesla.3p.public-key.pem; its
private half is on skippy-core only (/etc/skippy-core/tesla-fleet/private.pem).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAULT = os.environ.get("VAULT_ENV", "G:/My Drive/vault/.env")
DOMAIN = os.environ.get("TESLA_FLEET_DOMAIN", "makerportal.live")
AUTH = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
API = os.environ.get("TESLA_FLEET_API", "https://fleet-api.prd.na.vn.cloud.tesla.com")    # North America
SCOPES = "openid vehicle_device_data vehicle_location vehicle_cmds vehicle_charging_cmds"
OUR_KEY = ROOT / "portal" / "tesla-fleet-public-key.pem"


def vault() -> dict:
    out = {}
    for line in Path(VAULT).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    missing = [k for k in ("TESLA_CLIENT_ID", "TESLA_SECRET") if not out.get(k)]
    if missing:
        sys.exit(f"missing in the vault ({VAULT}): {', '.join(missing)}")
    return out


def call(method: str, url: str, *, form: dict | None = None, body: dict | None = None, token: str = "") -> tuple[int, dict]:
    data, headers = None, {"Accept": "application/json"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": e.reason}


def partner_token(v: dict) -> str:
    code, got = call("POST", AUTH, form={"grant_type": "client_credentials", "client_id": v["TESLA_CLIENT_ID"],
                                         "client_secret": v["TESLA_SECRET"], "scope": SCOPES, "audience": API})
    if code != 200 or not got.get("access_token"):
        sys.exit(f"partner token refused: HTTP {code} {got.get('error')} {got.get('error_description', '')}".strip())
    return got["access_token"]


def our_key_hex() -> str:
    """The uncompressed EC point of our public key as hex (what Tesla's public_key endpoint returns), from the PEM's DER."""
    import base64
    der = base64.b64decode("".join(l for l in OUR_KEY.read_text().splitlines() if "-----" not in l))
    return der[-65:].hex()                       # P-256 SubjectPublicKeyInfo ends with the 65-byte 04||X||Y point


def check(token: str) -> bool:
    code, got = call("GET", f"{API}/api/1/partner_accounts/public_key?domain={urllib.parse.quote(DOMAIN)}", token=token)
    held = ((got.get("response") or {}).get("public_key") or "").lower()
    if code != 200 or not held:
        print(f"not registered yet: HTTP {code} {got.get('error') or got}")
        return False
    ok = held == our_key_hex()
    print(f"{DOMAIN} is registered; Tesla holds {'OUR key' if ok else 'a DIFFERENT key (re-run register)'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("register", "check"))
    args = ap.parse_args()
    token = partner_token(vault())
    print("partner token: ok")
    if args.cmd == "register":
        code, got = call("POST", f"{API}/api/1/partner_accounts", body={"domain": DOMAIN}, token=token)
        print(f"register {DOMAIN}: HTTP {code} {got.get('error') or 'ok'}")
        if code != 200:
            return 1
    return 0 if check(token) else 1


if __name__ == "__main__":
    sys.exit(main())
