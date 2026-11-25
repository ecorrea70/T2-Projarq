"""Modelos ORM do ieducar-service-db.

Duas tabelas, e nada do i-Educar além do necessário para responder "isso mudou?":

- `source_fingerprints`: a memória da última leitura de cada registro.
- `scan_runs`: a auditoria de cada varredura.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SourceFingerprint(Base):
    __tablename__ = "source_fingerprints"

    entity_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    entity_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_scan_id: Mapped[str] = mapped_column(String(64))
    last_correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ScanRun(Base):
    __tablename__ = "scan_runs"

    scan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    full_republish: Mapped[bool] = mapped_column(Boolean, default=False)
    records_read: Mapped[int] = mapped_column(Integer, default=0)
    changes_detected: Mapped[int] = mapped_column(Integer, default=0)
    changes_published: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
