"""Schemas versionados (v1) de envelopes e payloads de comandos/eventos."""
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class MessageEnvelope(BaseModel):
    """Envelope padrão v1 para comunicação assíncrona via RabbitMQ."""

    message_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=128)
    event_version: Literal["1.0"]
    correlation_id: str = Field(min_length=1, max_length=64)
    causation_id: str | None = Field(default=None, max_length=64)
    producer: str = Field(min_length=1, max_length=64)
    occurred_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=255)
    payload: dict[str, Any]


class CourseSyncRequestedPayload(BaseModel):
    turma_id: int = Field(ge=1)
    disciplina_id: int = Field(ge=1)
    idnumber: str = Field(min_length=1, max_length=100)
    shortname: str = Field(min_length=1, max_length=100)
    fullname: str = Field(min_length=1, max_length=254)


class UserSyncRequestedPayload(BaseModel):
    kind: Literal["teacher", "student"]
    origin_id: int = Field(ge=0)
    idnumber: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=100)
    firstname: str | None = None
    lastname: str | None = None
    email: str | None = None


class TeacherEnrolRequestedPayload(BaseModel):
    professor_id: int = Field(ge=0)
    turma_id: int = Field(ge=1)
    disciplina_id: int = Field(ge=1)
    user_idnumber: str = Field(min_length=1, max_length=100)
    course_idnumber: str = Field(min_length=1, max_length=100)


class StudentEnrolItem(BaseModel):
    aluno_id: int = Field(ge=0)
    user_idnumber: str = Field(min_length=1, max_length=100)


class StudentsEnrolRequestedPayload(BaseModel):
    turma_id: int = Field(ge=1)
    disciplina_id: int = Field(ge=1)
    course_idnumber: str = Field(min_length=1, max_length=100)
    students: list[StudentEnrolItem] = Field(min_length=1, max_length=100)


def build_result_event(
    envelope: MessageEnvelope,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Constrói evento de resposta em integration.events preservando correlação."""
    return {
        "message_id": str(uuid4()),
        "event_type": event_type,
        "event_version": "1.0",
        "correlation_id": envelope.correlation_id,
        "causation_id": envelope.message_id,
        "producer": "moodle-service",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": envelope.idempotency_key,
        "payload": payload,
    }
