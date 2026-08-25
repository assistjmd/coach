"""Keep credentials out of the application log.

Uvicorn's access log records the full request line, query string included. The
OAuth callback carries the provider's authorization code and our state token in
exactly that position, so every successful connection wrote two live credentials
into Fly's log stream:

    GET /v1/providers/google_health/callback?state=eyJhbGciOi...&code=4/0AXEQx...

Both are single-use and short-lived, which bounds the damage but does not remove
it — logs outlive the credentials' validity window by a long way, and anyone with
read access to them could replay a code inside it.

This scrubs the query string on any path carrying a secret, leaving the path and
status intact so the log is still useful for debugging.
"""

from __future__ import annotations

import logging
import re

# Query parameters that must never be logged, whatever route they appear on.
_SECRET_PARAMS = ("code", "state", "access_token", "id_token", "refresh_token")

_PATTERN = re.compile(
    r"\b(" + "|".join(_SECRET_PARAMS) + r")=[^&\s\"']+",
    re.IGNORECASE,
)


def scrub(text: str) -> str:
    return _PATTERN.sub(lambda m: f"{m.group(1)}=[redacted]", text)


class RedactSecrets(logging.Filter):
    """Redact secret query parameters from any record passing through."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Applied to args rather than the formatted message: uvicorn's access
        # logger builds its line from args, so rewriting `record.msg` alone
        # would leave the credential untouched.
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    scrub(a) if isinstance(a, str) else a for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: scrub(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        return True


def install() -> None:
    """Attach the filter to every logger that can carry a request line."""
    f = RedactSecrets()
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn", "", "coach.sync"):
        logging.getLogger(name).addFilter(f)
    # Handlers too: a filter on a logger does not apply to records that reach a
    # handler by propagation from a child.
    for handler in logging.getLogger().handlers:
        handler.addFilter(f)
