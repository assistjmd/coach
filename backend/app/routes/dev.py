"""Dev-only endpoints. Registered by `main.py` only when `app_env == "dev"`.

Hosts the consent screen for the `mock` provider (`app/providers/mock.py`) so the
OAuth connect flow is exercisable without a registered third-party app.
"""

import html
import urllib.parse

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.providers.mock import VALID_CODE

router = APIRouter(prefix="/v1/dev", tags=["dev"])


def _guard() -> None:
    if settings().app_env != "dev":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")


def _back(redirect_uri: str, **params: str) -> RedirectResponse:
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urllib.parse.urlencode(params)}", status_code=302)


# response_model=None: the return is a union of Response subclasses, which FastAPI
# would otherwise try to interpret as a Pydantic model.
@router.get("/fake-consent", response_class=HTMLResponse, response_model=None)
def fake_consent(
    state: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query(default=""),
    decision: str | None = Query(default=None),
    code: str | None = Query(default=None),
) -> HTMLResponse | RedirectResponse:
    """Stand-in for a provider's consent screen.

    Interactive by default (two buttons, for driving the flow in the Simulator).
    Pass `decision=approve|deny` to skip straight to the redirect, which is what
    curl-based tests use. `code` overrides the returned authorization code so the
    `exchange_failed` branch is reachable.
    """
    _guard()

    if decision == "approve":
        return _back(redirect_uri, code=code or VALID_CODE, state=state)
    if decision == "deny":
        return _back(redirect_uri, error="access_denied", state=state)

    def link(**extra: str) -> str:
        q = urllib.parse.urlencode(
            {"state": state, "redirect_uri": redirect_uri, "scope": scope, **extra}
        )
        return html.escape(f"/v1/dev/fake-consent?{q}")

    return HTMLResponse(f"""<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mock Tracker — Authorize</title>
<style>
  body {{ font: 16px/1.5 -apple-system, system-ui, sans-serif; margin: 0;
         display: grid; place-items: center; min-height: 100vh; background: #f2f2f7; }}
  .card {{ background: #fff; padding: 28px; border-radius: 14px; width: min(380px, 88vw);
           box-shadow: 0 1px 3px rgba(0,0,0,.12); }}
  h1 {{ font-size: 20px; margin: 0 0 6px; }}
  p {{ color: #636366; margin: 0 0 20px; }}
  code {{ background: #f2f2f7; padding: 1px 5px; border-radius: 4px; font-size: 13px; }}
  a {{ display: block; text-align: center; padding: 13px; border-radius: 10px;
       text-decoration: none; font-weight: 600; margin-bottom: 10px; }}
  .ok {{ background: #007aff; color: #fff; }}
  .no {{ background: #f2f2f7; color: #ff3b30; }}
  .bad {{ background: transparent; color: #8e8e93; font-weight: 400; font-size: 14px; }}
</style>
<div class="card">
  <h1>Mock Tracker</h1>
  <p>Coach wants access to <code>{html.escape(scope or "read")}</code>. This is a
     development stand-in — no real account is involved.</p>
  <a class="ok" href="{link(decision="approve")}">Allow</a>
  <a class="no" href="{link(decision="deny")}">Deny</a>
  <a class="bad" href="{link(decision="approve", code="wrong-code")}">Allow, but return a bad code</a>
</div>""")
