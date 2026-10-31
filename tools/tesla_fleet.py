#!/usr/bin/env python3
"""Tesla Fleet API onboarding for the Captain's app (docs/tesla.md, phase 2). Standard library only. Run on skippy-pc, where the vault is.

    python tools/tesla_fleet.py register   # partner token, register makerportal.live as the app's domain, then read back the key Tesla saw
    python tools/tesla_fleet.py check      # read back only: is makerportal.live registered, and does Tesla hold OUR public key?
    python tools/tesla_fleet.py login-url  # the address the Captain signs in at (the state is kept in STATE_FILE for `exchange`)
    python tools/tesla_fleet.py exchange '<the callback address>'   # the one-time code -> his tokens, stored on skippy-core (root, 600)
    python tools/tesla_fleet.py vehicles   # end to end: his token lists his cars (one Fleet API data request)

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
AUTHORIZE = "https://auth.tesla.com/oauth2/v3/authorize"
REDIRECT = f"https://{DOMAIN}/car/oauth/callback"           # the app's Allowed Redirect URI (portal/car_web.py)
USER_SCOPES = "openid offline_access " + SCOPES.split(" ", 1)[1]
STATE_FILE = Path(os.environ.get("TEMP", "/tmp")) / "tesla-fleet-oauth-state"
TOKENS = "/etc/skippy-core/tesla-fleet/tokens.json"      # on skippy-core, beside the private key


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


def login_url(v: dict) -> str:
    import secrets
    state = secrets.token_urlsafe(24)
    STATE_FILE.write_text(state)
    return AUTHORIZE + "?" + urllib.parse.urlencode({"response_type": "code", "client_id": v["TESLA_CLIENT_ID"], "redirect_uri": REDIRECT,
                                                     "scope": USER_SCOPES, "state": state, "locale": "en-US", "prompt": "login"})


def exchange(v: dict, callback: str) -> int:
    import subprocess
    import time
    q = urllib.parse.parse_qs(urllib.parse.urlparse(callback).query)
    if q.get("error"):
        sys.exit(f"Tesla refused: {q['error'][0]} {q.get('error_description', [''])[0]}")
    want = STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""
    if not want or q.get("state", [""])[0] != want:
        sys.exit("the state doesn't match the last login-url: start again with login-url")
    code, got = call("POST", AUTH, form={"grant_type": "authorization_code", "client_id": v["TESLA_CLIENT_ID"],
                                         "client_secret": v["TESLA_SECRET"], "code": q.get("code", [""])[0], "audience": API,
                                         "redirect_uri": REDIRECT})
    if code != 200 or not got.get("refresh_token"):
        sys.exit(f"token swap refused: HTTP {code} {got.get('error')} {got.get('error_description', '')}".strip())
    keep = {k: got.get(k) for k in ("access_token", "refresh_token", "id_token", "token_type", "scope")}
    keep["expires_at"] = int(time.time()) + int(got.get("expires_in") or 0)
    keep["client_id"] = v["TESLA_CLIENT_ID"]
    r = subprocess.run(["ssh", "skippy-core", f"sudo sh -c 'umask 077; cat > {TOKENS}'"], input=json.dumps(keep).encode(), capture_output=True)
    if r.returncode:
        sys.exit(f"couldn't store the tokens on skippy-core: {r.stderr.decode()[-300:]}")
    STATE_FILE.unlink(missing_ok=True)
    print(f"tokens stored on skippy-core ({TOKENS}); scopes: {got.get('scope')}")
    return 0


def user_token() -> str:
    import subprocess
    r = subprocess.run(["ssh", "skippy-core", f"sudo cat {TOKENS}"], capture_output=True)
    if r.returncode:
        sys.exit("no tokens on skippy-core yet: login-url, then exchange")
    return json.loads(r.stdout)["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("register", "check", "login-url", "exchange", "vehicles"))
    ap.add_argument("callback", nargs="?", default="")
    args = ap.parse_args()
    if args.cmd == "login-url":
        print(login_url(vault()))
        return 0
    if args.cmd == "exchange":
        return exchange(vault(), args.callback)
    if args.cmd == "vehicles":
        code, got = call("GET", f"{API}/api/1/vehicles", token=user_token())
        cars = got.get("response") or []
        print(f"HTTP {code}: " + (", ".join(f"{c.get('display_name')} ({c.get('state')})" for c in cars) or str(got.get("error") or got)))
        return 0 if code == 200 else 1
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
