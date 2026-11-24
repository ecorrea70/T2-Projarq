"""Publicador de comandos para o moodle-service."""
import json

from aio_pika import DeliveryMode, Message

from app.core.metrics import COMMAND_PUBLISH
from app.infrastructure.messaging.schemas import MessageEnvelope, routing_key_for_command
from app.infrastructure.messaging.topology import SyncTopology


class CommandPublisher:
    def __init__(self, topology: SyncTopology) -> None:
        self._topology = topology

    async def publish_command(self, envelope: MessageEnvelope) -> str:
        rk = routing_key_for_command(envelope.event_type)
        body = envelope.model_dump(mode="json")
        if isinstance(body.get("occurred_at"), str):
            pass
        else:
            body["occurred_at"] = envelope.occurred_at.isoformat()
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        message = Message(
            body=payload,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers={"x-correlation-id": envelope.correlation_id},
        )
        await self._topology.commands_exchange.publish(message, routing_key=rk)
        COMMAND_PUBLISH.labels(command_type=envelope.event_type).inc()
        return envelope.message_id