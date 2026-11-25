"""Preflight best-effort contra o moodle-service (não bloqueia se Moodle estiver fora)."""
import httpx

from app.core.config import Settings
from app.core.logging import get_logger

_log = get_logger("moodle_preflight")


async def run_setup_check(
    settings: Settings,
    kind: str,
    correlation_id: str | None = None,
) -> dict[str, str]:
    if not settings.moodle_setup_check_enabled:
        return {"status": "skipped", "reason": "disabled"}

    url = f"{settings.moodle_service_url.rstrip('/')}/internal/setup-check"
    headers = {"X-Correlation-Id": correlation_id} if correlation_id else {}
    try:
        async with httpx.AsyncClient(timeout=settings.moodle_setup_check_timeout) as client:
            response = await client.get(url, params={"kind": kind}, headers=headers)
            if response.status_code == 200:
                return {"status": "ok", "kind": kind}
            _log.warning(
                "Setup-check retornou %s (best-effort); comandos seguirão para o RabbitMQ.",
                response.status_code,
            )
            return {"status": "degraded", "http_status": str(response.status_code)}
    except httpx.HTTPError as exc:
        _log.warning(
            "Setup-check indisponível (%s); comandos seguirão para o RabbitMQ.",
            type(exc).__name__,
        )
        return {"status": "degraded", "error": type(exc).__name__}
