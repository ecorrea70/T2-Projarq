"""FastAPI do ieducar-service."""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from app.api.routes import router
from app.application.scanner import SourceScanner, scan_loop
from app.core.config import Settings
from app.core.logging import configure_logging, get_logger, log_context
from app.infrastructure.db.models import Base
from app.infrastructure.db.repository import IEducarRepository
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.ieducar.client import IEducarClient
from app.infrastructure.messaging.bootstrap import MessagingContext

_log = get_logger("main")


def create_app(
    settings: Settings | None = None,
    *,
    repo: IEducarRepository | None = None,
    client: IEducarClient | None = None,
    scanner: SourceScanner | None = None,
    create_tables_on_startup: bool = False,
    start_scan_loop: bool = True,
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
            active_repo = IEducarRepository(create_session_factory(engine))
        else:
            active_repo = repo

        # Uma varredura interrompida por queda não pode bloquear as próximas.
        try:
            interrupted = active_repo.mark_stale_scans_interrupted()
            if interrupted:
                _log.warning(
                    "%d varredura(s) marcada(s) como interrompida(s) na subida.",
                    interrupted,
                    extra={"status": "interrupted"},
                )
        except Exception:
            _log.exception("Não foi possível revisar varreduras pendentes na subida.")

        messaging = MessagingContext(active_settings)
        try:
            await messaging.start()
        except Exception as exc:
            _log.warning(
                "RabbitMQ indisponível na subida (%s); os avisos ficarão sem publicação até reconectar.",
                type(exc).__name__,
                extra={"error_type": type(exc).__name__},
            )

        active_client = client or IEducarClient(active_settings)
        active_scanner = scanner or SourceScanner(
            active_settings,
            active_repo,
            active_client,
            messaging.publisher,
        )

        application.state.settings = active_settings
        application.state.repo = active_repo
        application.state.client = active_client
        application.state.scanner = active_scanner
        application.state.messaging = messaging

        loop_task: asyncio.Task[None] | None = None
        if start_scan_loop:
            loop_task = asyncio.create_task(scan_loop(active_scanner, active_settings))

        try:
            yield
        finally:
            if loop_task is not None:
                loop_task.cancel()
                try:
                    await loop_task
                except asyncio.CancelledError:
                    pass
            if client is None:
                await active_client.aclose()
            await messaging.stop()
            if engine is not None:
                engine.dispose()

    application = FastAPI(
        title="i-Educar Service (T2)",
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
