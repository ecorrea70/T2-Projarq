"""Topologia AMQP do sync-service: duas caixas de correio separadas.

`sync.events` recebe os resultados do moodle-service (`moodle.#`) e
`sync.ieducar.notices` recebe os resumos de varredura do ieducar-service
(`ieducar.#`). Cada fila tem o seu próprio consumidor: a leitura da origem
feita no caminho do aviso nunca atrasa um resultado do Moodle de que a
execução em curso depende. Em uma fila só, isso seria deadlock — quem espera
o fim da execução trava a fila onde chegam os resultados que a encerram.

Quando a trava de execução única rejeita, o resumo é estacionado em
`sync.ieducar.retry`, que tem TTL e devolve a mensagem para
`integration.events` por dead-letter, preservando a routing key original. O
exchange de estacionamento é próprio (`integration.ieducar.retry`) e não o
`integration.retry` do moodle-service, cuja fila de retry assina `#` e levaria
o resumo para a fila de comandos do Moodle.

Ver ADR-007 em `docs/adr.md`.
"""
from dataclasses import dataclass

from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

COMMANDS_EXCHANGE = "integration.commands"
EVENTS_EXCHANGE = "integration.events"
NOTICES_RETRY_EXCHANGE = "integration.ieducar.retry"
DLX_EXCHANGE = "integration.dlx"

EVENTS_QUEUE = "sync.events"
EVENTS_BINDING = "moodle.#"

NOTICES_QUEUE = "sync.ieducar.notices"
NOTICES_BINDING = "ieducar.#"
NOTICES_RETRY_QUEUE = "sync.ieducar.retry"
NOTICES_DLQ_QUEUE = "sync.ieducar.dlq"
NOTICES_DLQ_ROUTING_KEY = "sync.ieducar.dlq"
NOTICES_ROUTING_KEY = "ieducar.scan.changed.v1"

DEFAULT_NOTICE_RETRY_TTL_MS = 15_000


@dataclass(frozen=True)
class SyncTopology:
    commands_exchange: AbstractExchange
    events_exchange: AbstractExchange
    notices_retry_exchange: AbstractExchange
    dlx_exchange: AbstractExchange
    events_queue: AbstractQueue
    notices_queue: AbstractQueue


async def declare_topology(
    channel: AbstractChannel,
    *,
    notice_retry_ttl_ms: int = DEFAULT_NOTICE_RETRY_TTL_MS,
) -> SyncTopology:
    commands_exchange = await channel.declare_exchange(
        COMMANDS_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    events_exchange = await channel.declare_exchange(
        EVENTS_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    notices_retry_exchange = await channel.declare_exchange(
        NOTICES_RETRY_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    dlx_exchange = await channel.declare_exchange(
        DLX_EXCHANGE, ExchangeType.DIRECT, durable=True
    )

    events_queue = await channel.declare_queue(EVENTS_QUEUE, durable=True)
    await events_queue.bind(events_exchange, routing_key=EVENTS_BINDING)

    notices_queue = await channel.declare_queue(NOTICES_QUEUE, durable=True)
    await notices_queue.bind(events_exchange, routing_key=NOTICES_BINDING)

    # Ao expirar o TTL, o resumo volta para `integration.events` preservando a
    # routing key original (`ieducar.scan.changed.v1`) e cai outra vez em
    # `sync.ieducar.notices`. Quem espera é o broker, não o nosso código.
    notices_retry_queue = await channel.declare_queue(
        NOTICES_RETRY_QUEUE,
        durable=True,
        arguments={
            "x-message-ttl": notice_retry_ttl_ms,
            "x-dead-letter-exchange": EVENTS_EXCHANGE,
        },
    )
    await notices_retry_queue.bind(notices_retry_exchange, routing_key="#")

    notices_dlq = await channel.declare_queue(NOTICES_DLQ_QUEUE, durable=True)
    await notices_dlq.bind(dlx_exchange, routing_key=NOTICES_DLQ_ROUTING_KEY)

    return SyncTopology(
        commands_exchange=commands_exchange,
        events_exchange=events_exchange,
        notices_retry_exchange=notices_retry_exchange,
        dlx_exchange=dlx_exchange,
        events_queue=events_queue,
        notices_queue=notices_queue,
    )
