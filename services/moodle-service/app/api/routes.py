"""Endpoints HTTP de saúde, métricas e inspeção interna do moodle-service."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.core.errors import PermanentDomainError, TransientIntegrationError
from app.core.metrics import render_metrics

router = APIRouter()


@router.get("/health")
async def healthcheck(request: Request) -> dict[str, object]:
    """Liveness probe do processo e do consumidor RabbitMQ."""
    settings = request.app.state.settings
    consumer = getattr(request.app.state, "consumer", None)
    return {
        "status": "ok",
        "service": settings.service_name,
        "consumer_running": bool(consumer and consumer.is_consuming),
    }


@router.get("/ready")
async def readiness(request: Request, response: Response) -> dict[str, object]:
    """Readiness probe validando banco próprio, conexão AMQP e Circuit Breaker."""
    settings = request.app.state.settings
    repo = request.app.state.repo
    moodle_client = request.app.state.moodle_client
    consumer = getattr(request.app.state, "consumer", None)

    try:
        db_ok = bool(repo.ping())
    except Exception:
        db_ok = False

    rabbit_ok = (not settings.rabbitmq_enabled) or bool(consumer and consumer.is_ready)
    cb_state = moodle_client.circuit_breaker.state
    ready = db_ok and rabbit_ok

    if not ready:
        response.status_code = 503

    return {
        "status": "ready" if ready else "unavailable",
        "database": "up" if db_ok else "down",
        "rabbitmq": "disabled" if not settings.rabbitmq_enabled else ("up" if rabbit_ok else "down"),
        "circuit_breaker": cb_state,
    }


@router.get("/metrics")
async def metrics() -> Response:
    """Expõe métricas no formato Prometheus."""
    data, content_type = render_metrics()
    return Response(content=data, media_type=content_type)


@router.get("/internal/setup-check")
async def setup_check(
    request: Request,
    kind: Literal["teachers", "students"] = "students",
) -> dict[str, str]:
    """Verifica funções Web Service e categoria configurada no Moodle (preflight)."""
    settings = request.app.state.settings
    if not settings.moodle_token:
        raise HTTPException(status_code=503, detail="MOODLE_TOKEN não configurado.")

    moodle_client = request.app.state.moodle_client
    try:
        if kind == "teachers":
            await moodle_client.check_teaching_setup()
        else:
            await moodle_client.check_student_setup()
    except TransientIntegrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except PermanentDomainError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return {"status": "ok", "kind": kind}


@router.get("/internal/runs/{correlation_id}")
async def get_run_summary(correlation_id: str, request: Request) -> dict[str, object]:
    """Retorna auditoria das mensagens processadas para um correlation_id."""
    repo = request.app.state.repo
    return repo.get_run_summary(correlation_id)


@router.post("/internal/dlq/replay")
async def replay_dlq(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, int]:
    """Reenfileira mensagens da DLQ para reprocessamento após recuperação."""
    consumer = getattr(request.app.state, "consumer", None)
    if consumer is None or not consumer.is_ready:
        raise HTTPException(status_code=503, detail="Consumidor RabbitMQ indisponível.")
    replayed = await consumer.replay_dlq(limit=limit)
    return {"replayed": replayed}
