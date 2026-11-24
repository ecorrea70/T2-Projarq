"""Conecta publicador de comandos (requer canal AMQP dedicado)."""
import aio_pika

from app.core.config import Settings
from app.infrastructure.messaging.publisher import CommandPublisher
from app.infrastructure.messaging.topology import declare_topology


class MessagingContext:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.connection = None
        self.channel = None
        self.publisher: CommandPublisher | None = None

    async def start(self) -> None:
        if not self._settings.rabbitmq_enabled:
            return
        self.connection = await aio_pika.connect_robust(self._settings.rabbitmq_url)
        self.channel = await self.connection.channel()
        topology = await declare_topology(self.channel)
        self.publisher = CommandPublisher(topology)

    async def stop(self) -> None:
        if self.channel and not self.channel.is_closed:
            await self.channel.close()
        if self.connection and not self.connection.is_closed:
            await self.connection.close()