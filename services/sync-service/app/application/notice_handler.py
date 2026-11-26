"""Traduz um resumo de varredura do ieducar-service em execuções de sincronização.

O resumo não diz quais registros mudaram, só em quais tipos de entidade houve
mudança (ADR-006). A decisão que sai daqui é quais execuções disparar: mudança
em professores pede a sincronização de professores, em alunos a de alunos, e em
catálogo as **duas**, porque `plan_teacher_sync` cria os cursos de todo o
catálogo e `plan_student_sync` indexa as matrículas pelo mesmo catálogo.

Nada aqui espera o fim de uma execução. Quando a trava de execução única
rejeita, o que ainda falta volta ao chamador em `pending`, para ser estacionado
na fila de retry (ADR-007).
"""
from dataclasses import dataclass, field

from app.application.sync_coordinator import SyncCoordinator
from app.core.logging import get_logger, log_context

_log = get_logger("notice_handler")

KIND_ORDER = ("teachers", "students")

ENTITY_KINDS = {
    "teacher": ("teachers",),
    "student": ("students",),
    "catalog": ("teachers", "students"),
}

# A origem fora do ar é transitória e a trava libera quando a execução fecha:
# os dois casos esperam na fila de retry em vez de virar trabalho perdido.
PARK_STATUSES = ("conflict", "origin_unavailable")


@dataclass(frozen=True)
class NoticeOutcome:
    started: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    discarded: dict[str, str] = field(default_factory=dict)

    @property
    def should_park(self) -> bool:
        return bool(self.pending)


def kinds_due(payload: dict) -> list[str]:
    """Quais execuções o resumo está devendo, em ordem estável."""
    estacionado = payload.get("pending_kinds")
    if estacionado:
        return [kind for kind in KIND_ORDER if kind in estacionado]
    devidos: set[str] = set()
    for entity_type, summary in (payload.get("entities") or {}).items():
        if isinstance(summary, dict) and summary.get("changed"):
            devidos.update(ENTITY_KINDS.get(entity_type, ()))
    return [kind for kind in KIND_ORDER if kind in devidos]


def parked_payload(payload: dict, pending: list[str]) -> dict:
    """O que volta para a fila carrega só o que ainda falta, não o resumo todo."""
    return {**payload, "pending_kinds": list(pending)}


def run_correlation_id(scan_id: str, kind: str, attempt: int) -> str:
    """Correlation id derivado da varredura: redundância de entrega não duplica run."""
    base = f"scan-{scan_id or 'desconhecido'}-{kind}"
    if attempt:
        base = f"{base}-r{attempt}"
    return base[:64]


class ScanNoticeHandler:
    def __init__(self, coordinator: SyncCoordinator) -> None:
        self._coordinator = coordinator

    async def handle(self, envelope: dict, *, attempt: int = 0) -> NoticeOutcome:
        payload = envelope.get("payload") or {}
        scan_id = str(payload.get("scan_id") or "")
        devidos = kinds_due(payload)
        if not devidos:
            return NoticeOutcome()

        started: list[str] = []
        discarded: dict[str, str] = {}
        with log_context(correlation_id=envelope.get("correlation_id")):
            for posicao, kind in enumerate(devidos):
                correlation_id = run_correlation_id(scan_id, kind, attempt)
                result = await self._start(kind, correlation_id)
                status = str(result.get("status"))

                if status == "accepted":
                    started.append(kind)
                    continue

                if status in PARK_STATUSES:
                    faltando = devidos[posicao:]
                    _log.info(
                        "Resumo estacionado: ainda devido %s.",
                        ", ".join(faltando),
                        extra={"kind": kind, "status": status},
                    )
                    return NoticeOutcome(
                        started=started, pending=faltando, discarded=discarded
                    )

                discarded[kind] = status
                _log.warning(
                    "Aviso descartado para %s: %s.",
                    kind,
                    result.get("reason") or status,
                    extra={"kind": kind, "status": status},
                )

        return NoticeOutcome(started=started, discarded=discarded)

    async def _start(self, kind: str, correlation_id: str) -> dict:
        if kind == "teachers":
            return await self._coordinator.start_teachers(correlation_id)
        return await self._coordinator.start_students(correlation_id)
