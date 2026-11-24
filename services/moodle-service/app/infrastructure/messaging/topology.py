"""Declaração idempotente da topologia RabbitMQ do moodle-service (3 filas)."""
from dataclasses import dataclass

from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

from app.core.config import Settings

COMMANDS_EXCHANGE = "integration.commands"
EVENTS_EXCHANGE = "integration.events"
RETRY_EXCHANGE = "integration.retry"
DLX_EXCHANGE = "integration.dlx"

COMMANDS_QUEUE = "moodle.commands"
RETRY_QUEUE = "moodle.commands.retry"
DLQ_QUEUE = "moodle.commands.dlq"

COMMANDS_BINDING_KEY = "moodle.#.requested.v1"
DLQ_ROUTING_KEY = "moodle.commands.dlq"


@dataclass(frozen=True)
class TopologyResources:
    commands_exchange: AbstractExchange
    events_exchange: AbstractExchange
    retry_exchange: AbstractExchange
    dlx_exchange: AbstractExchange
    commands_queue: AbstractQueue
    retry_queue: AbstractQueue
    dlq_queue: AbstractQueue


async def declare_topology(channel: AbstractChannel, settings: Settings) -> TopologyResources:
    """Declara exchanges, fila principal, fila de retry com TTL e DLQ."""
    commands_exchange = await channel.declare_exchange(
        COMMANDS_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    events_exchange = await channel.declare_exchange(
        EVENTS_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    retry_exchange = await channel.declare_exchange(
        RETRY_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    dlx_exchange = await channel.declare_exchange(
        DLX_EXCHANGE, ExchangeType.DIRECT, durable=True
    )

    commands_queue = await channel.declare_queue(
        COMMANDS_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": DLX_EXCHANGE,
            "x-dead-letter-routing-key": DLQ_ROUTING_KEY,
        },
    )
    await commands_queue.bind(commands_exchange, routing_key=COMMANDS_BINDING_KEY)

    # Ao expirar o TTL na fila de retry, a mensagem retorna ao exchange principal
    # preservando a routing key original (ex.: moodle.course.sync.requested.v1).
    retry_queue = await channel.declare_queue(
        RETRY_QUEUE,
        durable=True,
        arguments={
            "x-message-ttl": settings.rabbitmq_retry_ttl_ms,
            "x-dead-letter-exchange": COMMANDS_EXCHANGE,
        },
    )
    await retry_queue.bind(retry_exchange, routing_key="#")

    dlq_queue = await channel.declare_queue(DLQ_QUEUE, durable=True)
    await dlq_queue.bind(dlx_exchange, routing_key=DLQ_ROUTING_KEY)

    return TopologyResources(
        commands_exchange=commands_exchange,
        events_exchange=events_exchange,
        retry_exchange=retry_exchange,
        dlx_exchange=dlx_exchange,
        commands_queue=commands_queue,
        retry_queue=retry_queue,
        dlq_queue=dlq_queue,
    )
