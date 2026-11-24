"""Ponto de entrada FastAPI do moodle-service com lifespan gerenciado."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request

from app.api.routes import router
from app.core.config import Settings
from app.core.logging import configure_logging, log_context
from app.domain.contracts import StateRepository
from app.infrastructure.db.models import Base
from app.infrastructure.db.repository import SqlAlchemyStateRepository
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.messaging.consumer import RabbitMQConsumer
from app.infrastructure.moodle.client import MoodleClient


def create_app(
    settings: Settings | None = None,
    *,
    repo: StateRepository | None = None,
    moodle_client: MoodleClient | None = None,
    consumer: Any | None = None,
    create_tables_on_startup: bool = False,
) -> FastAPI:
    """Fábrica da aplicação FastAPI para execução em produção e testes."""
    active_settings = settings or Settings()
    configure_logging(service_name=active_settings.service_name)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        owns_client = moodle_client is None
        active_client = moodle_client or MoodleClient(active_settings)

        engine = None
        if repo is None:
            engine = create_db_engine(active_settings.database_url)
            if create_tables_on_startup:
                Base.metadata.create_all(engine)
            active_repo: StateRepository = SqlAlchemyStateRepository(create_session_factory(engine))
        else:
            active_repo = repo

        active_consumer = consumer
        owns_consumer = False
        if active_consumer is None and active_settings.rabbitmq_enabled:
            active_consumer = RabbitMQConsumer(active_settings, active_client, active_repo)
            owns_consumer = True
            await active_consumer.start()

        application.state.settings = active_settings
        application.state.repo = active_repo
        application.state.moodle_client = active_client
        application.state.consumer = active_consumer

        try:
            yield
        finally:
            if owns_consumer and active_consumer is not None:
                await active_consumer.stop()
            if owns_client:
                await active_client.aclose()
            if engine is not None:
                engine.dispose()

    application = FastAPI(
        title="Moodle Integration Service (T2)",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
        with log_context(correlation_id=correlation_id):
            response = await call_next(request)
            response.headers["X-Correlation-Id"] = correlation_id
            return response

    application.include_router(router)
    return application


app = create_app()
