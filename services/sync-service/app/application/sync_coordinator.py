"""Coordena início da sync, publicação de fases e reação a eventos do Moodle."""
from __future__ import annotations

from uuid import uuid4

from app.application.planner import plan_student_sync, plan_teacher_sync
from app.core.config import Settings
from app.core.logging import get_logger, log_context
from app.core.metrics import SYNC_DURATION, SYNC_RUNS_FINISHED, SYNC_RUNS_STARTED
from app.infrastructure.db.repository import SyncRepository
from app.infrastructure.ieducar.factory import IEducarSource, build_ieducar_source
from app.infrastructure.messaging.publisher import CommandPublisher
from app.infrastructure.messaging.schemas import build_command_envelope
from app.infrastructure.moodle_preflight import run_setup_check

_log = get_logger("sync_coordinator")

SUCCESS_SUFFIXES = (".synced", ".enrolled")
FAILURE_SUFFIXES = (".sync.failed", ".enrol.failed")


def _event_outcome(event_type: str) -> tuple[bool, str]:
    normalized = event_type.removesuffix(".v1")
    for suffix in FAILURE_SUFFIXES:
        if normalized.endswith(suffix):
            return False, normalized
    for suffix in SUCCESS_SUFFIXES:
        if normalized.endswith(suffix):
            return True, normalized
    return False, normalized


class SyncCoordinator:
    def __init__(
        self,
        settings: Settings,
        repo: SyncRepository,
        publisher: CommandPublisher | None,
        ieducar: IEducarSource | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._publisher = publisher
        self._ieducar = ieducar or build_ieducar_source(settings)

    async def start_teachers(self, correlation_id: str | None = None) -> dict:
        return await self._start("teachers", correlation_id, preflight_kind="teachers")

    async def start_students(self, correlation_id: str | None = None) -> dict:
        return await self._start("students", correlation_id, preflight_kind="students")

    async def _start(self, kind: str, correlation_id: str | None, preflight_kind: str) -> dict:
        conflict = self._repo.find_active_run(kind, self._settings.sync_lock_scope)
        if conflict is not None:
            return {
                "status": "conflict",
                "active": {
                    "correlation_id": conflict.correlation_id,
                    "kind": conflict.kind,
                    "status": conflict.status,
                },
            }

        corr = correlation_id or str(uuid4())
        with log_context(correlation_id=corr):
            preflight = await run_setup_check(self._settings, preflight_kind)
            catalog = await self._ieducar.fetch_catalog()
            if kind == "teachers":
                teachers = await self._ieducar.fetch_teachers()
                commands = plan_teacher_sync(catalog, teachers, self._settings)
            else:
                students = await self._ieducar.fetch_students()
                commands = plan_student_sync(catalog, students)

            if not commands:
                return {"status": "rejected", "reason": "Nenhum comando planejado (origem vazia ou inválida)."}

            self._repo.create_run(
                corr,
                kind,
                commands,
                extra_summary={
                    "ieducar_source": self._settings.ieducar_source,
                    "preflight": preflight,
                },
            )
            published = await self._publish_phase(corr, phase=1)
            SYNC_RUNS_STARTED.labels(kind=kind).inc()
            _log.info(
                "Sincronização iniciada (%s) com %d itens (%d publicados na fase 1).",
                kind,
                len(commands),
                published,
                extra={"kind": kind, "phase": "phase_1", "status": "running"},
            )
            return {
                "status": "accepted",
                "correlation_id": corr,
                "kind": kind,
                "total_items": len(commands),
                "phase_1_published": published,
                "preflight": preflight,
            }

    async def _publish_phase(self, correlation_id: str, phase: int) -> int:
        if self._publisher is None:
            return 0
        catalog = self._repo.get_command_catalog(correlation_id)
        count = 0
        for key, meta in catalog.items():
            if int(meta["phase"]) != phase:
                continue
            if phase == 2 and not self._should_publish_phase2_item(correlation_id, key, meta):
                self._repo.mark_item_skipped(correlation_id, key, "dependência da fase 1 falhou")
                continue
            envelope = build_command_envelope(
                event_type=meta["command_type"],
                correlation_id=correlation_id,
                idempotency_key=key,
                payload=meta["payload"],
            )
            msg_id = await self._publisher.publish_command(envelope)
            self._repo.mark_item_published(correlation_id, key, msg_id)
            count += 1
        if phase == 2:
            self._repo.mark_phase2_published(correlation_id)
            self._repo.try_finalize_run(correlation_id)
        return count

    def _should_publish_phase2_item(self, correlation_id: str, item_key: str, meta: dict) -> bool:
        payload = meta.get("payload") or {}
        if meta["command_type"] == "moodle.teacher.enrol.requested":
            course_key = f"course:{payload.get('course_idnumber')}"
            user_key = f"user:teacher:{payload.get('professor_id')}"
            for dep in (course_key, user_key):
                status = self._repo.item_status(correlation_id, dep)
                if status != "succeeded":
                    return False
            return True
        if meta["command_type"] == "moodle.students.enrol.requested":
            roster = payload.get("students") or []
            for student in roster:
                user_key = f"user:student:{student.get('aluno_id')}"
                if self._repo.item_status(correlation_id, user_key) != "succeeded":
                    return False
            return bool(roster)
        return True

    async def handle_moodle_event(self, envelope: dict) -> None:
        correlation_id = envelope.get("correlation_id")
        idempotency_key = envelope.get("idempotency_key")
        event_type = str(envelope.get("event_type", ""))
        if not correlation_id or not idempotency_key:
            return

        success, _ = _event_outcome(event_type)
        payload = envelope.get("payload") or {}
        error = None if success else str(payload.get("erro") or payload.get("error_type") or event_type)

        with log_context(correlation_id=correlation_id):
            self._repo.apply_event_result(
                correlation_id,
                idempotency_key,
                success=success,
                payload=payload,
                error=error,
            )
            progress = self._repo.get_progress(correlation_id)
            if (
                progress
                and progress.status == "running"
                and not progress.phase_2_published
                and self._repo.is_phase1_terminal(correlation_id)
            ):
                await self._publish_phase(correlation_id, phase=2)

            final = self._repo.try_finalize_run(correlation_id)
            if final:
                progress = self._repo.get_progress(correlation_id)
                if progress:
                    SYNC_RUNS_FINISHED.labels(kind=progress.kind, status=final).inc()
                    if progress.finished_at and progress.started_at:
                        SYNC_DURATION.labels(kind=progress.kind).observe(
                            (progress.finished_at - progress.started_at).total_seconds()
                        )
                _log.info(
                    "Sincronização finalizada com status %s.",
                    final,
                    extra={"status": final},
                )

    def get_progress(self, correlation_id: str) -> dict | None:
        progress = self._repo.get_progress(correlation_id)
        if progress is None:
            return None
        return {
            "correlation_id": progress.correlation_id,
            "kind": progress.kind,
            "status": progress.status,
            "phase": progress.phase,
            "phase_2_published": progress.phase_2_published,
            "counters": {
                "total": progress.total_items,
                "pending": progress.pending_count,
                "succeeded": progress.succeeded_count,
                "failed": progress.failed_count,
            },
            "started_at": progress.started_at.isoformat(),
            "updated_at": progress.updated_at.isoformat(),
            "finished_at": progress.finished_at.isoformat() if progress.finished_at else None,
            "summary": progress.summary_json,
            "items": progress.items,
        }