from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth import apple
from app.config import settings
from app.jobs import scheduler
from app.routes import auth as auth_routes
from app.routes import chat as chat_routes
from app.routes import dashboard as dashboard_routes
from app.routes import goal as goal_routes
from app.routes import ingest as ingest_routes
from app.routes import insights as insights_routes
from app.routes import legal as legal_routes
from app.routes import me as me_routes
from app.routes import providers as provider_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    apple.warm_cache()
    if settings().app_env != "test":
        scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="coach", version="0.1.0", lifespan=lifespan)

app.include_router(auth_routes.router)
app.include_router(me_routes.router)
app.include_router(provider_routes.router)
app.include_router(ingest_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(chat_routes.router)
app.include_router(goal_routes.router)
app.include_router(insights_routes.router)
app.include_router(legal_routes.router)

if settings().app_env == "dev":
    from app.routes import dev as dev_routes

    app.include_router(dev_routes.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
