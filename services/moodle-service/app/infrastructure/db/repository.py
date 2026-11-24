"""Repositório SQLAlchemy para idempotência (Inbox) e estado local do Moodle."""
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.models import ClaimResult
from app.infrastructure.db.models import MoodleEnrolment, MoodleEntity, ProcessedMessage, utcnow


class SqlAlchemyStateRepository:
    """Implementa persistência transacional no banco próprio do moodle-service."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def ping(self) -> bool:
        with self._session_factory() as session:
            session.execute(text("SELECT 1"))
            return True

    def claim_message(
        self,
        message_id: str,
        event_type: str,
        event_version: str,
        correlation_id: str,
        idempotency_key: str,
        payload_hash: str,
        allow_replay: bool = False,
    ) -> ClaimResult:
        """Registra ou reivindica o processamento de uma mensagem de forma idempotente."""
        with self._session_factory() as session:
            existing = session.get(ProcessedMessage, message_id)
            if existing is None:
                record = ProcessedMessage(
                    message_id=message_id,
                    event_type=event_type,
                    event_version=event_version,
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    status="processing",
                    attempts=1,
                    reconciled=False,
                )
                session.add(record)
                try:
                    session.commit()
                    return ClaimResult(attempts=1)
                except IntegrityError:
                    session.rollback()
                    existing = session.get(ProcessedMessage, message_id)
                    if existing is None:
                        raise

            if existing.status == "succeeded":
                return ClaimResult(
                    already_succeeded=True,
                    attempts=existing.attempts,
                    cached_result=existing.result_json,
                )

            if existing.status == "dead_lettered" and not allow_replay:
                return ClaimResult(
                    already_dead_lettered=True,
                    attempts=existing.attempts,
                )

            # Reentrega após falha/reinício (Cenário 4) ou replay da DLQ.
            existing.status = "processing"
            existing.attempts += 1
            existing.updated_at = utcnow()
            if allow_replay:
                existing.last_error = None
            session.commit()
            return ClaimResult(redelivered_in_flight=True, attempts=existing.attempts)

    def mark_message_succeeded(
        self,
        message_id: str,
        result: dict[str, Any],
        reconciled: bool = False,
    ) -> None:
        with self._session_factory() as session:
            record = session.get(ProcessedMessage, message_id)
            if record is None:
                return
            now = utcnow()
            record.status = "succeeded"
            record.reconciled = record.reconciled or reconciled
            record.result_json = result
            record.last_error = None
            record.updated_at = now
            record.processed_at = now
            session.commit()

    def mark_message_retry(self, message_id: str, error_message: str) -> None:
        with self._session_factory() as session:
            record = session.get(ProcessedMessage, message_id)
            if record is None:
                return
            record.status = "processing"
            record.last_error = error_message
            record.updated_at = utcnow()
            session.commit()

    def mark_message_dead_lettered(
        self,
        message_id: str,
        error_message: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self._session_factory() as session:
            record = session.get(ProcessedMessage, message_id)
            if record is None:
                return
            now = utcnow()
            record.status = "dead_lettered"
            record.last_error = error_message
            if result is not None:
                record.result_json = result
            record.updated_at = now
            record.processed_at = now
            session.commit()

    def get_entity_by_idnumber(self, idnumber: str) -> MoodleEntity | None:
        with self._session_factory() as session:
            stmt = select(MoodleEntity).where(MoodleEntity.idnumber == idnumber)
            return session.scalar(stmt)

    def upsert_entity(
        self,
        entity_type: str,
        idnumber: str,
        moodle_id: int,
        shortname_or_username: str | None,
        email: str | None,
        origin_id: str | None,
        last_sync_status: str,
        last_correlation_id: str,
    ) -> MoodleEntity:
        with self._session_factory() as session:
            stmt = select(MoodleEntity).where(MoodleEntity.idnumber == idnumber)
            entity = session.scalar(stmt)
            now = utcnow()
            if entity is None:
                entity = MoodleEntity(
                    entity_type=entity_type,
                    idnumber=idnumber,
                    moodle_id=moodle_id,
                    shortname_or_username=shortname_or_username,
                    email=email,
                    origin_id=origin_id,
                    last_sync_status=last_sync_status,
                    last_correlation_id=last_correlation_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(entity)
            else:
                entity.entity_type = entity_type
                entity.moodle_id = moodle_id
                entity.shortname_or_username = shortname_or_username or entity.shortname_or_username
                if email is not None:
                    entity.email = email
                if origin_id is not None:
                    entity.origin_id = origin_id
                entity.last_sync_status = last_sync_status
                entity.last_correlation_id = last_correlation_id
                entity.updated_at = now
            session.commit()
            session.refresh(entity)
            return entity

    def upsert_enrolment(
        self,
        course_idnumber: str,
        user_idnumber: str,
        role_id: int,
        moodle_course_id: int,
        moodle_user_id: int,
        status: str,
        warning_message: str | None,
        last_correlation_id: str,
    ) -> MoodleEnrolment:
        with self._session_factory() as session:
            stmt = select(MoodleEnrolment).where(
                MoodleEnrolment.course_idnumber == course_idnumber,
                MoodleEnrolment.user_idnumber == user_idnumber,
                MoodleEnrolment.role_id == role_id,
            )
            enrolment = session.scalar(stmt)
            now = utcnow()
            if enrolment is None:
                enrolment = MoodleEnrolment(
                    course_idnumber=course_idnumber,
                    user_idnumber=user_idnumber,
                    role_id=role_id,
                    moodle_course_id=moodle_course_id,
                    moodle_user_id=moodle_user_id,
                    status=status,
                    warning_message=warning_message,
                    last_correlation_id=last_correlation_id,
                    confirmed_at=now,
                )
                session.add(enrolment)
            else:
                enrolment.moodle_course_id = moodle_course_id
                enrolment.moodle_user_id = moodle_user_id
                enrolment.status = status
                enrolment.warning_message = warning_message
                enrolment.last_correlation_id = last_correlation_id
                enrolment.confirmed_at = now
            session.commit()
            session.refresh(enrolment)
            return enrolment

    def get_run_summary(self, correlation_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            stmt = (
                select(ProcessedMessage)
                .where(ProcessedMessage.correlation_id == correlation_id)
                .order_by(ProcessedMessage.received_at.asc())
            )
            rows = list(session.scalars(stmt).all())
            return {
                "correlation_id": correlation_id,
                "total_messages": len(rows),
                "summary": {
                    "processing": sum(1 for r in rows if r.status == "processing"),
                    "succeeded": sum(1 for r in rows if r.status == "succeeded"),
                    "dead_lettered": sum(1 for r in rows if r.status == "dead_lettered"),
                    "reconciled": sum(1 for r in rows if r.reconciled),
                },
                "messages": [
                    {
                        "message_id": r.message_id,
                        "event_type": r.event_type,
                        "idempotency_key": r.idempotency_key,
                        "status": r.status,
                        "attempts": r.attempts,
                        "reconciled": r.reconciled,
                        "last_error": r.last_error,
                        "result": r.result_json,
                        "received_at": r.received_at.isoformat() if r.received_at else None,
                        "processed_at": r.processed_at.isoformat() if r.processed_at else None,
                    }
                    for r in rows
                ],
            }
