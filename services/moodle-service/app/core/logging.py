"""Logging estruturado em JSON com propagação de correlation_id via ContextVar."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any, Iterator

correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)
message_id_ctx: ContextVar[str | None] = ContextVar("message_id", default=None)
event_type_ctx: ContextVar[str | None] = ContextVar("event_type", default=None)

_EXTRA_FIELDS = (
    "idempotency_key",
    "wsfunction",
    "duration_ms",
    "attempt",
    "reconciled",
    "status",
    "error_type",
    "cb_state",
)


@contextmanager
def log_context(
    correlation_id: str | None = None,
    message_id: str | None = None,
    event_type: str | None = None,
) -> Iterator[None]:
    """Associa metadados de correlação ao contexto assíncrono corrente."""
    t_corr = correlation_id_ctx.set(correlation_id)
    t_msg = message_id_ctx.set(message_id)
    t_evt = event_type_ctx.set(event_type)
    try:
        yield
    finally:
        correlation_id_ctx.reset(t_corr)
        message_id_ctx.reset(t_msg)
        event_type_ctx.reset(t_evt)


class JsonFormatter(logging.Formatter):
    """Formata cada registro de log como uma linha JSON para observabilidade."""

    def __init__(self, service_name: str = "moodle-service") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "correlation_id": correlation_id_ctx.get(),
            "message_id": message_id_ctx.get(),
            "event_type": event_type_ctx.get(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(service_name: str = "moodle-service", level: int = logging.INFO) -> logging.Logger:
    """Configura o logger principal do serviço com saída JSON no stdout."""
    log = logging.getLogger("moodle_service")
    log.setLevel(level)
    log.propagate = False
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter(service_name=service_name))
        log.addHandler(handler)
    return log


def get_logger(suffix: str | None = None) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"moodle_service.{suffix}" if suffix else "moodle_service")
