"""FastAPI do sync-service."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from app.api.routes import router
from app.application.sync_coordinator import SyncCoordinator
from app.core.config import Settings
from app.core.logging import configure_logging, log_context
from app.infrastructure.db.models import Base
from app.infrastructure.db.repository import SyncRepository
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.messaging.bootstrap import MessagingContext
from app.infrastructure.messaging.events_consumer import MoodleEventsConsumer


def create_app(
    settings: Settings | None = None,
    *,
    repo: SyncRepository | None = None,
    coordinator: SyncCoordinator | None = None,
    create_tables_on_startup: bool = False,
) -> FastAPI:
    active_settings = settings or Settings()
    configure_logging(active_settings.service_name)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = None
        if repo is None:
            engine = create_db_engine(active_settings.database_url)
            if create_tables_on_startup:
                Base.metadata.create_all(engine)
            active_repo = SyncRepository(create_session_factory(engine))
        else:
            active_repo = repo

        messaging = MessagingContext(active_settings)
        await messaging.start()

        owns_coordinator = coordinator is None
        active_coordinator = coordinator or SyncCoordinator(
            active_settings,
            active_repo,
            messaging.publisher,
            messaging=messaging,
        )
        events_consumer = MoodleEventsConsumer(active_settings, active_coordinator)
        await events_consumer.start()

        application.state.settings = active_settings
        application.state.repo = active_repo
        application.state.coordinator = active_coordinator
        application.state.messaging = messaging
        application.state.events_consumer = events_consumer

        try:
            yield
        finally:
            await events_consumer.stop()
            if owns_coordinator:
                await active_coordinator.aclose()
            await messaging.stop()
            if engine is not None:
                engine.dispose()

    application = FastAPI(
        title="Sync Service (T2)",
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
