"""Modelos SQLAlchemy do banco independente do moodle-service."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProcessedMessage(Base):
    """Inbox Pattern: registra mensagens recebidas, tentativas e resultados para idempotência."""

    __tablename__ = "processed_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[str] = mapped_column(String(16), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reconciled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MoodleEntity(Base):
    """Mapeamento local entre idnumber determinístico e moodle_id (cursos e usuários)."""

    __tablename__ = "moodle_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    idnumber: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    moodle_id: Mapped[int] = mapped_column(Integer, nullable=False)
    shortname_or_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    origin_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_correlation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MoodleEnrolment(Base):
    """Estado local das matrículas confirmadas no Moodle (professores e alunos)."""

    __tablename__ = "moodle_enrolments"
    __table_args__ = (
        UniqueConstraint("course_idnumber", "user_idnumber", "role_id", name="uq_moodle_enrolment_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_idnumber: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    user_idnumber: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    role_id: Mapped[int] = mapped_column(Integer, nullable=False)
    moodle_course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    moodle_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    warning_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_correlation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
