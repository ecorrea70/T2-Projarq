"""Envelope v1 e payload do aviso de mudança.

O envelope é o mesmo contrato dos outros serviços. O payload é um aviso magro:
diz **o que** mudou e como encontrar o registro, não leva o registro dentro.
Quem recebe busca os dados nas rotas `/internal/*` quando precisar, e assim a
mensagem nunca fica desatualizada em relação à origem.
"""
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ENTITY_ROUTES = {
    "catalog": "/internal/catalog",
    "teacher": "/internal/teachers",
    "student": "/internal/students",
}


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


class ChangeNoticePayload(BaseModel):
    """Aviso magro: identidade, tipo de mudança e onde buscar os dados."""

    entity_type: Literal["catalog", "teacher", "student"]
    entity_key: str = Field(min_length=1, max_length=128)
    change_type: Literal["created", "updated", "removed"]
    identity: dict[str, int]
    fingerprint: str | None = None
    fetch_from: str


def identity_of(entity_type: str, entity_key: str) -> dict[str, int]:
    """Desdobra a chave em identificadores, para o consumidor não ter que parsear texto."""
    if entity_type == "catalog":
        turma_id, disciplina_id = entity_key.split(":")
        return {"turma_id": int(turma_id), "disciplina_id": int(disciplina_id)}
    if entity_type == "teacher":
        return {"professor_id": int(entity_key)}
    return {"aluno_id": int(entity_key)}


def routing_key_for(entity_type: str) -> str:
    return f"ieducar.{entity_type}.changed.v1"


def build_change_envelope(
    *,
    entity_type: str,
    entity_key: str,
    change_type: str,
    fingerprint: str | None,
    correlation_id: str,
    scan_id: str,
) -> MessageEnvelope:
    payload = ChangeNoticePayload(
        entity_type=entity_type,
        entity_key=entity_key,
        change_type=change_type,
        identity=identity_of(entity_type, entity_key),
        fingerprint=fingerprint,
        fetch_from=ENTITY_ROUTES[entity_type],
    )
    return MessageEnvelope(
        message_id=str(uuid4()),
        event_type=f"ieducar.{entity_type}.changed",
        correlation_id=correlation_id,
        causation_id=scan_id,
        occurred_at=datetime.now(timezone.utc),
        idempotency_key=f"ieducar:{entity_type}:{entity_key}:{fingerprint or 'removed'}",
        payload=payload.model_dump(mode="json"),
    )
