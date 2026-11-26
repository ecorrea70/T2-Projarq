"""Publicador do resumo de varredura em integration.events."""
import json

from aio_pika import DeliveryMode, Message

from app.core.metrics import SCAN_SUMMARIES_PUBLISHED
from app.infrastructure.messaging.schemas import (
    SCAN_SUMMARY_ROUTING_KEY,
    MessageEnvelope,
)
from app.infrastructure.messaging.topology import IEducarTopology


class ChangePublisher:
    def __init__(self, topology: IEducarTopology) -> None:
        self._topology = topology

    async def publish_scan_summary(self, envelope: MessageEnvelope) -> str:
        body = envelope.model_dump(mode="json")
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        message = Message(
            body=payload,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers={"x-correlation-id": envelope.correlation_id},
        )
        await self._topology.events_exchange.publish(
            message, routing_key=SCAN_SUMMARY_ROUTING_KEY
        )
        SCAN_SUMMARIES_PUBLISHED.labels(trigger=str(envelope.payload["trigger"])).inc()
        return envelope.message_id
