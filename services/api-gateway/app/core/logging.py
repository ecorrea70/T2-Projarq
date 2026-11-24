"""Logging JSON com correlation_id."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any, Iterator

correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)


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
            "service": "api-gateway",
        }
        cid = correlation_id_ctx.get()
        if cid:
            payload["correlation_id"] = cid
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)