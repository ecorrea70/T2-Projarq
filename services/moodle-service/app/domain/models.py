"""Modelos de domínio internos do moodle-service."""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ClaimResult:
    """Resultado da tentativa de registro idempotente (Inbox) de uma mensagem."""

    already_succeeded: bool = False
    already_dead_lettered: bool = False
    redelivered_in_flight: bool = False
    attempts: int = 1
    cached_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class HandlerOutcome:
    """Resultado produzido por um handler após execução contra o Moodle."""

    event_type: str
    result: dict[str, Any]
    reconciled: bool = False


@dataclass(frozen=True)
class DispatchOutcome:
    """Decisão final do dispatcher ao processar uma entrega do RabbitMQ."""

    action: str  # "ack" | "retried" | "dlq" | "duplicate"
    event_type: str | None = None
    reconciled: bool = False
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
