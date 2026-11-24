"""Consumidor RabbitMQ robusto com manual ACK e replay controlado da DLQ."""
import asyncio
import json

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel, AbstractRobustConnection

from app.application.dispatcher import MessageDispatcher
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.contracts import MoodleGateway, StateRepository
from app.infrastructure.messaging.publisher import RabbitMQPublisher
from app.infrastructure.messaging.topology import TopologyResources, declare_topology


class RabbitMQConsumer:
    """Gerencia conexão resiliente, QoS, manual ACK e replay da fila morta."""

    def __init__(
        self,
        settings: Settings,
        gateway: MoodleGateway,
        repo: StateRepository,
    ) -> None:
        self._settings = settings
        self._gateway = gateway
        self._repo = repo
        self._log = get_logger("consumer")
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._topology: TopologyResources | None = None
        self._publisher: RabbitMQPublisher | None = None
        self._dispatcher: MessageDispatcher | None = None
        self._consumer_tag: str | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def is_connected(self) -> bool:
        return self._connection is not None and not self._connection.is_closed

    @property
    def is_consuming(self) -> bool:
        return self.is_connected and self._consumer_tag is not None

    @property
    def is_ready(self) -> bool:
        return self.is_consuming

    async def start(self) -> None:
        self._stopping = False
        try:
            await self._connect_and_consume()
        except Exception as exc:
            self._log.warning(
                "RabbitMQ ainda indisponível no startup (%s); tentando reconectar em background.",
                type(exc).__name__,
            )
            self._connect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while not self._stopping and not self.is_consuming:
            await asyncio.sleep(2.0)
            if self._stopping:
                break
            try:
                await self._connect_and_consume()
            except Exception as exc:
                self._log.warning("Tentativa de conexão ao RabbitMQ falhou: %s", type(exc).__name__)

    async def _connect_and_consume(self) -> None:
        self._connection = await aio_pika.connect_robust(self._settings.rabbitmq_url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._settings.rabbitmq_prefetch_count)
        self._topology = await declare_topology(self._channel, self._settings)
        self._publisher = RabbitMQPublisher(self._topology)
        self._dispatcher = MessageDispatcher(
            self._settings,
            self._gateway,
            self._repo,
            self._publisher,
        )
        self._consumer_tag = await self._topology.commands_queue.consume(
            self._on_message,
            no_ack=False,
        )
        self._log.info("Consumidor RabbitMQ conectado e escutando moodle.commands.")

    async def stop(self) -> None:
        self._stopping = True
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        if self._topology is not None and self._consumer_tag is not None:
            try:
                await self._topology.commands_queue.cancel(self._consumer_tag)
            except Exception:
                pass
            self._consumer_tag = None
        if self._channel is not None and not self._channel.is_closed:
            await self._channel.close()
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        assert self._dispatcher is not None
        try:
            await self._dispatcher.handle_raw_message(
                raw_body=message.body,
                routing_key=message.routing_key or "",
                headers=dict(message.headers or {}),
            )
        except Exception:
            self._log.exception("Erro inesperado ao processar mensagem; reenfileirando.")
            await message.nack(requeue=True)
            return
        await message.ack()

    async def replay_dlq(self, limit: int = 50) -> int:
        """Reenfileira mensagens da moodle.commands.dlq de volta para integration.commands."""
        if not self.is_ready or self._topology is None or self._publisher is None:
            raise RuntimeError("Consumidor RabbitMQ não está conectado.")
        replayed = 0
        for _ in range(limit):
            dlq_msg = await self._topology.dlq_queue.get(fail=False, timeout=0.5)
            if dlq_msg is None:
                break
            headers = dict(dlq_msg.headers or {})
            routing_key = self._resolve_replay_routing_key(dlq_msg.body, headers)
            replay_headers = {
                k: v
                for k, v in headers.items()
                if k not in {"x-error-type", "x-error-message", "x-failed-at"}
            }
            replay_headers["x-retry-count"] = 0
            replay_headers["x-replayed"] = "true"
            await self._publisher.publish_command(
                routing_key=routing_key,
                raw_body=dlq_msg.body,
                headers=replay_headers,
            )
            await dlq_msg.ack()
            replayed += 1
        return replayed

    @staticmethod
    def _resolve_replay_routing_key(body: bytes, headers: dict) -> str:
        raw_rk = headers.get("x-original-routing-key")
        if isinstance(raw_rk, bytes):
            raw_rk = raw_rk.decode("utf-8", errors="ignore")
        if isinstance(raw_rk, str) and raw_rk.strip():
            return raw_rk.strip()
        try:
            decoded = json.loads(body.decode("utf-8"))
            event_type = str(decoded.get("event_type", "")).strip()
            if event_type:
                return event_type if event_type.endswith(".v1") else f"{event_type}.v1"
        except Exception:
            pass
        return "moodle.course.sync.requested.v1"
