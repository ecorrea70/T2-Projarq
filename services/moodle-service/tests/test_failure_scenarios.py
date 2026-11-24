"""Testes dedicados aos Cenários de Falha 2, 3 e 4 do T2."""
import json
from urllib.parse import parse_qs

import httpx

from app.application.dispatcher import MessageDispatcher
from app.infrastructure.moodle.circuit_breaker import CircuitBreaker
from app.infrastructure.moodle.client import MoodleClient
from tests.conftest import make_envelope_dict


def _course_envelope(correlation_id: str | None = None, message_id: str | None = None) -> dict:
    return make_envelope_dict(
        "moodle.course.sync.requested",
        {
            "turma_id": 10,
            "disciplina_id": 2,
            "idnumber": "ieducar:curso:1:2026:10:2",
            "shortname": "ieducar-1-2026-t10-d2",
            "fullname": "Matemática — Turma A (turma 10) — 2026",
        },
        message_id=message_id,
        correlation_id=correlation_id,
        idempotency_key="course:ieducar:curso:1:2026:10:2",
    )


class TestFailureScenarios:
    async def test_scenario_2_moodle_down_opens_circuit_retries_and_routes_to_dlq(
        self, settings, repo, publisher
    ) -> None:
        """Cenário 2: Moodle fora do ar -> abre CB -> filas de retry -> DLQ sem bloquear fluxo."""
        def down_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        cb = CircuitBreaker(failure_threshold=2, recovery_seconds=60.0)
        client = MoodleClient(
            settings,
            circuit_breaker=cb,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(down_handler)),
        )
        dispatcher = MessageDispatcher(settings, client, repo, publisher)

        corr_id = "corr-scenario-2"
        env = _course_envelope(correlation_id=corr_id)
        raw = json.dumps(env).encode()
        rk = "moodle.course.sync.requested.v1"

        # Entregas 1, 2 e 3 vão para retry com incremento de x-retry-count.
        out1 = await dispatcher.handle_raw_message(raw, rk, headers={})
        assert out1.action == "retried"
        assert cb.state == "open"
        assert publisher.retries[-1][0] == rk
        assert publisher.retries[-1][2]["x-retry-count"] == 1

        out2 = await dispatcher.handle_raw_message(raw, rk, headers=publisher.retries[-1][2])
        assert out2.action == "retried"
        assert publisher.retries[-1][2]["x-retry-count"] == 2

        out3 = await dispatcher.handle_raw_message(raw, rk, headers=publisher.retries[-1][2])
        assert out3.action == "retried"
        assert publisher.retries[-1][2]["x-retry-count"] == 3

        # Entrega 4 atinge rabbitmq_max_retries=3 e vai para DLQ + publica evento de falha.
        out4 = await dispatcher.handle_raw_message(raw, rk, headers=publisher.retries[-1][2])
        assert out4.action == "dlq"
        assert len(publisher.dlq) == 1
        assert publisher.events[-1][0] == "moodle.course.sync.failed.v1"

        summary = repo.get_run_summary(corr_id)
        assert summary["summary"]["dead_lettered"] == 1
        await client.aclose()

    async def test_scenario_3_http_response_lost_after_course_create_reconciles_without_duplicate(
        self, settings, repo, publisher
    ) -> None:
        """Cenário 3: Moodle cria curso mas resposta HTTP cai -> retry local reconcilia sem duplicar."""
        stateful_courses: list[dict] = []

        def moodle_handler(request: httpx.Request) -> httpx.Response:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            fn = form["wsfunction"]
            if fn == "core_course_get_courses_by_field":
                field = form["field"]
                value = form["value"]
                matched = [c for c in stateful_courses if str(c.get(field)) == str(value)]
                return httpx.Response(200, json={"courses": matched, "warnings": []})
            if fn == "core_course_create_courses":
                new_id = 20 + len(stateful_courses)
                stateful_courses.append(
                    {
                        "id": new_id,
                        "idnumber": form["courses[0][idnumber]"],
                        "shortname": form["courses[0][shortname]"],
                        "fullname": form["courses[0][fullname]"],
                    }
                )
                return httpx.Response(200, json=[{"id": new_id}])
            return httpx.Response(200, json={})

        cfg = settings.model_copy(
            update={
                "fault_inject_drop_after_course_create": True,
                "fault_inject_target_idnumber": "ieducar:curso:1:2026:10:2",
            }
        )
        client = MoodleClient(
            cfg,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(moodle_handler)),
        )
        dispatcher = MessageDispatcher(cfg, client, repo, publisher)

        corr_id = "corr-scenario-3"
        env = _course_envelope(correlation_id=corr_id)
        outcome = await dispatcher.handle_raw_message(
            json.dumps(env).encode(), "moodle.course.sync.requested.v1"
        )

        assert outcome.action == "ack"
        assert outcome.reconciled is True
        assert outcome.result["moodle_id"] == 20
        assert len(stateful_courses) == 1  # Nunca duplicou no Moodle!

        entity = repo.get_entity_by_idnumber("ieducar:curso:1:2026:10:2")
        assert entity is not None
        assert entity.last_sync_status == "reconciled"

        summary = repo.get_run_summary(corr_id)
        assert summary["summary"]["succeeded"] == 1
        assert summary["summary"]["reconciled"] == 1
        assert publisher.events[-1][0] == "moodle.course.synced.v1"
        assert publisher.events[-1][1]["payload"]["reconciled"] is True
        await client.aclose()

    async def test_scenario_4_service_crash_mid_flight_reconciles_on_redelivery(
        self, settings, gateway, repo, publisher
    ) -> None:
        """Cenário 4: Serviço reinicia antes do ACK -> mensagem reentregue reconcilia sem duplicar."""
        corr_id = "corr-scenario-4"
        msg_id = "msg-in-flight-1"
        env = _course_envelope(correlation_id=corr_id, message_id=msg_id)

        # Simula estado deixado pelo container morto: Inbox em "processing" e curso já gravado no Moodle.
        repo.claim_message(
            message_id=msg_id,
            event_type="moodle.course.sync.requested",
            event_version="1.0",
            correlation_id=corr_id,
            idempotency_key=env["idempotency_key"],
            payload_hash="hash",
        )
        gateway.courses.append(
            {
                "id": 20,
                "idnumber": "ieducar:curso:1:2026:10:2",
                "shortname": "ieducar-1-2026-t10-d2",
            }
        )

        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)
        outcome = await dispatcher.handle_raw_message(
            json.dumps(env).encode(), "moodle.course.sync.requested.v1"
        )

        assert outcome.action == "ack"
        assert outcome.reconciled is True
        assert len(gateway.courses) == 1

        summary = repo.get_run_summary(corr_id)
        assert summary["summary"]["succeeded"] == 1
        assert summary["messages"][0]["attempts"] == 2
        assert summary["messages"][0]["reconciled"] is True
