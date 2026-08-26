from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: object) -> object:
        """Trim surrounding whitespace from every string setting.

        A single leading space on ANTHROPIC_API_KEY cost hours: httpx rejects a
        header value that starts with a space, raising LocalProtocolError before
        any request is made, and the Anthropic SDK wraps that as
        APIConnectionError. So a stray character in a secret presented as a
        network outage, and the same misleading symptom appeared in both the
        daily brief and chat.

        Secrets get whitespace easily — a trailing newline when piped, a space
        after `=` in an .env file, an editor reformatting on paste. None of that
        should reach an HTTP header.
        """
        return v.strip() if isinstance(v, str) else v

    database_url: str

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, v: str) -> str:
        """Accept the `postgres://` URLs hosting providers hand out.

        Fly, Heroku, Railway and friends all inject `postgres://…`. SQLAlchemy 2
        refuses that scheme outright, and plain `postgresql://` selects psycopg2,
        which isn't installed — this project uses psycopg 3. Rewriting here means
        DATABASE_URL can be pasted verbatim from any provider.
        """
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v
    jwt_secret: str
    jwt_alg: str = "HS256"
    jwt_expires_hours: int = 720
    fernet_key: str

    apple_bundle_id: str = "com.trainwithcoach.app"
    apple_team_id: str = ""
    google_client_id_ios: str = ""
    google_client_id_web: str = ""

    anthropic_api_key: str = ""
    anthropic_model_default: str = "claude-sonnet-4-6"
    anthropic_model_cheap: str = "claude-haiku-4-5-20251001"

    # Chat is the only unbounded cost in the app: the daily brief runs once per
    # user per day, but nothing stops someone using the coach as a general
    # chatbot. Capped per rolling 24h, and run on the cheap model — the brief
    # keeps the better one, since it is the output that actually matters and it
    # is generated once.
    chat_daily_message_limit: int = 25
    # Separate from chat. Deliberately small: this runs on the expensive model,
    # and a seventh revision in one day is arguing, not training.
    adjust_daily_limit: int = 6

    # Subscription. The trial runs on our clock, not Apple's: a StoreKit
    # introductory offer needs a payment method on day zero, and asking an
    # unknown app's first users for a card before they have seen a single brief
    # costs more installs than it earns subscriptions.
    trial_days: int = 14
    subscription_product_ids: str = "com.trainwithcoach.app.monthly,com.trainwithcoach.app.yearly"
    # Apple's App Store Server Notifications and transaction JWS both carry an
    # environment. Verifying against the wrong one rejects every sandbox
    # purchase, which is every purchase during TestFlight.
    apple_iap_bundle_id: str = "com.trainwithcoach.app"
    # Apple's numeric id for the app. Required by SignedDataVerifier whenever the
    # environment is Production — without it every real purchase raises rather
    # than verifying, which is the sort of thing that only shows up on launch day.
    apple_app_apple_id: int = 6797961960
    # Turn on to require a subscription. Off until the products exist in App
    # Store Connect, so a deploy cannot lock out every current tester.
    subscriptions_enforced: bool = False

    # Direct provider connections, off for launch. Everything routes through
    # Apple Health instead, which has no approval to wait on and no per-app user
    # cap — Strava stops an unreviewed app at ten connected athletes and Oura at
    # ten users, so shipping those buttons publicly would break the app for the
    # eleventh person to install it. Flip on per provider as approvals land.
    direct_providers_enabled: bool = False

    # Who may read aggregate product metrics. Empty closes the route entirely,
    # which is the right default for an environment nobody configured.
    admin_emails: str = ""
    chat_max_output_tokens: int = 700

    oura_client_id: str = ""
    oura_client_secret: str = ""

    strava_client_id: str = ""
    strava_client_secret: str = ""

    # Fitbit's own Web API is turned down in September 2026; its replacement is
    # the Google Health API, which uses Google OAuth rather than Fitbit's.
    wahoo_client_id: str = ""
    wahoo_client_secret: str = ""
    google_health_client_id: str = ""
    google_health_client_secret: str = ""
    # Empty means "derive from public_base_url" — see providers.base.redirect_uri_for.
    oura_redirect_uri: str = ""

    app_env: str = "dev"
    app_deep_link_scheme: str = "coach"
    public_base_url: str = "http://localhost:8000"

    backup_bucket: str = ""
    aws_region: str = "us-east-2"


@lru_cache
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
