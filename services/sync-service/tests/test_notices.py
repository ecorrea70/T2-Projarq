"""O caminho automático: resumo de varredura → execuções, com estacionamento."""
import json

from app.application.notice_handler import (
    ScanNoticeHandler,
    kinds_due,
    parked_payload,
    run_correlation_id,
)
from app.core.config import Settings
from app.infrastructure.messaging import topology as topo
from app.infrastructure.messaging.notices_consumer import (
    ATTEMPTS_HEADER,
    ScanNoticesConsumer,
    attempts_of,
)
from app.infrastructure.messaging.topology import SyncTopology

ROTAS = {
    "catalog": "/internal/catalog",
    "teacher": "/internal/teachers",
    "student": "/internal/students",
}


def resumo(
    *,
    catalog: bool = False,
    teacher: bool = False,
    student: bool = False,
    scan_id: str = "11111111-2222-3333-4444-555555555555",
    trigger: str = "schedule",
    **extra,
) -> dict:
    mudou = {"catalog": catalog, "teacher": teacher, "student": student}
    entities = {
        entity_type: {
            "changed": flag,
            "changes_detected": 1 if flag else 0,
            "fetch_from": ROTAS[entity_type],
        }
        for entity_type, flag in mudou.items()
    }
    return {
        "message_id": "msg-1",
        "event_type": "ieducar.scan.changed",
        "event_version": "1.0",
        "correlation_id": scan_id,
        "causation_id": scan_id,
        "producer": "ieducar-service",
        "idempotency_key": f"ieducar:scan:{scan_id}",
        "payload": {
            "scan_id": scan_id,
            "trigger": trigger,
            "full_republish": False,
            "entities": entities,
            **extra,
        },
    }


class StubCoordinator:
    """Devolve os status combinados, sem tocar banco nem origem."""

    def __init__(self, *results: dict) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, str]] = []

    async def start_teachers(self, correlation_id: str | None = None) -> dict:
        return self._next("teachers", correlation_id)

    async def start_students(self, correlation_id: str | None = None) -> dict:
        return self._next("students", correlation_id)

    def _next(self, kind: str, correlation_id: str | None) -> dict:
        self.calls.append((kind, str(correlation_id)))
        if self._results:
            return self._results.pop(0)
        return {"status": "accepted", "correlation_id": correlation_id}


class FakeExchange:
    def __init__(self, name: str) -> None:
        self.name = name
        self.published: list[tuple[str, object]] = []

    async def publish(self, message, routing_key: str) -> None:
        self.published.append((routing_key, message))


class FakeQueue:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeMessage:
    def __init__(self, body: bytes, headers: dict | None = None, routing_key: str | None = None) -> None:
        self.body = body
        self.headers = headers or {}
        self.routing_key = routing_key or topo.NOTICES_ROUTING_KEY
        self.acked = False
        self.requeued: bool | None = None

    async def ack(self) -> None:
        self.acked = True

    async def nack(self, requeue: bool = False) -> None:
        self.requeued = requeue


def build_consumer(coordinator, **overrides) -> tuple[ScanNoticesConsumer, SyncTopology]:
    settings = Settings(
        database_url="sqlite:///:memory:",
        rabbitmq_enabled=False,
        ieducar_source="mock",
        **overrides,
    )
    consumer = ScanNoticesConsumer(settings, ScanNoticeHandler(coordinator))
    topology = SyncTopology(
        commands_exchange=FakeExchange(topo.COMMANDS_EXCHANGE),
        events_exchange=FakeExchange(topo.EVENTS_EXCHANGE),
        notices_retry_exchange=FakeExchange(topo.NOTICES_RETRY_EXCHANGE),
        dlx_exchange=FakeExchange(topo.DLX_EXCHANGE),
        events_queue=FakeQueue(topo.EVENTS_QUEUE),
        notices_queue=FakeQueue(topo.NOTICES_QUEUE),
    )
    consumer._topology = topology
    return consumer, topology


class TestDuasCaixasDeCorreio:
    def test_fila_de_resultados_nao_assina_os_avisos(self) -> None:
        assert topo.EVENTS_BINDING == "moodle.#"
        assert topo.NOTICES_BINDING == "ieducar.#"
        assert topo.EVENTS_QUEUE != topo.NOTICES_QUEUE

    def test_estacionamento_nao_usa_o_retry_do_moodle_service(self) -> None:
        # `moodle.commands.retry` assina `#` no `integration.retry`: um resumo
        # publicado lá viraria comando na fila do Moodle.
        assert topo.NOTICES_RETRY_EXCHANGE != "integration.retry"
        assert topo.NOTICES_RETRY_QUEUE != "moodle.commands.retry"


class TestKindsDue:
    def test_mudanca_em_professor_pede_so_professores(self) -> None:
        assert kinds_due(resumo(teacher=True)["payload"]) == ["teachers"]

    def test_mudanca_em_aluno_pede_so_alunos(self) -> None:
        assert kinds_due(resumo(student=True)["payload"]) == ["students"]

    def test_mudanca_em_catalogo_pede_as_duas(self) -> None:
        assert kinds_due(resumo(catalog=True)["payload"]) == ["teachers", "students"]

    def test_resumo_sem_mudanca_nao_pede_nada(self) -> None:
        assert kinds_due(resumo()["payload"]) == []

    def test_ordem_e_estavel_independente_do_resumo(self) -> None:
        assert kinds_due(resumo(student=True, teacher=True)["payload"]) == [
            "teachers",
            "students",
        ]

    def test_mensagem_estacionada_manda_no_que_falta(self) -> None:
        payload = resumo(catalog=True, pending_kinds=["students"])["payload"]
        assert kinds_due(payload) == ["students"]

    def test_payload_estacionado_guarda_so_o_que_falta(self) -> None:
        original = resumo(catalog=True)["payload"]
        estacionado = parked_payload(original, ["students"])

        assert estacionado["pending_kinds"] == ["students"]
        assert estacionado["scan_id"] == original["scan_id"]
        assert kinds_due(estacionado) == ["students"]


class TestCorrelationId:
    def test_vem_da_varredura_e_do_tipo(self) -> None:
        assert run_correlation_id("abc", "teachers", 0) == "scan-abc-teachers"

    def test_tentativa_entra_no_id_para_nao_duplicar_run(self) -> None:
        assert run_correlation_id("abc", "students", 2) == "scan-abc-students-r2"

    def test_nunca_passa_do_limite_da_coluna(self) -> None:
        gerado = run_correlation_id("x" * 120, "teachers", 7)
        assert len(gerado) <= 64


class TestHandler:
    async def test_aviso_de_professor_dispara_so_professores(self) -> None:
        coordinator = StubCoordinator()
        outcome = await ScanNoticeHandler(coordinator).handle(resumo(teacher=True))

        assert outcome.started == ["teachers"]
        assert outcome.pending == []
        assert outcome.should_park is False
        assert [kind for kind, _ in coordinator.calls] == ["teachers"]

    async def test_aviso_de_catalogo_dispara_as_duas(self) -> None:
        coordinator = StubCoordinator()
        outcome = await ScanNoticeHandler(coordinator).handle(resumo(catalog=True))

        assert outcome.started == ["teachers", "students"]
        assert [kind for kind, _ in coordinator.calls] == ["teachers", "students"]

    async def test_conflito_na_primeira_devolve_tudo_como_pendente(self) -> None:
        coordinator = StubCoordinator({"status": "conflict", "active": {}})
        outcome = await ScanNoticeHandler(coordinator).handle(resumo(catalog=True))

        assert outcome.started == []
        assert outcome.pending == ["teachers", "students"]
        assert outcome.should_park is True
        # Para na primeira rejeição: não insiste no que já está travado.
        assert len(coordinator.calls) == 1

    async def test_primeira_aceita_e_segunda_conflita_deixa_so_a_segunda_pendente(self) -> None:
        coordinator = StubCoordinator(
            {"status": "accepted"}, {"status": "conflict", "active": {}}
        )
        outcome = await ScanNoticeHandler(coordinator).handle(resumo(catalog=True))

        assert outcome.started == ["teachers"]
        assert outcome.pending == ["students"]

    async def test_origem_indisponivel_tambem_estaciona(self) -> None:
        coordinator = StubCoordinator({"status": "origin_unavailable", "reason": "fora"})
        outcome = await ScanNoticeHandler(coordinator).handle(resumo(student=True))

        assert outcome.pending == ["students"]
        assert outcome.should_park is True

    async def test_rejeitado_e_descartado_sem_estacionar(self) -> None:
        coordinator = StubCoordinator({"status": "rejected", "reason": "origem vazia"})
        outcome = await ScanNoticeHandler(coordinator).handle(resumo(teacher=True))

        assert outcome.started == []
        assert outcome.pending == []
        assert outcome.discarded == {"teachers": "rejected"}
        assert outcome.should_park is False

    async def test_entrega_repetida_e_descartada_como_duplicata(self) -> None:
        coordinator = StubCoordinator({"status": "duplicate_correlation_id", "reason": "já existe"})
        outcome = await ScanNoticeHandler(coordinator).handle(resumo(teacher=True))

        assert outcome.discarded == {"teachers": "duplicate_correlation_id"}
        assert outcome.should_park is False

    async def test_resumo_sem_mudanca_nao_chama_o_coordenador(self) -> None:
        coordinator = StubCoordinator()
        outcome = await ScanNoticeHandler(coordinator).handle(resumo())

        assert outcome.started == []
        assert coordinator.calls == []

    async def test_tentativa_muda_o_correlation_id_da_run(self) -> None:
        coordinator = StubCoordinator()
        aviso = resumo(teacher=True, scan_id="scan-7")
        await ScanNoticeHandler(coordinator).handle(aviso, attempt=3)

        assert coordinator.calls == [("teachers", "scan-scan-7-teachers-r3")]


class TestConsumidor:
    async def test_pendente_volta_para_o_retry_com_so_o_que_falta(self) -> None:
        coordinator = StubCoordinator(
            {"status": "accepted"}, {"status": "conflict", "active": {}}
        )
        consumer, topology = build_consumer(coordinator)
        aviso = resumo(catalog=True)
        message = FakeMessage(json.dumps(aviso).encode("utf-8"))

        await consumer._on_message(message)

        assert message.acked is True
        assert message.requeued is None
        assert len(topology.notices_retry_exchange.published) == 1
        routing_key, parked = topology.notices_retry_exchange.published[0]
        assert routing_key == topo.NOTICES_ROUTING_KEY
        corpo = json.loads(parked.body.decode("utf-8"))
        assert corpo["payload"]["pending_kinds"] == ["students"]
        assert parked.headers[ATTEMPTS_HEADER] == 1
        assert topology.dlx_exchange.published == []

    async def test_contador_de_tentativas_sobe_a_cada_estacionamento(self) -> None:
        coordinator = StubCoordinator({"status": "conflict", "active": {}})
        consumer, topology = build_consumer(coordinator)
        message = FakeMessage(
            json.dumps(resumo(student=True)).encode("utf-8"),
            headers={ATTEMPTS_HEADER: 4},
        )

        await consumer._on_message(message)

        _, parked = topology.notices_retry_exchange.published[0]
        assert parked.headers[ATTEMPTS_HEADER] == 5

    async def test_excedido_o_limite_vai_para_a_dlq(self) -> None:
        coordinator = StubCoordinator({"status": "conflict", "active": {}})
        consumer, topology = build_consumer(coordinator, ieducar_notice_max_attempts=2)
        message = FakeMessage(
            json.dumps(resumo(student=True)).encode("utf-8"),
            headers={ATTEMPTS_HEADER: 2},
        )

        await consumer._on_message(message)

        assert topology.notices_retry_exchange.published == []
        assert len(topology.dlx_exchange.published) == 1
        routing_key, _ = topology.dlx_exchange.published[0]
        assert routing_key == topo.NOTICES_DLQ_ROUTING_KEY
        assert message.acked is True

    async def test_resumo_aceito_nao_estaciona_nada(self) -> None:
        consumer, topology = build_consumer(StubCoordinator())
        message = FakeMessage(json.dumps(resumo(teacher=True)).encode("utf-8"))

        await consumer._on_message(message)

        assert topology.notices_retry_exchange.published == []
        assert topology.dlx_exchange.published == []
        assert message.acked is True

    async def test_mensagem_ilegivel_recebe_ack_e_nao_volta_para_a_fila(self) -> None:
        consumer, topology = build_consumer(StubCoordinator())
        message = FakeMessage(b"isso nao e json")

        await consumer._on_message(message)

        assert message.acked is True
        assert message.requeued is None
        assert topology.notices_retry_exchange.published == []

    async def test_falha_inesperada_reenfileira(self) -> None:
        class Explodindo:
            async def handle(self, envelope, *, attempt=0):
                raise RuntimeError("boom")

        settings = Settings(
            database_url="sqlite:///:memory:", rabbitmq_enabled=False, ieducar_source="mock"
        )
        consumer = ScanNoticesConsumer(settings, Explodindo())
        message = FakeMessage(json.dumps(resumo(teacher=True)).encode("utf-8"))

        await consumer._on_message(message)

        assert message.requeued is True
        assert message.acked is False

    def test_header_de_tentativas_tolera_lixo(self) -> None:
        assert attempts_of(FakeMessage(b"{}", headers={})) == 0
        assert attempts_of(FakeMessage(b"{}", headers={ATTEMPTS_HEADER: "nao-numero"})) == 0
        assert attempts_of(FakeMessage(b"{}", headers={ATTEMPTS_HEADER: 3})) == 3
