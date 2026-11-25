"""Retry local com backoff exponencial e jitter para falhas transitórias."""
import asyncio
from collections.abc import Awaitable, Callable
import random
from typing import TypeVar

from app.core.errors import CircuitBreakerOpenError, TransientIntegrationError
from app.core.logging import get_logger

T = TypeVar("T")
_log = get_logger("retry")


async def run_with_local_retry(
    operation: Callable[[int], Awaitable[T]],
    max_retries: int,
    base_delay: float,
) -> T:
    """Executa a operação repetindo apenas falhas transitórias.

    Circuito aberto não é repetido aqui: o breaker existe justamente para não
    insistir, e a varredura seguinte tentará de novo.
    """
    for attempt in range(1, max_retries + 2):
        try:
            return await operation(attempt)
        except CircuitBreakerOpenError:
            raise
        except TransientIntegrationError as exc:
            if attempt > max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            if delay > 0:
                delay += random.uniform(0.0, 0.2)
            _log.warning(
                "Falha transitória na tentativa %d; novo retry em %.2fs: %s",
                attempt,
                delay,
                exc,
                extra={"attempt": attempt, "error_type": type(exc).__name__},
            )
            if delay > 0:
                await asyncio.sleep(delay)
    raise RuntimeError("Loop de retry encerrado inesperadamente.")
