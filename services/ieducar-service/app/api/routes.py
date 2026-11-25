"""API do ieducar-service.

As três rotas de leitura são o contrato que o sync-service consome quando
`IEDUCAR_SOURCE=http`. As rotas de varredura existem para operar e demonstrar o
serviço sem depender do relógio.
"""
from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.core.errors import IEducarServiceError
from app.core.metrics import render_metrics

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    scanner = request.app.state.scanner
    client = request.app.state.client
    return {
        "status": "ok",
        "service": settings.service_name,
        "scanning": scanner.is_scanning,
        "scan_interval_seconds": settings.scan_interval_seconds,
        "ieducar_circuit_breaker": client.breaker_state,
    }


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict:
    repo = request.app.state.repo
    messaging = request.app.state.messaging
    client = request.app.state.client
    try:
        db_ok = bool(repo.ping())
    except Exception:
        db_ok = False
    rabbit_ok = bool(messaging.is_ready)
    breaker = client.breaker_state
    if not db_ok or not rabbit_ok:
        response.status_code = 503
    return {
        "status": "ready" if db_ok and rabbit_ok else "unavailable",
        "database": "up" if db_ok else "down",
        "rabbitmq": "up" if rabbit_ok else "down",
        "ieducar_circuit_breaker": breaker,
    }


@router.get("/metrics")
async def metrics() -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


@router.get("/internal/catalog")
async def internal_catalog(request: Request) -> list[dict]:
    client = request.app.state.client
    try:
        return [entry.model_dump(mode="json") for entry in await client.fetch_catalog()]
    except IEducarServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.get("/internal/teachers")
async def internal_teachers(request: Request) -> list[dict]:
    client = request.app.state.client
    try:
        return [record.model_dump(mode="json") for record in await client.fetch_teachers()]
    except IEducarServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.get("/internal/students")
async def internal_students(request: Request) -> list[dict]:
    client = request.app.state.client
    try:
        return [record.model_dump(mode="json") for record in await client.fetch_students()]
    except IEducarServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.post("/internal/scan", status_code=200)
async def trigger_scan(
    request: Request,
    response: Response,
    full: bool = Query(
        default=False,
        description="Republica um aviso por registro vigente, ignorando as impressões digitais.",
    ),
) -> dict:
    scanner = request.app.state.scanner
    correlation_id = request.headers.get("X-Correlation-Id")
    result = await scanner.scan(trigger="manual", correlation_id=correlation_id, full=full)
    if result.get("status") == "busy":
        response.status_code = 409
    elif result.get("status") == "failed":
        response.status_code = 503
    return result


@router.get("/internal/scans")
async def list_scans(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    return request.app.state.repo.latest_scans(limit)


@router.get("/internal/scans/{scan_id}")
async def get_scan(request: Request, scan_id: str) -> dict:
    scan = request.app.state.repo.get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Varredura não encontrada.")
    return scan
