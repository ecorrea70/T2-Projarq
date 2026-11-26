"""Consumidor de integration.events (respostas do moodle-service)."""
import asyncio
import json

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel, AbstractRobustConnection

from app.application.sync_coordinator import SyncCoordinator
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.metrics import MOODLE_EVENTS_HANDLED
from app.infrastructure.messaging.topology import SyncTopology, declare_topology


class MoodleEventsConsumer:
    def __init__(self, settings: Settings, coordinator: SyncCoordinator) -> None:
        self._settings = settings
        self._coordinator = coordinator
        self._log = get_logger("events_consumer")
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._topology: SyncTopology | None = None
        self._consumer_tag: str | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def is_ready(self) -> bool:
        return self._connection is not None and not self._connection.is_closed and self._consumer_tag is not None

    async def start(self) -> None:
        if not self._settings.rabbitmq_enabled:
            return
        self._stopping = False
        try:
            await self._connect_and_consume()
        except Exception as exc:
            self._log.warning("RabbitMQ indisponível no startup (%s); reconectando.", type(exc).__name__)
            self._connect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while not self._stopping and not self.is_ready:
            await asyncio.sleep(2.0)
            if self._stopping:
                break
            try:
                await self._connect_and_consume()
            except Exception as exc:
                self._log.warning("Falha ao conectar no RabbitMQ: %s", type(exc).__name__)

    async def _connect_and_consume(self) -> None:
        self._connection = await aio_pika.connect_robust(self._settings.rabbitmq_url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._settings.rabbitmq_prefetch_count)
        self._topology = await declare_topology(
            self._channel,
            notice_retry_ttl_ms=self._settings.ieducar_notice_retry_ttl_ms,
        )
        self._consumer_tag = await self._topology.events_queue.consume(self._on_message, no_ack=False)
        self._log.info("Consumidor de eventos conectado em sync.events.")

    async def stop(self) -> None:
        self._stopping = True
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        if self._topology and self._consumer_tag:
            try:
                await self._topology.events_queue.cancel(self._consumer_tag)
            except Exception:
                pass
            self._consumer_tag = None
        if self._channel and not self._channel.is_closed:
            await self._channel.close()
        if self._connection and not self._connection.is_closed:
            await self._connection.close()

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        try:
            data = json.loads(message.body.decode("utf-8"))
            event_type = str(data.get("event_type", message.routing_key or "unknown"))
            await self._coordinator.handle_moodle_event(data)
            MOODLE_EVENTS_HANDLED.labels(event_type=event_type, outcome="processed").inc()
        except Exception:
            self._log.exception("Falha ao processar evento do Moodle; reenfileirando.")
            await message.nack(requeue=True)
            return
        await message.ack()