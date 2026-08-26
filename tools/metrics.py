"""Print the product metrics as something readable.

    python3 tools/metrics.py

Reads the database directly over `fly ssh` rather than going through the API,
so it needs no token and adds no authenticated surface. The HTTP route exists
for anything that wants JSON.
"""

import json
import subprocess
import sys

REMOTE = (
    "python -c \""
    "import json;"
    "from app.analytics.product import collect;"
    "from app.db import SessionLocal;"
    "db=SessionLocal();"
    "print('__JSON__'+json.dumps(collect(db).as_dict(),default=str))"
    "\""
)


def fetch() -> dict:
    out = subprocess.run(
        ["fly", "ssh", "console", "-a", "jd-coach-api", "-C", REMOTE],
        capture_output=True, text=True, timeout=180,
    )
    for line in out.stdout.splitlines():
        if line.startswith("__JSON__"):
            return json.loads(line[len("__JSON__"):])
    sys.exit(f"no metrics returned:\n{out.stdout}\n{out.stderr}")


def show(title: str, section: dict) -> None:
    print(f"\n\033[1m{title}\033[0m")
    for k, v in section.items():
        if isinstance(v, dict):
            inner = ", ".join(f"{a}={b}" for a, b in v.items()) or "none"
            print(f"  {k:<34} {inner}")
        else:
            print(f"  {k:<34} {'—' if v is None else v}")


if __name__ == "__main__":
    m = fetch()
    print(f"Coach Will — {m['generated_at'][:16].replace('T', ' ')} UTC")
    for key, title in (
        ("users", "PEOPLE"),
        ("retention", "COMING BACK"),
        ("engagement", "USING IT"),
        ("coaching", "THE COACHING"),
        ("sources", "DATA IN"),
        ("money", "MONEY"),
    ):
        show(title, m.get(key, {}))
    print()
