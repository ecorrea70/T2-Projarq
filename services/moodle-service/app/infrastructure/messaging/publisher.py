"""Publicador RabbitMQ para eventos de resultado, filas de retry e DLQ."""
import json
from typing import Any

from aio_pika import DeliveryMode, Message

from app.infrastructure.messaging.topology import DLQ_ROUTING_KEY, TopologyResources


class RabbitMQPublisher:
    """Publica mensagens persistentes respeitando exchanges de eventos, retry e DLQ."""

    def __init__(self, topology: TopologyResources) -> None:
        self._topology = topology

    async def publish_event(
        self,
        routing_key: str,
        payload: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers=headers or {},
        )
        await self._topology.events_exchange.publish(message, routing_key=routing_key)

    async def publish_retry(
        self,
        routing_key: str,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> None:
        message = Message(
            body=raw_body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers=headers,
        )
        await self._topology.retry_exchange.publish(message, routing_key=routing_key)

    async def publish_dlq(
        self,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> None:
        message = Message(
            body=raw_body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers=headers,
        )
        await self._topology.dlx_exchange.publish(message, routing_key=DLQ_ROUTING_KEY)

    async def publish_command(
        self,
        routing_key: str,
        raw_body: bytes,
        headers: dict[str, Any] | None = None,
    ) -> None:
        message = Message(
            body=raw_body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers=headers or {},
        )
        await self._topology.commands_exchange.publish(message, routing_key=routing_key)
