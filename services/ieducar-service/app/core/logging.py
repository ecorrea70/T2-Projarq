"""Logging estruturado JSON com correlation_id."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any, Iterator

correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_EXTRA_FIELDS = (
    "entity_type",
    "entity_key",
    "change_type",
    "scan_id",
    "trigger",
    "status",
    "attempt",
    "error_type",
    "duration_ms",
    "records_read",
    "changes_published",
    "entities_changed",
    "summary_published",
)


@contextmanager
def log_context(correlation_id: str | None = None) -> Iterator[None]:
    token = correlation_id_ctx.set(correlation_id)
    try:
        yield
    finally:
        correlation_id_ctx.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        cid = correlation_id_ctx.get()
        if cid:
            payload["correlation_id"] = cid
        for key in _EXTRA_FIELDS:
            if hasattr(record, key) and getattr(record, key) is not None:
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(service_name: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger(service_name).setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
