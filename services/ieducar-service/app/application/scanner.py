"""A varredura da origem: ler, comparar, gravar e avisar.

Uma varredura lê o catálogo, os professores e os alunos do i-Educar, compara
cada registro com a impressão digital guardada e, no fim, publica **um** resumo
dizendo em quais tipos de entidade houve mudança (ADR-006). A primeira
varredura de cada entidade é um baseline silencioso: ela grava as impressões e
não marca nada no resumo, para que o primeiro ciclo não dispare uma
sincronização de coisas que já existiam.
"""
import asyncio
import time
from uuid import uuid4

from app.application.fingerprint import (
    EntityChange,
    catalog_key,
    fingerprint,
    student_key,
    teacher_key,
)
from app.core.config import Settings
from app.core.errors import IEducarServiceError
from app.core.logging import get_logger, log_context
from app.core.metrics import CHANGES_DETECTED, RECORDS_READ, SCAN_DURATION, SCANS_TOTAL
from app.infrastructure.db.repository import IEducarRepository
from app.infrastructure.ieducar.client import IEducarClient
from app.infrastructure.messaging.publisher import ChangePublisher
from app.infrastructure.messaging.schemas import (
    announced_entities,
    build_scan_summary_envelope,
)

_log = get_logger("scanner")

_KEY_FUNCTIONS = {
    "catalog": catalog_key,
    "teacher": teacher_key,
    "student": student_key,
}


class SourceScanner:
    def __init__(
        self,
        settings: Settings,
        repo: IEducarRepository,
        client: IEducarClient,
        publisher: ChangePublisher | None,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._client = client
        self._publisher = publisher
        self._lock = asyncio.Lock()

    @property
    def is_scanning(self) -> bool:
        return self._lock.locked()

    async def scan(
        self,
        *,
        trigger: str,
        correlation_id: str | None = None,
        full: bool = False,
    ) -> dict:
        """Roda uma varredura. Duas varreduras nunca rodam em paralelo."""
        if self._lock.locked():
            return {"status": "busy", "reason": "Já existe uma varredura em andamento."}

        async with self._lock:
            scan_id = str(uuid4())
            corr = correlation_id or scan_id
            with log_context(correlation_id=corr):
                return await self._run(scan_id, corr, trigger, full)

    async def _run(self, scan_id: str, correlation_id: str, trigger: str, full: bool) -> dict:
        self._repo.start_scan(scan_id, correlation_id, trigger, full_republish=full)
        started = time.perf_counter()
        _log.info(
            "Varredura iniciada.",
            extra={"scan_id": scan_id, "trigger": trigger, "status": "running"},
        )
        try:
            datasets = {
                "catalog": [e.model_dump(mode="json") for e in await self._client.fetch_catalog()],
                "teacher": [t.model_dump(mode="json") for t in await self._client.fetch_teachers()],
                "student": [s.model_dump(mode="json") for s in await self._client.fetch_students()],
            }
        except IEducarServiceError as exc:
            elapsed = time.perf_counter() - started
            SCAN_DURATION.observe(elapsed)
            SCANS_TOTAL.labels(trigger=trigger, status="failed").inc()
            self._repo.finish_scan(
                scan_id,
                status="failed",
                error_message=str(exc),
            )
            _log.warning(
                "Varredura falhou ao ler a origem: %s",
                exc,
                extra={
                    "scan_id": scan_id,
                    "trigger": trigger,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )
            return {
                "status": "failed",
                "scan_id": scan_id,
                "correlation_id": correlation_id,
                "error": str(exc),
            }

        records_read = 0
        changes_detected = 0
        changes_published = 0
        baseline_entities: list[str] = []
        by_entity: dict[str, dict] = {}

        for entity_type, rows in datasets.items():
            key_of = _KEY_FUNCTIONS[entity_type]
            records = {key_of(row): row for row in rows}
            records_read += len(records)
            RECORDS_READ.labels(entity_type=entity_type).inc(len(records))

            was_baseline = not self._repo.has_any(entity_type)
            changes = self._repo.diff_entities(
                entity_type,
                records,
                scan_id=scan_id,
                correlation_id=correlation_id,
            )
            changes_detected += len(changes)

            if full:
                to_publish = [
                    change
                    for change in changes
                    if change.change_type == "removed"
                ] + [
                    _republish_change(entity_type, key, records)
                    for key in records
                ]
            elif was_baseline:
                to_publish = []
                baseline_entities.append(entity_type)
                _log.info(
                    "Baseline de %s gravado com %d registros; nenhum aviso publicado.",
                    entity_type,
                    len(records),
                    extra={
                        "scan_id": scan_id,
                        "entity_type": entity_type,
                        "records_read": len(records),
                        "status": "baseline",
                    },
                )
            else:
                to_publish = list(changes)

            for change in changes:
                CHANGES_DETECTED.labels(
                    entity_type=entity_type, change_type=change.change_type
                ).inc()

            # `changes_published` conta as mudanças anunciadas no resumo, não
            # mensagens: o resumo é uma única mensagem por varredura.
            announced = len(to_publish)
            changes_published += announced
            by_entity[entity_type] = {
                "records_read": len(records),
                "changes_detected": len(changes),
                "changes_published": announced,
                "baseline": was_baseline and not full,
            }

        summary_published = await self._publish_summary(
            scan_id, correlation_id, trigger, by_entity, full
        )

        elapsed = time.perf_counter() - started
        SCAN_DURATION.observe(elapsed)
        SCANS_TOTAL.labels(trigger=trigger, status="succeeded").inc()
        self._repo.finish_scan(
            scan_id,
            status="succeeded",
            baseline=bool(baseline_entities),
            records_read=records_read,
            changes_detected=changes_detected,
            changes_published=changes_published,
        )
        _log.info(
            "Varredura concluída: %d registros lidos, %d mudanças, %d anunciadas.",
            records_read,
            changes_detected,
            changes_published,
            extra={
                "scan_id": scan_id,
                "trigger": trigger,
                "status": "succeeded",
                "records_read": records_read,
                "changes_published": changes_published,
                "summary_published": summary_published,
                "duration_ms": round(elapsed * 1000, 2),
            },
        )
        return {
            "status": "succeeded",
            "scan_id": scan_id,
            "correlation_id": correlation_id,
            "trigger": trigger,
            "baseline": baseline_entities,
            "records_read": records_read,
            "changes_detected": changes_detected,
            "changes_published": changes_published,
            "summary_published": summary_published,
            "by_entity": by_entity,
        }

    async def _publish_summary(
        self,
        scan_id: str,
        correlation_id: str,
        trigger: str,
        by_entity: dict[str, dict],
        full: bool,
    ) -> bool:
        """Uma mensagem por varredura, e só quando algum tipo mudou (ADR-006)."""
        if self._publisher is None:
            return False
        mudaram = announced_entities(by_entity)
        if not mudaram:
            return False
        envelope = build_scan_summary_envelope(
            scan_id=scan_id,
            trigger=trigger,
            correlation_id=correlation_id,
            by_entity=by_entity,
            full_republish=full,
        )
        await self._publisher.publish_scan_summary(envelope)
        _log.info(
            "Resumo de varredura publicado.",
            extra={
                "scan_id": scan_id,
                "trigger": trigger,
                "entities_changed": mudaram,
                "summary_published": True,
            },
        )
        return True


def _republish_change(entity_type: str, key: str, records: dict) -> EntityChange:
    """No republish total, todo registro vigente vira um aviso `updated`."""
    return EntityChange(entity_type, key, "updated", fingerprint(records[key]))


async def scan_loop(scanner: SourceScanner, settings: Settings) -> None:
    """Laço de fundo: varre a cada `SCAN_INTERVAL_SECONDS`."""
    if settings.scan_on_startup:
        await _safe_scan(scanner, trigger="startup")
    while True:
        await asyncio.sleep(settings.scan_interval_seconds)
        await _safe_scan(scanner, trigger="schedule")


async def _safe_scan(scanner: SourceScanner, *, trigger: str) -> None:
    """O laço nunca morre por uma varredura ruim: a próxima tenta de novo."""
    try:
        await scanner.scan(trigger=trigger)
    except asyncio.CancelledError:
        raise
    except Exception:
        _log.exception("Varredura automática falhou de forma inesperada.")
