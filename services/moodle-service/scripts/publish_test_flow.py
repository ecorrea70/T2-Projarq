"""Script CLI para testar o moodle-service isoladamente via RabbitMQ."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from uuid import uuid4

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message


def make_envelope(
    event_type: str,
    correlation_id: str,
    idempotency_key: str,
    payload: dict,
    message_id: str | None = None,
) -> dict:
    return {
        "message_id": message_id or str(uuid4()),
        "event_type": event_type,
        "event_version": "1.0",
        "correlation_id": correlation_id,
        "causation_id": None,
        "producer": "publish-test-flow",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": idempotency_key,
        "payload": payload,
    }


async def publish(exchange, routing_key: str, envelope: dict) -> None:
    msg = Message(
        body=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        delivery_mode=DeliveryMode.PERSISTENT,
        content_type="application/json",
        headers={"x-correlation-id": envelope["correlation_id"]},
    )
    await exchange.publish(msg, routing_key=routing_key)
    print(f"[SENT] rk={routing_key} message_id={envelope['message_id']} key={envelope['idempotency_key']}")


async def run(args: argparse.Namespace) -> None:
    connection = await aio_pika.connect_robust(args.rabbitmq_url)
    channel = await connection.channel()

    commands_exchange = await channel.declare_exchange("integration.commands", ExchangeType.TOPIC, durable=True)
    events_exchange = await channel.declare_exchange("integration.events", ExchangeType.TOPIC, durable=True)

    listener_queue = await channel.declare_queue("", exclusive=True, auto_delete=True)
    await listener_queue.bind(events_exchange, routing_key="#")

    correlation_id = args.correlation_id or str(uuid4())
    print(f"Correlation ID: {correlation_id}")

    received_events: list[dict] = []

    async def on_event(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process():
            data = json.loads(message.body.decode("utf-8"))
            if data.get("correlation_id") == correlation_id:
                received_events.append(data)
                print(
                    f"[EVENT] rk={message.routing_key} type={data.get('event_type')} "
                    f"key={data.get('idempotency_key')} payload={json.dumps(data.get('payload'), ensure_ascii=False)}"
                )

    await listener_queue.consume(on_event)

    course_idnumber = "ieducar:curso:1:2026:10:2"
    teacher_idnumber = "ieducar:professor:7"
    student_idnumber = "ieducar:aluno:1"

    if args.scenario == "happy":
        course_env = make_envelope(
            "moodle.course.sync.requested",
            correlation_id,
            f"course:{course_idnumber}",
            {
                "turma_id": 10,
                "disciplina_id": 2,
                "idnumber": course_idnumber,
                "shortname": "ieducar-1-2026-t10-d2",
                "fullname": "Matemática — Turma A (turma 10) — 2026",
            },
        )
        teacher_env = make_envelope(
            "moodle.user.sync.requested",
            correlation_id,
            f"user:{teacher_idnumber}",
            {
                "kind": "teacher",
                "origin_id": 7,
                "idnumber": teacher_idnumber,
                "username": "ieducar-professor-7",
                "firstname": "Maria",
                "lastname": "Silva",
                "email": "maria.silva.t2@example.org",
            },
        )
        student_env = make_envelope(
            "moodle.user.sync.requested",
            correlation_id,
            f"user:{student_idnumber}",
            {
                "kind": "student",
                "origin_id": 1,
                "idnumber": student_idnumber,
                "username": "ieducar-aluno-1",
                "firstname": "Ana",
                "lastname": "Oliveira",
                "email": "ana.oliveira.t2@example.org",
            },
        )
        await publish(commands_exchange, "moodle.course.sync.requested.v1", course_env)
        await publish(commands_exchange, "moodle.user.sync.requested.v1", teacher_env)
        await publish(commands_exchange, "moodle.user.sync.requested.v1", student_env)

        await asyncio.sleep(min(2.0, args.wait_seconds))

        teacher_enrol_env = make_envelope(
            "moodle.teacher.enrol.requested",
            correlation_id,
            f"teacher-enrol:{teacher_idnumber}:{course_idnumber}",
            {
                "professor_id": 7,
                "turma_id": 10,
                "disciplina_id": 2,
                "user_idnumber": teacher_idnumber,
                "course_idnumber": course_idnumber,
            },
        )
        students_enrol_env = make_envelope(
            "moodle.students.enrol.requested",
            correlation_id,
            f"students-enrol:{course_idnumber}:batch-1",
            {
                "turma_id": 10,
                "disciplina_id": 2,
                "course_idnumber": course_idnumber,
                "students": [{"aluno_id": 1, "user_idnumber": student_idnumber}],
            },
        )
        await publish(commands_exchange, "moodle.teacher.enrol.requested.v1", teacher_enrol_env)
        await publish(commands_exchange, "moodle.students.enrol.requested.v1", students_enrol_env)

    elif args.scenario == "duplicate":
        fixed_msg_id = str(uuid4())
        payload = {
            "turma_id": 10,
            "disciplina_id": 2,
            "idnumber": course_idnumber,
            "shortname": "ieducar-1-2026-t10-d2",
            "fullname": "Matemática — Turma A (turma 10) — 2026",
        }
        first = make_envelope(
            "moodle.course.sync.requested",
            correlation_id,
            f"course:{course_idnumber}",
            payload,
            message_id=fixed_msg_id,
        )
        await publish(commands_exchange, "moodle.course.sync.requested.v1", first)
        await asyncio.sleep(1.5)
        # Mesmo message_id: ignorado pelo Inbox.
        await publish(commands_exchange, "moodle.course.sync.requested.v1", first)
        # Novo message_id com mesmo idnumber: reconhecido como existente no Moodle.
        second = make_envelope(
            "moodle.course.sync.requested",
            correlation_id,
            f"course:{course_idnumber}",
            payload,
        )
        await publish(commands_exchange, "moodle.course.sync.requested.v1", second)

    elif args.scenario == "burst":
        for idx in range(1, args.count + 1):
            c_id = f"ieducar:curso:1:2026:99:{idx}"
            env = make_envelope(
                "moodle.course.sync.requested",
                correlation_id,
                f"course:{c_id}",
                {
                    "turma_id": 99,
                    "disciplina_id": idx,
                    "idnumber": c_id,
                    "shortname": f"ieducar-1-2026-t99-d{idx}",
                    "fullname": f"Disciplina Burst {idx} — Turma 99 — 2026",
                },
            )
            await publish(commands_exchange, "moodle.course.sync.requested.v1", env)

    print(f"Aguardando eventos por {args.wait_seconds}s...")
    await asyncio.sleep(args.wait_seconds)
    print(f"Total de eventos recebidos para {correlation_id}: {len(received_events)}")
    await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publica comandos de teste para o moodle-service.")
    parser.add_argument("--scenario", choices=["happy", "duplicate", "burst"], default="happy")
    parser.add_argument("--count", type=int, default=20, help="Quantidade de cursos no modo burst.")
    parser.add_argument("--rabbitmq-url", default="amqp://guest:guest@localhost:5672/")
    parser.add_argument("--correlation-id", default=None)
    parser.add_argument("--wait-seconds", type=float, default=8.0)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
