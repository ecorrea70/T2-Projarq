"""Topologia AMQP do sync-service (publica commands, consome events)."""
from dataclasses import dataclass

from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

COMMANDS_EXCHANGE = "integration.commands"
EVENTS_EXCHANGE = "integration.events"
EVENTS_QUEUE = "sync.events"
EVENTS_BINDING = "moodle.#"


@dataclass(frozen=True)
class SyncTopology:
    commands_exchange: AbstractExchange
    events_exchange: AbstractExchange
    events_queue: AbstractQueue


async def declare_topology(channel: AbstractChannel) -> SyncTopology:
    commands_exchange = await channel.declare_exchange(
        COMMANDS_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    events_exchange = await channel.declare_exchange(
        EVENTS_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    events_queue = await channel.declare_queue(EVENTS_QUEUE, durable=True)
    await events_queue.bind(events_exchange, routing_key=EVENTS_BINDING)
    return SyncTopology(
        commands_exchange=commands_exchange,
        events_exchange=events_exchange,
        events_queue=events_queue,
    )