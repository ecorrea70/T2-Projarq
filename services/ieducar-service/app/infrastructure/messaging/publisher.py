"""Publicador dos avisos de mudança em integration.events."""
import json

from aio_pika import DeliveryMode, Message

from app.core.metrics import CHANGES_PUBLISHED
from app.infrastructure.messaging.schemas import MessageEnvelope, routing_key_for
from app.infrastructure.messaging.topology import IEducarTopology


class ChangePublisher:
    def __init__(self, topology: IEducarTopology) -> None:
        self._topology = topology

    async def publish_change(self, envelope: MessageEnvelope) -> str:
        entity_type = str(envelope.payload["entity_type"])
        change_type = str(envelope.payload["change_type"])
        body = envelope.model_dump(mode="json")
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        message = Message(
            body=payload,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers={"x-correlation-id": envelope.correlation_id},
        )
        await self._topology.events_exchange.publish(
            message, routing_key=routing_key_for(entity_type)
        )
        CHANGES_PUBLISHED.labels(entity_type=entity_type, change_type=change_type).inc()
        return envelope.message_id
