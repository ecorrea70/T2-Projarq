"""API interna do sync-service (será exposta via api-gateway na Fase 3)."""
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response

from app.core.metrics import render_metrics

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    consumer = getattr(request.app.state, "events_consumer", None)
    return {
        "status": "ok",
        "service": settings.service_name,
        "ieducar_source": settings.ieducar_source,
        "events_consumer_running": bool(consumer and consumer.is_ready),
    }


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict:
    repo = request.app.state.repo
    consumer = getattr(request.app.state, "events_consumer", None)
    settings = request.app.state.settings
    try:
        db_ok = bool(repo.ping())
    except Exception:
        db_ok = False
    rabbit_ok = (not settings.rabbitmq_enabled) or bool(consumer and consumer.is_ready)
    if not db_ok or not rabbit_ok:
        response.status_code = 503
    return {
        "status": "ready" if db_ok and rabbit_ok else "unavailable",
        "database": "up" if db_ok else "down",
        "rabbitmq": "disabled" if not settings.rabbitmq_enabled else ("up" if rabbit_ok else "down"),
    }


@router.get("/metrics")
async def metrics() -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


@router.post("/sync/teachers", status_code=202)
async def sync_teachers(
    request: Request,
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict:
    coordinator = request.app.state.coordinator
    result = await coordinator.start_teachers(correlation_id=x_correlation_id or str(uuid4()))
    if result.get("status") == "conflict":
        raise HTTPException(status_code=409, detail=result["active"])
    if result.get("status") == "rejected":
        raise HTTPException(status_code=400, detail=result.get("reason"))
    return result


@router.post("/sync/students", status_code=202)
async def sync_students(
    request: Request,
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict:
    coordinator = request.app.state.coordinator
    result = await coordinator.start_students(correlation_id=x_correlation_id or str(uuid4()))
    if result.get("status") == "conflict":
        raise HTTPException(status_code=409, detail=result["active"])
    if result.get("status") == "rejected":
        raise HTTPException(status_code=400, detail=result.get("reason"))
    return result


@router.get("/sync/{correlation_id}")
async def get_sync_status(request: Request, correlation_id: str) -> dict:
    coordinator = request.app.state.coordinator
    snapshot = coordinator.get_progress(correlation_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    return snapshot