"""Consumidor dos resumos de varredura do ieducar-service, em fila própria.

Nada aqui bloqueia esperando o fim de uma execução: quando a trava rejeita, o
resumo é estacionado em `sync.ieducar.retry` com só o que ainda falta, recebe
ACK, e o broker o devolve quando o TTL vencer. Ver ADR-007 em `docs/adr.md`.
"""
import asyncio
import json

import aio_pika
from aio_pika import DeliveryMode, Message
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
)

from app.application.notice_handler import ScanNoticeHandler, parked_payload
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.metrics import SCAN_NOTICES_HANDLED
from app.infrastructure.messaging.topology import (
    NOTICES_DLQ_ROUTING_KEY,
    NOTICES_ROUTING_KEY,
    SyncTopology,
    declare_topology,
)

ATTEMPTS_HEADER = "x-notice-attempts"


def attempts_of(message: AbstractIncomingMessage) -> int:
    try:
        return int((message.headers or {}).get(ATTEMPTS_HEADER) or 0)
    except (TypeError, ValueError):
        return 0


class ScanNoticesConsumer:
    def __init__(self, settings: Settings, handler: ScanNoticeHandler) -> None:
        self._settings = settings
        self._handler = handler
        self._log = get_logger("notices_consumer")
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._topology: SyncTopology | None = None
        self._consumer_tag: str | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def is_ready(self) -> bool:
        if not self._enabled:
            return True
        return (
            self._connection is not None
            and not self._connection.is_closed
            and self._consumer_tag is not None
        )

    @property
    def _enabled(self) -> bool:
        return self._settings.rabbitmq_enabled and self._settings.ieducar_notices_enabled

    async def start(self) -> None:
        if not self._enabled:
            return
        self._stopping = False
        try:
            await self._connect_and_consume()
        except Exception as exc:
            self._log.warning(
                "RabbitMQ indisponível no startup do consumidor de avisos (%s); reconectando.",
                type(exc).__name__,
            )
            self._connect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while not self._stopping and not self.is_ready:
            await asyncio.sleep(2.0)
            if self._stopping:
                break
            try:
                await self._connect_and_consume()
            except Exception as exc:
                self._log.warning(
                    "Falha ao conectar consumidor de avisos: %s", type(exc).__name__
                )

    async def _connect_and_consume(self) -> None:
        self._connection = await aio_pika.connect_robust(self._settings.rabbitmq_url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=1)
        self._topology = await declare_topology(
            self._channel,
            notice_retry_ttl_ms=self._settings.ieducar_notice_retry_ttl_ms,
        )
        self._consumer_tag = await self._topology.notices_queue.consume(
            self._on_message, no_ack=False
        )
        self._log.info("Consumidor de avisos conectado em sync.ieducar.notices.")

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
                await self._topology.notices_queue.cancel(self._consumer_tag)
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
        except Exception:
            self._log.exception("Resumo de varredura ilegível; descartando.")
            SCAN_NOTICES_HANDLED.labels(outcome="unreadable").inc()
            await message.ack()
            return

        attempt = attempts_of(message)
        try:
            outcome = await self._handler.handle(data, attempt=attempt)
        except Exception:
            self._log.exception("Falha ao tratar resumo de varredura; reenfileirando.")
            await message.nack(requeue=True)
            return

        outcome_label = "started" if outcome.started else "noop"
        if outcome.should_park:
            outcome_label = await self._park(data, outcome.pending, attempt + 1, message)
        SCAN_NOTICES_HANDLED.labels(outcome=outcome_label).inc()
        await message.ack()

    async def _park(
        self,
        data: dict,
        pending: list[str],
        attempt: int,
        message: AbstractIncomingMessage,
    ) -> str:
        body = json.dumps(
            {**data, "payload": parked_payload(data.get("payload") or {}, pending)},
            ensure_ascii=False,
        ).encode("utf-8")
        routing_key = message.routing_key or NOTICES_ROUTING_KEY
        headers = {**(message.headers or {}), ATTEMPTS_HEADER: attempt}

        if attempt > self._settings.ieducar_notice_max_attempts:
            self._log.error(
                "Resumo excedeu %d tentativas com %s ainda devido; enviando para a DLQ.",
                self._settings.ieducar_notice_max_attempts,
                ", ".join(pending),
                extra={"status": "exhausted"},
            )
            await self._publish(
                self._topology.dlx_exchange, body, NOTICES_DLQ_ROUTING_KEY, headers
            )
            return "exhausted"

        await self._publish(
            self._topology.notices_retry_exchange, body, routing_key, headers
        )
        return "parked"

    async def _publish(self, exchange, body: bytes, routing_key: str, headers: dict) -> None:
        await exchange.publish(
            Message(
                body=body,
                delivery_mode=DeliveryMode.PERSISTENT,
                content_type="application/json",
                headers=headers,
            ),
            routing_key=routing_key,
        )
