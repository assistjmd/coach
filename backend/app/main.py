from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth import apple
from app.config import settings
from app.jobs import scheduler
from app.logging_filters import install as install_log_redaction
from app.routes import auth as auth_routes
from app.routes import chat as chat_routes
from app.routes import dashboard as dashboard_routes
from app.routes import export as export_routes
from app.routes import goal as goal_routes
from app.routes import ingest as ingest_routes
from app.routes import insights as insights_routes
from app.routes import legal as legal_routes
from app.routes import me as me_routes
from app.routes import providers as provider_routes
from app.routes import subscriptions as subscription_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before anything can serve a request: the OAuth callback carries a live
    # authorization code in its query string, and uvicorn's access log records
    # the full request line.
    install_log_redaction()
    apple.warm_cache()
    if settings().app_env != "test":
        scheduler.start()
    yield
    scheduler.shutdown()


_IS_DEV = settings().app_env == "dev"

app = FastAPI(
    title="coach",
    version="0.1.0",
    lifespan=lifespan,
    # The interactive docs publish a complete map of the API — every route, every
    # field, every enum — to anyone who asks. Useful in development, and free
    # reconnaissance in production.
    docs_url="/docs" if _IS_DEV else None,
    redoc_url="/redoc" if _IS_DEV else None,
    openapi_url="/openapi.json" if _IS_DEV else None,
)


@app.middleware("http")
async def security_headers(request, call_next):
    """Baseline headers, mainly for the public legal pages.

    Those are the only HTML this API serves, and they are linked from the App
    Store listing and the consent screen. The JSON routes get them too, which
    costs nothing.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    # The legal pages are self-contained: inline style, no script, no images.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
    )
    return response

app.include_router(auth_routes.router)
app.include_router(me_routes.router)
app.include_router(provider_routes.router)
app.include_router(ingest_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(chat_routes.router)
app.include_router(goal_routes.router)
app.include_router(insights_routes.router)
app.include_router(export_routes.router)
app.include_router(subscription_routes.router)
app.include_router(legal_routes.router)

if settings().app_env == "dev":
    from app.routes import dev as dev_routes

    app.include_router(dev_routes.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
