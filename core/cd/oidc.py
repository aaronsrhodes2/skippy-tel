#!/usr/bin/env python3
"""skippy-core's keyless sign-in to Google Cloud (docs/no-github.md, phase 3): the box signs a short-lived OIDC token with its own
key, and Google swaps it for skippy-deployer's credentials. The project forbids service-account keys; nothing here is one.

    oidc.py keygen    make the key pair once (/etc/skippy-core/gcp-oidc/signing.pem, root-only) and print the public JWKS for
                      infra/bootstrap/skippy-core-jwks.json (the only half Google ever sees)
    oidc.py token     sign a fresh token (an hour) into TOKEN_FILE, and write the credential config gcloud and Terraform read
                      (CRED_FILE: Google's "external_account" form, file-sourced, impersonating skippy-deployer)

core/cd/cd.py calls `token` before every deploy; containers mount both files read-only and use
`gcloud auth login --cred-file=$CRED_FILE` or GOOGLE_APPLICATION_CREDENTIALS. Standard library and the openssl command only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

KEY = Path(os.environ.get("OIDC_KEY", "/etc/skippy-core/gcp-oidc/signing.pem"))
TOKEN_FILE = Path(os.environ.get("OIDC_TOKEN_FILE", "/run/skippy-core-oidc/token.jwt"))
CRED_FILE = Path(os.environ.get("OIDC_CRED_FILE", "/run/skippy-core-oidc/credentials.json"))
ISSUER = "https://skippy-core.skippy-tel.invalid"          # infra/bootstrap/main.tf: never fetched, the keys are uploaded
SUBJECT = "skippy-core-deployer"
PROVIDER = "projects/1015050363686/locations/global/workloadIdentityPools/github/providers/skippy-core"
DEPLOYER = "skippy-deployer@skippy-tel.iam.gserviceaccount.com"
LIFETIME_S = 3600


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def modulus(key: Path = KEY) -> bytes:
    out = subprocess.run(["openssl", "rsa", "-in", str(key), "-noout", "-modulus"], capture_output=True, text=True, check=True).stdout
    return bytes.fromhex(out.strip().split("=", 1)[1])


def kid(n: bytes) -> str:
    return hashlib.sha256(n).hexdigest()[:16]


def jwks(key: Path = KEY) -> dict:
    n = modulus(key)
    return {"keys": [{"kty": "RSA", "alg": "RS256", "use": "sig", "kid": kid(n), "n": b64(n), "e": b64((65537).to_bytes(3, "big"))}]}


def keygen(key: Path = KEY) -> dict:
    if not key.is_file():
        key.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(key.parent, 0o700)
        old = os.umask(0o077)
        try:
            subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], capture_output=True, check=True)
        finally:
            os.umask(old)
    return jwks(key)


def sign(claims: dict, key: Path = KEY, now: float | None = None) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": kid(modulus(key))}
    signing_input = f"{b64(json.dumps(header, separators=(',', ':')).encode())}.{b64(json.dumps(claims, separators=(',', ':')).encode())}"
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key)], input=signing_input.encode(), capture_output=True, check=True).stdout
    return f"{signing_input}.{b64(sig)}"


def claims(now: float | None = None) -> dict:
    now = int(now if now is not None else time.time())
    return {"iss": ISSUER, "sub": SUBJECT, "aud": f"//iam.googleapis.com/{PROVIDER}", "iat": now, "exp": now + LIFETIME_S}


def credential_config(token_file: Path = TOKEN_FILE) -> dict:
    return {"type": "external_account", "audience": f"//iam.googleapis.com/{PROVIDER}",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt", "token_url": "https://sts.googleapis.com/v1/token",
            "credential_source": {"file": str(token_file)},
            "service_account_impersonation_url":
                f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{DEPLOYER}:generateAccessToken"}


def token(key: Path = KEY, token_file: Path = TOKEN_FILE, cred_file: Path = CRED_FILE) -> Path:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(token_file.parent, 0o700)
    old = os.umask(0o077)
    try:
        token_file.write_text(sign(claims(), key), encoding="utf-8")
        cred_file.write_text(json.dumps(credential_config(token_file), indent=1), encoding="utf-8")
    finally:
        os.umask(old)
    return cred_file


def main(argv: list[str]) -> int:
    if argv[:1] == ["keygen"]:
        print(json.dumps(keygen(), indent=1))
        return 0
    if argv[:1] == ["token"]:
        print(token())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
