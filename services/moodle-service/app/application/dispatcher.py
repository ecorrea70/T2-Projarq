"""Dispatcher de mensagens RabbitMQ: Inbox, roteamento, retry e DLQ."""
from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Any

from pydantic import ValidationError

from app.application.handlers import (
    handle_course_sync,
    handle_students_enrol,
    handle_teacher_enrol,
    handle_user_sync,
    run_with_local_retry,
)
from app.core.config import Settings
from app.core.errors import PermanentDomainError, TransientIntegrationError
from app.core.logging import get_logger, log_context
from app.core.metrics import MESSAGE_PROCESSING_DURATION, MESSAGES_PROCESSED
from app.domain.contracts import EventPublisher, MoodleGateway, StateRepository
from app.domain.models import DispatchOutcome, HandlerOutcome
from app.infrastructure.messaging.schemas import MessageEnvelope, build_result_event

_FAILED_EVENT_MAP = {
    "moodle.course.sync.requested": "moodle.course.sync.failed",
    "moodle.user.sync.requested": "moodle.user.sync.failed",
    "moodle.teacher.enrol.requested": "moodle.teacher.enrol.failed",
    "moodle.students.enrol.requested": "moodle.students.enrol.failed",
}


def _normalize_event_type(event_type: str) -> str:
    return event_type[:-3] if event_type.endswith(".v1") else event_type


def _parse_retry_count(headers: dict[str, Any]) -> int:
    raw = headers.get("x-retry-count", 0)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _is_replayed(headers: dict[str, Any]) -> bool:
    raw = headers.get("x-replayed")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, bool):
        return raw
    return str(raw).lower() in {"1", "true", "yes"}


class MessageDispatcher:
    """Coordena idempotência, execução do caso de uso, eventos e encaminhamento para retry/DLQ."""

    def __init__(
        self,
        settings: Settings,
        gateway: MoodleGateway,
        repo: StateRepository,
        publisher: EventPublisher,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.repo = repo
        self.publisher = publisher
        self._log = get_logger("dispatcher")

    async def handle_raw_message(
        self,
        raw_body: bytes,
        routing_key: str,
        headers: dict[str, Any] | None = None,
    ) -> DispatchOutcome:
        headers = dict(headers or {})
        try:
            decoded = json.loads(raw_body.decode("utf-8"))
            envelope = MessageEnvelope.model_validate(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            error_msg = f"Envelope inválido: {type(exc).__name__}"
            self._log.error("Mensagem malformada enviada para DLQ: %s", error_msg)
            MESSAGES_PROCESSED.labels(event_type="unknown", outcome="dlq").inc()
            await self.publisher.publish_dlq(
                raw_body,
                {
                    "x-error-type": type(exc).__name__,
                    "x-error-message": error_msg,
                    "x-original-routing-key": routing_key,
                    "x-failed-at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return DispatchOutcome(action="dlq", error=error_msg)

        canonical_type = _normalize_event_type(envelope.event_type)
        payload_hash = hashlib.sha256(
            json.dumps(envelope.payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        with log_context(
            correlation_id=envelope.correlation_id,
            message_id=envelope.message_id,
            event_type=canonical_type,
        ):
            started = time.perf_counter()
            claim = self.repo.claim_message(
                message_id=envelope.message_id,
                event_type=canonical_type,
                event_version=envelope.event_version,
                correlation_id=envelope.correlation_id,
                idempotency_key=envelope.idempotency_key,
                payload_hash=payload_hash,
                allow_replay=_is_replayed(headers),
            )

            if claim.already_succeeded:
                self._log.info(
                    "Mensagem duplicada ignorada via Inbox.",
                    extra={"idempotency_key": envelope.idempotency_key, "status": "duplicate"},
                )
                MESSAGES_PROCESSED.labels(event_type=canonical_type, outcome="duplicate").inc()
                return DispatchOutcome(
                    action="duplicate",
                    event_type=canonical_type,
                    result=claim.cached_result or {},
                )

            if claim.already_dead_lettered:
                self._log.warning(
                    "Mensagem já encaminhada anteriormente para DLQ ignorada sem replay explícito.",
                    extra={"idempotency_key": envelope.idempotency_key, "status": "duplicate_dlq"},
                )
                MESSAGES_PROCESSED.labels(event_type=canonical_type, outcome="duplicate").inc()
                return DispatchOutcome(action="duplicate", event_type=canonical_type)

            try:
                outcome = await run_with_local_retry(
                    lambda attempt: self._execute_handler(
                        canonical_type,
                        envelope,
                        redelivered=claim.redelivered_in_flight,
                        attempt=attempt,
                    ),
                    max_retries=self.settings.local_max_retries,
                    base_delay=self.settings.local_retry_base_delay,
                )
            except PermanentDomainError as exc:
                return await self._send_to_dlq(
                    raw_body=raw_body,
                    routing_key=routing_key,
                    envelope=envelope,
                    canonical_type=canonical_type,
                    retry_count=_parse_retry_count(headers),
                    error=exc,
                    started=started,
                )
            except TransientIntegrationError as exc:
                retry_count = _parse_retry_count(headers)
                if retry_count < self.settings.rabbitmq_max_retries:
                    next_retry = retry_count + 1
                    self.repo.mark_message_retry(envelope.message_id, str(exc))
                    retry_headers = {
                        **headers,
                        "x-retry-count": next_retry,
                        "x-correlation-id": envelope.correlation_id,
                        "x-last-error": str(exc),
                    }
                    target_rk = routing_key or f"{canonical_type}.v1"
                    await self.publisher.publish_retry(target_rk, raw_body, retry_headers)
                    elapsed_s = time.perf_counter() - started
                    MESSAGE_PROCESSING_DURATION.labels(event_type=canonical_type).observe(elapsed_s)
                    MESSAGES_PROCESSED.labels(event_type=canonical_type, outcome="retried").inc()
                    self._log.warning(
                        "Mensagem encaminhada para fila de retry (%d/%d): %s",
                        next_retry,
                        self.settings.rabbitmq_max_retries,
                        exc,
                        extra={
                            "idempotency_key": envelope.idempotency_key,
                            "duration_ms": round(elapsed_s * 1000, 2),
                            "attempt": next_retry,
                            "error_type": type(exc).__name__,
                        },
                    )
                    return DispatchOutcome(
                        action="retried",
                        event_type=canonical_type,
                        error=str(exc),
                    )
                return await self._send_to_dlq(
                    raw_body=raw_body,
                    routing_key=routing_key,
                    envelope=envelope,
                    canonical_type=canonical_type,
                    retry_count=retry_count,
                    error=exc,
                    started=started,
                )

            self.repo.mark_message_succeeded(
                envelope.message_id,
                result=outcome.result,
                reconciled=outcome.reconciled,
            )
            event_payload = build_result_event(envelope, outcome.event_type, outcome.result)
            await self.publisher.publish_event(
                routing_key=f"{outcome.event_type}.v1",
                payload=event_payload,
                headers={"x-correlation-id": envelope.correlation_id},
            )
            elapsed_s = time.perf_counter() - started
            MESSAGE_PROCESSING_DURATION.labels(event_type=canonical_type).observe(elapsed_s)
            MESSAGES_PROCESSED.labels(event_type=canonical_type, outcome="succeeded").inc()
            self._log.info(
                "Mensagem processada com sucesso.",
                extra={
                    "idempotency_key": envelope.idempotency_key,
                    "duration_ms": round(elapsed_s * 1000, 2),
                    "reconciled": outcome.reconciled,
                    "status": outcome.result.get("status"),
                },
            )
            return DispatchOutcome(
                action="ack",
                event_type=outcome.event_type,
                reconciled=outcome.reconciled,
                result=outcome.result,
            )

    async def _execute_handler(
        self,
        canonical_type: str,
        envelope: MessageEnvelope,
        *,
        redelivered: bool,
        attempt: int,
    ) -> HandlerOutcome:
        if canonical_type == "moodle.course.sync.requested":
            return await handle_course_sync(
                envelope, self.gateway, self.repo, redelivered=redelivered, attempt=attempt
            )
        if canonical_type == "moodle.user.sync.requested":
            return await handle_user_sync(
                envelope, self.gateway, self.repo, redelivered=redelivered, attempt=attempt
            )
        if canonical_type == "moodle.teacher.enrol.requested":
            return await handle_teacher_enrol(
                envelope,
                self.gateway,
                self.repo,
                self.settings,
                redelivered=redelivered,
                attempt=attempt,
            )
        if canonical_type == "moodle.students.enrol.requested":
            return await handle_students_enrol(
                envelope,
                self.gateway,
                self.repo,
                self.settings,
                redelivered=redelivered,
                attempt=attempt,
            )
        raise PermanentDomainError(f"Tipo de evento não suportado pelo moodle-service: {canonical_type}")

    async def _send_to_dlq(
        self,
        *,
        raw_body: bytes,
        routing_key: str,
        envelope: MessageEnvelope,
        canonical_type: str,
        retry_count: int,
        error: Exception,
        started: float,
    ) -> DispatchOutcome:
        error_msg = str(error)
        failed_event_type = _FAILED_EVENT_MAP.get(canonical_type, "moodle.command.failed")
        failure_result = {"erro": error_msg, "error_type": type(error).__name__}

        self.repo.mark_message_dead_lettered(
            envelope.message_id,
            error_message=error_msg,
            result=failure_result,
        )
        await self.publisher.publish_event(
            routing_key=f"{failed_event_type}.v1",
            payload=build_result_event(envelope, failed_event_type, failure_result),
            headers={"x-correlation-id": envelope.correlation_id},
        )
        await self.publisher.publish_dlq(
            raw_body,
            {
                "x-error-type": type(error).__name__,
                "x-error-message": error_msg,
                "x-retry-count": retry_count,
                "x-correlation-id": envelope.correlation_id,
                "x-original-routing-key": routing_key or f"{canonical_type}.v1",
                "x-failed-at": datetime.now(timezone.utc).isoformat(),
            },
        )
        elapsed_s = time.perf_counter() - started
        MESSAGE_PROCESSING_DURATION.labels(event_type=canonical_type).observe(elapsed_s)
        MESSAGES_PROCESSED.labels(event_type=canonical_type, outcome="dlq").inc()
        self._log.error(
            "Mensagem enviada para DLQ (%s): %s",
            type(error).__name__,
            error_msg,
            extra={
                "idempotency_key": envelope.idempotency_key,
                "duration_ms": round(elapsed_s * 1000, 2),
                "error_type": type(error).__name__,
            },
        )
        return DispatchOutcome(action="dlq", event_type=failed_event_type, error=error_msg)
