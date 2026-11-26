"""Conecta publicador de comandos (requer canal AMQP dedicado com reconexão)."""
import asyncio

import aio_pika

from app.core.config import Settings
from app.core.logging import get_logger
from app.infrastructure.messaging.publisher import CommandPublisher
from app.infrastructure.messaging.topology import declare_topology


class MessagingContext:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log = get_logger("messaging")
        self.connection = None
        self.channel = None
        self.publisher: CommandPublisher | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def is_ready(self) -> bool:
        if not self._settings.rabbitmq_enabled:
            return True
        return (
            self.connection is not None
            and not self.connection.is_closed
            and self.publisher is not None
        )

    async def start(self) -> None:
        if not self._settings.rabbitmq_enabled:
            return
        self._stopping = False
        try:
            await self._connect()
        except Exception as exc:
            self._log.warning(
                "RabbitMQ indisponível no startup do publicador (%s); reconectando em background.",
                type(exc).__name__,
            )
            self._connect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while not self._stopping and not self.is_ready:
            await asyncio.sleep(2.0)
            if self._stopping:
                break
            try:
                await self._connect()
            except Exception as exc:
                self._log.warning("Falha ao conectar publicador no RabbitMQ: %s", type(exc).__name__)

    async def _connect(self) -> None:
        self.connection = await aio_pika.connect_robust(self._settings.rabbitmq_url)
        self.channel = await self.connection.channel()
        topology = await declare_topology(
            self.channel,
            notice_retry_ttl_ms=self._settings.ieducar_notice_retry_ttl_ms,
        )
        self.publisher = CommandPublisher(topology)

    async def stop(self) -> None:
        self._stopping = True
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        if self.channel and not self.channel.is_closed:
            await self.channel.close()
        if self.connection and not self.connection.is_closed:
            await self.connection.close()
