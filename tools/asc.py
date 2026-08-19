"""Minimal App Store Connect API client.

Mints an ES256 JWT from the .p8 team key and issues one request. Tokens are
short-lived (Apple caps them at 20 minutes) and generated per invocation, so
nothing is cached to disk.

The private key is NOT in this repo and must never be. It lives in
~/.appstoreconnect/private_keys/ and is what actually grants access — the key id
and issuer id below are account identifiers, useless without it.

Usage:
    python3 tools/asc.py GET /v1/apps
    python3 tools/asc.py POST /v1/betaAppReviewSubmissions '{"data": {...}}'

Prints the HTTP status on the first line, then the JSON body. Callers that parse
the output must check that first line — a silent empty result usually means this
script failed to run at all.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

import jwt

KEY_ID = os.environ.get("ASC_KEY_ID", "55WY75228U")
ISSUER_ID = os.environ.get("ASC_ISSUER_ID", "fa01d78f-1d1b-41cd-beb7-cf28af977b45")
KEY_PATH = pathlib.Path(
    os.environ.get(
        "ASC_KEY_PATH",
        pathlib.Path.home() / ".appstoreconnect/private_keys" / f"AuthKey_{KEY_ID}.p8",
    )
)
BASE = "https://api.appstoreconnect.apple.com"


def token() -> str:
    if not KEY_PATH.exists():
        sys.exit(
            f"missing App Store Connect key at {KEY_PATH}\n"
            "Download it from App Store Connect > Users and Access > Integrations."
        )
    return jwt.encode(
        {
            "iss": ISSUER_ID,
            "iat": int(time.time()),
            # Well inside Apple's 20-minute ceiling; a longer expiry is rejected.
            "exp": int(time.time()) + 15 * 60,
            "aud": "appstoreconnect-v1",
        },
        KEY_PATH.read_text(),
        algorithm="ES256",
        headers={"kid": KEY_ID, "typ": "JWT"},
    )


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    method, path = sys.argv[1].upper(), sys.argv[2]
    body = sys.argv[3].encode() if len(sys.argv) > 3 else None

    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=body,
        headers={
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            print(f"HTTP {r.status}")
            payload = r.read().decode()
    except urllib.error.HTTPError as e:
        # Printed rather than raised: the error body names the actual problem,
        # and a bare status code has cost real time on this project.
        print(f"HTTP {e.code}")
        payload = e.read().decode()

    if payload.strip():
        try:
            print(json.dumps(json.loads(payload), indent=2))
        except ValueError:
            print(payload)


if __name__ == "__main__":
    main()
