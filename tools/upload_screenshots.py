"""Upload screenshots to an App Store version through the API.

Dragging files into App Store Connect validates against the slot they land in
and reports only "the dimensions are wrong", without saying which sizes it
would accept. Doing it here removes the guessing: the display type is stated
explicitly and the file is checked before it is sent.

Apple's upload is a four-step handshake, not a POST:

    1. create a screenshot set for the display type
    2. reserve a screenshot, which returns signed upload operations
    3. PUT the bytes to each operation, with the headers Apple supplies
    4. PATCH uploaded=true with an MD5 of the file, which Apple verifies

Skipping step four leaves the asset in limbo — present in the API, invisible in
the UI, and blocking submission with no error to read.

    python3 tools/upload_screenshots.py ~/Downloads/appstore-ready/*.png
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from asc import BASE, token  # noqa: E402

APP = "6797961960"
# The 6.5 inch slot, which is what 1284x2778 belongs to.
DISPLAY_TYPE = "APP_IPHONE_65"
EXPECTED = (1284, 2778)


def api(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}\n{e.read().decode()[:900]}")


def dimensions(path: pathlib.Path) -> tuple[int, int]:
    """Width and height from the PNG header, without a dependency."""
    data = path.read_bytes()[:33]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit(f"{path.name} is not a PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def localization_id() -> str:
    versions = api("GET", f"/v1/apps/{APP}/appStoreVersions?limit=1")
    version = versions["data"][0]["id"]
    locs = api("GET", f"/v1/appStoreVersions/{version}/appStoreVersionLocalizations")
    for loc in locs["data"]:
        if loc["attributes"]["locale"] == "en-US":
            return loc["id"]
    sys.exit("no en-US localization on this version")


def screenshot_set(loc_id: str) -> str:
    existing = api("GET", f"/v1/appStoreVersionLocalizations/{loc_id}/appScreenshotSets")
    for s in existing.get("data", []):
        if s["attributes"]["screenshotDisplayType"] == DISPLAY_TYPE:
            return s["id"]
    created = api("POST", "/v1/appScreenshotSets", {
        "data": {
            "type": "appScreenshotSets",
            "attributes": {"screenshotDisplayType": DISPLAY_TYPE},
            "relationships": {
                "appStoreVersionLocalization": {
                    "data": {"type": "appStoreVersionLocalizations", "id": loc_id}
                }
            },
        }
    })
    return created["data"]["id"]


def upload(path: pathlib.Path, set_id: str) -> str:
    size = path.stat().st_size
    reserved = api("POST", "/v1/appScreenshots", {
        "data": {
            "type": "appScreenshots",
            "attributes": {"fileSize": size, "fileName": path.name},
            "relationships": {
                "appScreenshotSet": {"data": {"type": "appScreenshotSets", "id": set_id}}
            },
        }
    })
    shot_id = reserved["data"]["id"]
    blob = path.read_bytes()

    for op in reserved["data"]["attributes"]["uploadOperations"]:
        chunk = blob[op["offset"]: op["offset"] + op["length"]]
        req = urllib.request.Request(op["url"], method=op["method"], data=chunk)
        for h in op["requestHeaders"]:
            req.add_header(h["name"], h["value"])
        with urllib.request.urlopen(req) as r:
            if r.status not in (200, 201):
                sys.exit(f"upload of {path.name} returned {r.status}")

    # Apple verifies this against what it received. Without it the asset stays
    # in limbo: present in the API, invisible in the UI, blocking submission.
    api("PATCH", f"/v1/appScreenshots/{shot_id}", {
        "data": {
            "type": "appScreenshots",
            "id": shot_id,
            "attributes": {
                "uploaded": True,
                "sourceFileChecksum": hashlib.md5(blob).hexdigest(),
            },
        }
    })
    return shot_id


if __name__ == "__main__":
    files = sorted(pathlib.Path(a).expanduser() for a in sys.argv[1:])
    if not files:
        sys.exit(__doc__)

    for f in files:
        if not f.exists():
            sys.exit(f"missing: {f}")
        got = dimensions(f)
        if got != EXPECTED:
            sys.exit(
                f"{f.name} is {got[0]}x{got[1]}, and the {DISPLAY_TYPE} slot takes "
                f"{EXPECTED[0]}x{EXPECTED[1]}. Run tools/appstore_screenshots.py first."
            )

    loc = localization_id()
    target = screenshot_set(loc)
    print(f"set {target} ({DISPLAY_TYPE})")
    for f in files:
        print(f"  {f.name} ... ", end="", flush=True)
        upload(f, target)
        print("uploaded")
