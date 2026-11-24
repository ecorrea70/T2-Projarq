"""Envelope v1 (espelho do contrato moodle-service)."""
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class MessageEnvelope(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=128)
    event_version: str = "1.0"
    correlation_id: str = Field(min_length=1, max_length=64)
    causation_id: str | None = None
    producer: str = "sync-service"
    occurred_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=255)
    payload: dict[str, Any]


def build_command_envelope(
    *,
    event_type: str,
    correlation_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
    message_id: str | None = None,
) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=message_id or str(uuid4()),
        event_type=event_type,
        occurred_at=datetime.now(timezone.utc),
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )


def routing_key_for_command(event_type: str) -> str:
    if event_type.endswith(".v1"):
        return event_type
    return f"{event_type}.v1"