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

    def __init__(
        self,
        slug: str,
        display_name: str,
        note: str,
        workaround_title: str | None = None,
        workaround_steps: list[str] | None = None,
    ):
        self.info = ProviderInfo(
            slug=slug,
            display_name=display_name,
            auth_kind="oauth2",
            availability="coming_soon",
        )
        self.note = note
        # A route that works today, where one exists. Garmin has no API access
        # for us yet, but Garmin Connect writes to Apple Health, so the data can
        # reach us anyway — telling the athlete that is worth more than making
        # them wait for an approval they cannot influence.
        self.workaround_title = workaround_title
        self.workaround_steps = workaround_steps or []

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
    note=None,
    workaround_title="Connect Garmin through Apple Health",
    workaround_steps=[
        "Open the Garmin Connect app.",
        "Tap the ☰ menu (bottom right), then Settings.",
        "Tap Apple Health, then turn on Enable Apple Health.",
        "Tap Turn All Categories On when Apple asks what to share, then Allow.",
        "Come back here and connect Apple Health if you have not already.",
        "Garmin writes new activities and sleep to Apple Health as they sync. "
        "History before today usually does not come across, so give it a day "
        "or two to fill in.",
    ],
)

WAHOO = AnnouncedProvider(
    slug="wahoo",
    display_name="Wahoo",
    note=None,
    workaround_title="Connect Wahoo through Apple Health",
    workaround_steps=[
        "Open the Wahoo app and go to your profile.",
        "Tap Apple Health, then turn on the connection.",
        "Allow the categories Apple asks about — workouts and heart rate.",
        "Come back here and connect Apple Health if you have not already.",
        "Rides sync to Apple Health as your ELEMNT uploads them.",
    ],
)

GOOGLE_HEALTH = AnnouncedProvider(
    slug="google_health",
    display_name="Google Health",
    note=None,
    workaround_title="Fitbit and Pixel through Apple Health",
    workaround_steps=[
        "Open the Fitbit app and go to your profile, then Fitbit settings.",
        "Tap Apple Health and turn on the connection.",
        "Allow the categories Apple asks about — activity, sleep and heart rate.",
        "Come back here and connect Apple Health if you have not already.",
    ],
)

OURA = AnnouncedProvider(
    slug="oura",
    display_name="Oura Ring",
    note=None,
    workaround_title="Connect Oura through Apple Health",
    workaround_steps=[
        "Open the Oura app and tap the menu icon.",
        "Go to Settings, then Data Sharing, then Apple Health.",
        "Turn on Connect to Health.",
        "Allow the categories Apple asks about — sleep, heart rate, "
        "resting heart rate, steps and workouts.",
        "Come back here and connect Apple Health if you have not already.",
    ],
)

STRAVA = AnnouncedProvider(
    slug="strava",
    display_name="Strava",
    note=None,
    workaround_title="Connect Strava through Apple Health",
    workaround_steps=[
        "Open Strava and go to the You tab, then tap the settings icon.",
        "Tap Manage Apps and Devices, then Health, then the plus icon.",
        "Tap Connect, then enable Workouts & Routes.",
        "Turn on Send to Health.",
        "Come back here and connect Apple Health if you have not already.",
    ],
)
