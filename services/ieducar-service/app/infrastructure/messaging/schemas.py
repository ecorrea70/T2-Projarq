"""Envelope v1 e payload do resumo de varredura.

O envelope é o mesmo contrato dos outros serviços. O payload é um resumo magro:
diz em **quais tipos de entidade** houve mudança nesta varredura, não quais
registros mudaram nem o conteúdo deles. Quem recebe busca os dados nas rotas
`/internal/*` quando precisar, e assim a mensagem nunca fica desatualizada em
relação à origem.

Uma varredura publica **uma** mensagem, não uma por registro: 177 cursos
alterados pedem ao consumidor exatamente o mesmo trabalho que um curso
alterado, porque não existe sincronização parcial por registro. Ver ADR-006 em
`docs/adr.md`.
"""
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

ENTITY_TYPES = ("catalog", "teacher", "student")

ENTITY_ROUTES = {
    "catalog": "/internal/catalog",
    "teacher": "/internal/teachers",
    "student": "/internal/students",
}

SCAN_SUMMARY_EVENT_TYPE = "ieducar.scan.changed"
SCAN_SUMMARY_ROUTING_KEY = "ieducar.scan.changed.v1"


class MessageEnvelope(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=128)
    event_version: str = "1.0"
    correlation_id: str = Field(min_length=1, max_length=64)
    causation_id: str | None = Field(default=None, max_length=64)
    producer: str = "ieducar-service"
    occurred_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=255)
    payload: dict[str, Any]


class EntitySummary(BaseModel):
    """Por tipo de entidade: se mudou, quantas mudanças e onde buscar os dados."""

    changed: bool
    changes_detected: int = Field(ge=0)
    fetch_from: str


class ScanSummaryPayload(BaseModel):
    """Resumo de uma varredura: o que mudou, sem dizer quais registros mudaram."""

    scan_id: str = Field(min_length=1, max_length=64)
    trigger: str = Field(min_length=1, max_length=16)
    full_republish: bool = False
    entities: dict[str, EntitySummary]


def announced_entities(by_entity: dict[str, dict]) -> list[str]:
    """Tipos de entidade que o resumo marca como mudados, em ordem estável."""
    return sorted(
        entity_type
        for entity_type, data in by_entity.items()
        if data.get("changes_published")
    )


def build_scan_summary_envelope(
    *,
    scan_id: str,
    trigger: str,
    correlation_id: str,
    by_entity: dict[str, dict],
    full_republish: bool = False,
) -> MessageEnvelope:
    entities = {
        entity_type: EntitySummary(
            changed=bool(data.get("changes_published")),
            changes_detected=int(data.get("changes_detected", 0)),
            fetch_from=ENTITY_ROUTES[entity_type],
        )
        for entity_type, data in by_entity.items()
    }
    payload = ScanSummaryPayload(
        scan_id=scan_id,
        trigger=trigger,
        full_republish=full_republish,
        entities=entities,
    )
    return MessageEnvelope(
        message_id=str(uuid4()),
        event_type=SCAN_SUMMARY_EVENT_TYPE,
        correlation_id=correlation_id,
        causation_id=scan_id,
        occurred_at=datetime.now(timezone.utc),
        idempotency_key=f"ieducar:scan:{scan_id}",
        payload=payload.model_dump(mode="json"),
    )
