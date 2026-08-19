"""Ship the newest build to external testers.

Three steps that must all happen and are easy to half-do by hand: attach the
build to every external group, write the "What to Test" notes, and submit it for
Beta App Review. A build that is processed but unattached is invisible to
testers while looking finished in App Store Connect.

`hasAccessToAllBuilds` can only be set when a group is created, never patched,
so attaching is a per-upload step for any group that lacks it.

Usage:
    python3 tools/testflight.py status
    python3 tools/testflight.py ship "What to test text..."
    python3 tools/testflight.py ship --notes-file notes.txt
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

APP = "6797961960"
ROOT = pathlib.Path(__file__).resolve().parent


def asc(method: str, path: str, body=None):
    cmd = [sys.executable, str(ROOT / "asc.py"), method, path]
    if body is not None:
        cmd.append(json.dumps(body))
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"asc.py failed: {out.stderr or out.stdout}")
    status, _, rest = out.stdout.partition("\n")
    return status, (json.loads(rest) if rest.strip() else {})


def newest_build():
    _, d = asc("GET", f"/v1/builds?filter[app]={APP}&limit=1&sort=-version")
    data = d.get("data") or []
    return data[0] if data else None


def external_groups():
    _, d = asc("GET", f"/v1/apps/{APP}/betaGroups")
    return [
        g for g in (d.get("data") or [])
        if not g["attributes"].get("isInternalGroup")
    ]


def review_state(build_id: str):
    _, d = asc("GET", f"/v1/builds/{build_id}/betaAppReviewSubmission")
    return ((d.get("data") or {}).get("attributes") or {}).get("betaReviewState")


def status() -> None:
    _, builds = asc("GET", f"/v1/builds?filter[app]={APP}&limit=5&sort=-version")
    for b in builds.get("data") or []:
        a = b["attributes"]
        print(f"build {a['version']}: {a.get('processingState')} | review {review_state(b['id'])}")
    print()
    for g in external_groups():
        gid, a = g["id"], g["attributes"]
        _, gb = asc("GET", f"/v1/betaGroups/{gid}/builds")
        _, gt = asc("GET", f"/v1/betaGroups/{gid}/betaTesters")
        print(f"{a['name']!r}")
        print(f"   link    : {a.get('publicLink')}")
        print(f"   serving : {[x['attributes']['version'] for x in (gb.get('data') or [])]}")
        print(f"   testers : {len(gt.get('data') or [])}")


def ship(notes: str) -> None:
    build = newest_build()
    if build is None:
        sys.exit("no builds found")
    bid, version = build["id"], build["attributes"]["version"]
    if build["attributes"].get("processingState") != "VALID":
        sys.exit(f"build {version} is {build['attributes'].get('processingState')} — wait and retry")
    print(f"build {version}")

    for g in external_groups():
        s, _ = asc("POST", f"/v1/betaGroups/{g['id']}/relationships/builds",
                   {"data": [{"type": "builds", "id": bid}]})
        print(f"  attach -> {g['attributes']['name']}: {s}")

    _, locs = asc("GET", f"/v1/builds/{bid}/betaBuildLocalizations")
    existing = next(
        (loc for loc in (locs.get("data") or [])
         if loc["attributes"].get("locale") == "en-US"),
        None,
    )
    if existing:
        s, _ = asc("PATCH", f"/v1/betaBuildLocalizations/{existing['id']}",
                   {"data": {"type": "betaBuildLocalizations", "id": existing["id"],
                             "attributes": {"whatsNew": notes}}})
    else:
        s, _ = asc("POST", "/v1/betaBuildLocalizations",
                   {"data": {"type": "betaBuildLocalizations",
                             "attributes": {"locale": "en-US", "whatsNew": notes},
                             "relationships": {"build": {"data": {"type": "builds", "id": bid}}}}})
    print(f"  notes: {s}")

    if review_state(bid):
        print(f"  review: already submitted — {review_state(bid)}")
        return
    s, d = asc("POST", "/v1/betaAppReviewSubmissions",
               {"data": {"type": "betaAppReviewSubmissions",
                         "relationships": {"build": {"data": {"type": "builds", "id": bid}}}}})
    print(f"  review: {s}", d.get("data", {}).get("attributes", {}).get("betaReviewState", ""))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("status", "ship"):
        sys.exit(__doc__)
    if sys.argv[1] == "status":
        status()
    else:
        if len(sys.argv) > 3 and sys.argv[2] == "--notes-file":
            ship(pathlib.Path(sys.argv[3]).read_text())
        elif len(sys.argv) > 2:
            ship(sys.argv[2])
        else:
            sys.exit("ship needs notes text or --notes-file")
