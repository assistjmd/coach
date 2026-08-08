"""Sources that are listed but not yet connectable.

Garmin and Google Health are both gated on approvals we have applied for and do
not control. Listing them is not decoration — a Garmin user who opens Sources,
sees Oura and Strava, and concludes their watch is unsupported is a user we lose
before they ever try the product. Saying "we asked, it is pending" keeps them.

These implement the same protocol as a real provider so that nothing downstream
needs a special case, but every operation refuses. `me.py` marks them
unavailable, and the connect route turns an attempt into a clear 409 rather than
an OAuth redirect to a client id we do not have yet.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from app.providers.base import NormalizedRecord, ProviderInfo, TokenSet


class NotYetAvailable(RuntimeError):
    """Raised on any attempt to actually use an announced provider."""


class AnnouncedProvider:
    """A provider we intend to ship, surfaced early and inert."""

    scopes: list[str] = []

    def __init__(self, slug: str, display_name: str, note: str):
        self.info = ProviderInfo(
            slug=slug,
            display_name=display_name,
            auth_kind="oauth2",
            availability="coming_soon",
        )
        self.note = note

    def _refuse(self):
        raise NotYetAvailable(f"{self.info.display_name} is not connectable yet")

    def oauth_authorize_url(self, state: str) -> str:
        self._refuse()

    def exchange_code(self, code: str) -> TokenSet:
        self._refuse()

    def refresh(self, token: TokenSet) -> TokenSet:
        self._refuse()

    def sync(
        self,
        token: TokenSet,
        since: datetime | None,
        until: datetime | None = None,
    ) -> Iterable[NormalizedRecord]:
        self._refuse()


# The notes are deliberately specific about *why* and *what happens next*.
# "Coming soon" with no timeline is the phrasing every abandoned feature uses,
# and users have learned to read it as "never".
GARMIN = AnnouncedProvider(
    slug="garmin",
    display_name="Garmin",
    note="Developer access requested. Garmin reviews each app by hand — we'll "
    "turn this on the day they approve us.",
)

WAHOO = AnnouncedProvider(
    slug="wahoo",
    display_name="Wahoo",
    note="Requested by a tester with an ELEMNT. Wahoo approves each app by "
    "hand — the integration is built and waiting on them.",
)

GOOGLE_HEALTH = AnnouncedProvider(
    slug="google_health",
    display_name="Google Health",
    note="In build. This is where Fitbit data will arrive too — Fitbit's own API "
    "shuts down in September 2026 and moves here.",
)
