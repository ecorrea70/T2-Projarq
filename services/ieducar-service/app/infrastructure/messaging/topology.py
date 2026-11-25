"""Topologia AMQP do ieducar-service: só publica, não consome.

Reusa o exchange de fatos que o moodle-service já declara (`integration.events`,
topic, durável). A declaração precisa ser idêntica, senão o RabbitMQ recusa.
A fila `sync.events` do sync-service assina apenas `moodle.#`, então estes
avisos não chegam nela até alguém acrescentar um binding `ieducar.#`.
"""
from dataclasses import dataclass

from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel, AbstractExchange

EVENTS_EXCHANGE = "integration.events"


@dataclass(frozen=True)
class IEducarTopology:
    events_exchange: AbstractExchange


async def declare_topology(channel: AbstractChannel) -> IEducarTopology:
    events_exchange = await channel.declare_exchange(
        EVENTS_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    return IEducarTopology(events_exchange=events_exchange)
