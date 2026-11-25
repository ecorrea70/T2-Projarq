"""Acesso ao ieducar-service-db: impressões digitais e auditoria das varreduras."""
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.application.fingerprint import EntityChange, fingerprint
from app.infrastructure.db.models import ScanRun, SourceFingerprint


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IEducarRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def ping(self) -> bool:
        with self._sessions() as session:
            session.execute(text("SELECT 1"))
        return True

    # ------------------------------------------------------------ varreduras

    def start_scan(
        self,
        scan_id: str,
        correlation_id: str,
        trigger: str,
        *,
        full_republish: bool = False,
    ) -> None:
        with self._sessions() as session:
            session.add(
                ScanRun(
                    scan_id=scan_id,
                    correlation_id=correlation_id,
                    trigger=trigger,
                    status="running",
                    full_republish=full_republish,
                    started_at=_utcnow(),
                )
            )
            session.commit()

    def finish_scan(
        self,
        scan_id: str,
        *,
        status: str,
        baseline: bool = False,
        records_read: int = 0,
        changes_detected: int = 0,
        changes_published: int = 0,
        error_message: str | None = None,
    ) -> None:
        with self._sessions() as session:
            run = session.get(ScanRun, scan_id)
            if run is None:
                return
            run.status = status
            run.baseline = baseline
            run.records_read = records_read
            run.changes_detected = changes_detected
            run.changes_published = changes_published
            run.error_message = error_message
            run.finished_at = _utcnow()
            session.commit()

    def mark_stale_scans_interrupted(self) -> int:
        """Varredura que ficou `running` por queda do processo não bloqueia a próxima."""
        with self._sessions() as session:
            stale = session.scalars(
                select(ScanRun).where(ScanRun.status == "running")
            ).all()
            for run in stale:
                run.status = "interrupted"
                run.error_message = "Processo reiniciou durante a varredura."
                run.finished_at = _utcnow()
            session.commit()
            return len(stale)

    def latest_scans(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ScanRun).order_by(ScanRun.started_at.desc()).limit(limit)
            ).all()
            return [self._scan_as_dict(row) for row in rows]

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self._sessions() as session:
            run = session.get(ScanRun, scan_id)
            return self._scan_as_dict(run) if run is not None else None

    def has_running_scan(self) -> bool:
        with self._sessions() as session:
            return (
                session.scalars(
                    select(ScanRun).where(ScanRun.status == "running").limit(1)
                ).first()
                is not None
            )

    @staticmethod
    def _scan_as_dict(run: ScanRun) -> dict[str, Any]:
        return {
            "scan_id": run.scan_id,
            "correlation_id": run.correlation_id,
            "trigger": run.trigger,
            "status": run.status,
            "baseline": run.baseline,
            "full_republish": run.full_republish,
            "records_read": run.records_read,
            "changes_detected": run.changes_detected,
            "changes_published": run.changes_published,
            "error_message": run.error_message,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }

    # --------------------------------------------------------- impressões

    def has_any(self, entity_type: str) -> bool:
        """Se não há nenhuma impressão digital, esta é a primeira varredura."""
        with self._sessions() as session:
            return (
                session.scalars(
                    select(SourceFingerprint)
                    .where(SourceFingerprint.entity_type == entity_type)
                    .limit(1)
                ).first()
                is not None
            )

    def diff_entities(
        self,
        entity_type: str,
        records: dict[str, dict[str, Any]],
        *,
        scan_id: str,
        correlation_id: str,
    ) -> list[EntityChange]:
        """Compara a leitura atual com a guardada, grava e devolve as mudanças.

        Marca tudo que foi visto nesta varredura. O que estava gravado e não
        apareceu agora é tratado como removido, com `removed_at` preenchido.
        """
        changes: list[EntityChange] = []
        now = _utcnow()
        with self._sessions() as session:
            stored = {
                row.entity_key: row
                for row in session.scalars(
                    select(SourceFingerprint).where(
                        SourceFingerprint.entity_type == entity_type
                    )
                ).all()
            }

            for key, record in records.items():
                digest = fingerprint(record)
                row = stored.get(key)
                if row is None:
                    session.add(
                        SourceFingerprint(
                            entity_type=entity_type,
                            entity_key=key,
                            fingerprint=digest,
                            snapshot=record,
                            first_seen_at=now,
                            last_seen_at=now,
                            last_changed_at=now,
                            last_scan_id=scan_id,
                            last_correlation_id=correlation_id,
                            removed_at=None,
                        )
                    )
                    changes.append(EntityChange(entity_type, key, "created", digest))
                    continue

                row.last_seen_at = now
                row.last_scan_id = scan_id
                reappeared = row.removed_at is not None
                if reappeared or row.fingerprint != digest:
                    row.fingerprint = digest
                    row.snapshot = record
                    row.last_changed_at = now
                    row.last_correlation_id = correlation_id
                    row.removed_at = None
                    changes.append(
                        EntityChange(
                            entity_type,
                            key,
                            "created" if reappeared else "updated",
                            digest,
                        )
                    )

            for key, row in stored.items():
                if key in records or row.removed_at is not None:
                    continue
                row.removed_at = now
                row.last_changed_at = now
                row.last_scan_id = scan_id
                row.last_correlation_id = correlation_id
                changes.append(EntityChange(entity_type, key, "removed", None))

            session.commit()
        return changes

    def mark_seen(
        self,
        entity_type: str,
        records: dict[str, dict[str, Any]],
        *,
        scan_id: str,
        correlation_id: str,
    ) -> int:
        """Grava a leitura sem produzir mudança. É o baseline silencioso."""
        self.diff_entities(
            entity_type,
            records,
            scan_id=scan_id,
            correlation_id=correlation_id,
        )
        return len(records)

    def all_keys(self, entity_type: str) -> list[str]:
        with self._sessions() as session:
            return [
                row.entity_key
                for row in session.scalars(
                    select(SourceFingerprint).where(
                        SourceFingerprint.entity_type == entity_type,
                        SourceFingerprint.removed_at.is_(None),
                    )
                ).all()
            ]

    def fingerprints(self, entity_type: str) -> dict[str, str]:
        with self._sessions() as session:
            return {
                row.entity_key: row.fingerprint
                for row in session.scalars(
                    select(SourceFingerprint).where(
                        SourceFingerprint.entity_type == entity_type
                    )
                ).all()
            }

    def snapshots(self, entity_type: str, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
        wanted = set(keys)
        with self._sessions() as session:
            return {
                row.entity_key: row.snapshot or {}
                for row in session.scalars(
                    select(SourceFingerprint).where(
                        SourceFingerprint.entity_type == entity_type
                    )
                ).all()
                if row.entity_key in wanted
            }
