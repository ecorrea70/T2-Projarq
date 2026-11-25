"""API Gateway — borda pública na porta 8000."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from app.api.routes import router
from app.core.config import Settings
from app.core.logging import configure_logging, log_context
from app.infrastructure.sync_proxy import SyncServiceProxy


def create_app(
    settings: Settings | None = None,
    *,
    sync_proxy: SyncServiceProxy | None = None,
) -> FastAPI:
    active_settings = settings or Settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_proxy = sync_proxy or SyncServiceProxy(active_settings)
        application.state.settings = active_settings
        application.state.sync_proxy = active_proxy
        try:
            yield
        finally:
            await active_proxy.aclose()

    application = FastAPI(
        title="T2 API Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        raw = request.headers.get("X-Correlation-Id")
        correlation_id = raw.strip() if raw and raw.strip() else str(uuid4())
        request.state.correlation_id = correlation_id
        with log_context(correlation_id=correlation_id):
            response = await call_next(request)
            response.headers["X-Correlation-Id"] = correlation_id
            return response

    application.include_router(router)
    return application


app = create_app()
