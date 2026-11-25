"""Persistência de execuções e itens de sincronização."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.domain.models import PlannedCommand
from app.infrastructure.db.models import SyncRun, SyncRunItem, utcnow


@dataclass
class ActiveRunConflict:
    correlation_id: str
    kind: str
    status: str


@dataclass
class RunProgress:
    correlation_id: str
    kind: str
    status: str
    phase: str
    phase_2_published: bool
    total_items: int
    pending_count: int
    succeeded_count: int
    failed_count: int
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    summary_json: dict | None
    items: list[dict[str, Any]]


class SyncRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def ping(self) -> bool:
        with self._session_factory() as session:
            session.execute(text("SELECT 1"))
            return True

    def run_exists(self, correlation_id: str) -> bool:
        with self._session_factory() as session:
            return session.get(SyncRun, correlation_id) is not None

    def find_active_run(self, kind: str | None, scope: str) -> ActiveRunConflict | None:
        with self._session_factory() as session:
            stmt = select(SyncRun).where(SyncRun.status == "running")
            if scope == "per_kind" and kind:
                stmt = stmt.where(SyncRun.kind == kind)
            row = session.scalars(stmt).first()
            if row is None:
                return None
            return ActiveRunConflict(
                correlation_id=row.correlation_id,
                kind=row.kind,
                status=row.status,
            )

    def create_run(
        self,
        correlation_id: str,
        kind: str,
        commands: list[PlannedCommand],
        *,
        extra_summary: dict[str, Any] | None = None,
    ) -> None:
        now = utcnow()
        command_index = {
            cmd.idempotency_key: {
                "phase": cmd.phase,
                "command_type": cmd.command_type,
                "routing_key": cmd.routing_key,
                "payload": cmd.payload,
            }
            for cmd in commands
        }
        summary = {"commands": command_index, **(extra_summary or {})}
        with self._session_factory() as session:
            run = SyncRun(
                correlation_id=correlation_id,
                kind=kind,
                status="running",
                phase="phase_1",
                phase_2_published=False,
                total_items=len(commands),
                pending_count=len(commands),
                succeeded_count=0,
                failed_count=0,
                summary_json=summary,
                started_at=now,
                updated_at=now,
            )
            session.add(run)
            for cmd in commands:
                session.add(
                    SyncRunItem(
                        correlation_id=correlation_id,
                        phase=cmd.phase,
                        command_type=cmd.command_type,
                        message_id=None,
                        idempotency_key=cmd.idempotency_key,
                        status="pending",
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.commit()

    def mark_item_published(self, correlation_id: str, idempotency_key: str, message_id: str) -> None:
        with self._session_factory() as session:
            item = session.scalars(
                select(SyncRunItem).where(
                    SyncRunItem.correlation_id == correlation_id,
                    SyncRunItem.idempotency_key == idempotency_key,
                )
            ).first()
            if item is None:
                return
            item.message_id = message_id
            item.updated_at = utcnow()
            session.commit()

    def apply_event_result(
        self,
        correlation_id: str,
        idempotency_key: str,
        *,
        success: bool,
        payload: dict[str, Any],
        error: str | None = None,
    ) -> bool:
        """Atualiza item e contadores. Retorna True se houve mudança de estado."""
        with self._session_factory() as session:
            run = session.get(SyncRun, correlation_id)
            item = session.scalars(
                select(SyncRunItem).where(
                    SyncRunItem.correlation_id == correlation_id,
                    SyncRunItem.idempotency_key == idempotency_key,
                )
            ).first()
            if run is None or item is None:
                return False
            if item.status in {"succeeded", "failed", "skipped"}:
                return False

            item.status = "succeeded" if success else "failed"
            item.result_json = payload
            item.last_error = error
            item.updated_at = utcnow()
            run.pending_count = max(0, run.pending_count - 1)
            if success:
                run.succeeded_count += 1
            else:
                run.failed_count += 1
            run.updated_at = utcnow()
            session.commit()
            return True

    def mark_item_skipped(self, correlation_id: str, idempotency_key: str, reason: str) -> None:
        with self._session_factory() as session:
            run = session.get(SyncRun, correlation_id)
            item = session.scalars(
                select(SyncRunItem).where(
                    SyncRunItem.correlation_id == correlation_id,
                    SyncRunItem.idempotency_key == idempotency_key,
                )
            ).first()
            if run is None or item is None or item.status != "pending":
                return
            item.status = "skipped"
            item.last_error = reason
            item.updated_at = utcnow()
            run.pending_count = max(0, run.pending_count - 1)
            run.failed_count += 1
            run.updated_at = utcnow()
            session.commit()

    def list_phase2_unpublished(self, correlation_id: str) -> list[SyncRunItem]:
        with self._session_factory() as session:
            items = session.scalars(
                select(SyncRunItem).where(
                    SyncRunItem.correlation_id == correlation_id,
                    SyncRunItem.phase == 2,
                    SyncRunItem.status == "pending",
                    SyncRunItem.message_id.is_(None),
                )
            ).all()
            return list(items)

    def claim_phase2_publication(self, correlation_id: str) -> bool:
        """Transição atômica para fase 2: evita disparo duplicado sob consumo concorrente."""
        with self._session_factory() as session:
            stmt = (
                update(SyncRun)
                .where(
                    SyncRun.correlation_id == correlation_id,
                    SyncRun.status == "running",
                    SyncRun.phase_2_published.is_(False),
                )
                .values(phase="phase_2", phase_2_published=True, updated_at=utcnow())
            )
            result = session.execute(stmt)
            session.commit()
            return bool(result.rowcount and result.rowcount > 0)

    def mark_phase2_published(self, correlation_id: str) -> None:
        with self._session_factory() as session:
            run = session.get(SyncRun, correlation_id)
            if run is None:
                return
            run.phase = "phase_2"
            run.phase_2_published = True
            run.updated_at = utcnow()
            session.commit()

    def try_finalize_run(self, correlation_id: str) -> str | None:
        """Finaliza run se não houver itens pendentes. Retorna status final ou None."""
        with self._session_factory() as session:
            run = session.get(SyncRun, correlation_id)
            if run is None or run.status != "running":
                return None
            pending = session.scalar(
                select(SyncRunItem.id).where(
                    SyncRunItem.correlation_id == correlation_id,
                    SyncRunItem.status == "pending",
                )
            )
            if pending is not None:
                return None
            final_status = "completed_with_errors" if run.failed_count > 0 else "completed"
            run.status = final_status
            run.finished_at = utcnow()
            run.updated_at = run.finished_at
            session.commit()
            return final_status

    def get_progress(self, correlation_id: str) -> RunProgress | None:
        with self._session_factory() as session:
            run = session.get(SyncRun, correlation_id)
            if run is None:
                return None
            items = session.scalars(
                select(SyncRunItem)
                .where(SyncRunItem.correlation_id == correlation_id)
                .order_by(SyncRunItem.phase, SyncRunItem.id)
            ).all()
            item_rows = [
                {
                    "phase": i.phase,
                    "command_type": i.command_type,
                    "idempotency_key": i.idempotency_key,
                    "status": i.status,
                    "message_id": i.message_id,
                    "result": i.result_json,
                    "error": i.last_error,
                }
                for i in items
            ]
            return RunProgress(
                correlation_id=run.correlation_id,
                kind=run.kind,
                status=run.status,
                phase=run.phase,
                phase_2_published=run.phase_2_published,
                total_items=run.total_items,
                pending_count=run.pending_count,
                succeeded_count=run.succeeded_count,
                failed_count=run.failed_count,
                started_at=run.started_at,
                updated_at=run.updated_at,
                finished_at=run.finished_at,
                summary_json=run.summary_json,
                items=item_rows,
            )

    def get_command_catalog(self, correlation_id: str) -> dict[str, dict[str, Any]]:
        with self._session_factory() as session:
            run = session.get(SyncRun, correlation_id)
            if run is None or not run.summary_json:
                return {}
            raw = run.summary_json.get("commands") or {}
            return dict(raw)

    def is_phase1_terminal(self, correlation_id: str) -> bool:
        with self._session_factory() as session:
            pending = session.scalar(
                select(SyncRunItem.id).where(
                    SyncRunItem.correlation_id == correlation_id,
                    SyncRunItem.phase == 1,
                    SyncRunItem.status == "pending",
                )
            )
            return pending is None

    def item_status(self, correlation_id: str, idempotency_key: str) -> str | None:
        with self._session_factory() as session:
            item = session.scalars(
                select(SyncRunItem).where(
                    SyncRunItem.correlation_id == correlation_id,
                    SyncRunItem.idempotency_key == idempotency_key,
                )
            ).first()
            return item.status if item else None