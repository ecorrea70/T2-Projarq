"""Superfície pública — apenas sync + saúde/métricas operacionais."""
import json
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response

from app.core.auth import require_bearer_token
from app.core.metrics import render_metrics
from app.infrastructure.sync_proxy import SyncServiceProxy

router = APIRouter()


def _correlation_id(header_value: str | None) -> str:
    if header_value and header_value.strip():
        return header_value.strip()
    return str(uuid4())


def _parse_upstream(response: httpx.Response) -> tuple[int, Any]:
    try:
        return response.status_code, response.json()
    except json.JSONDecodeError:
        return response.status_code, response.text


@router.get("/health")
async def health(request: Request, response: Response) -> dict:
    """Liveness do gateway; agrega saúde do sync-service quando configurado."""
    settings = request.app.state.settings
    proxy: SyncServiceProxy = request.app.state.sync_proxy
    body: dict = {
        "status": "ok",
        "service": settings.service_name,
        "sync_service_configured": bool(settings.sync_service_url),
        "auth_configured": bool(settings.sync_api_token.strip()),
    }
    if settings.aggregate_health:
        try:
            upstream = await proxy.health()
            content_type = upstream.headers.get("content-type", "")
            if content_type.startswith("application/json"):
                body["sync_service"] = upstream.json()
            else:
                body["sync_service"] = {"status_code": upstream.status_code}
            if upstream.status_code >= 500:
                response.status_code = 503
                body["status"] = "degraded"
        except httpx.HTTPError as exc:
            response.status_code = 503
            body["status"] = "degraded"
            body["sync_service"] = {"status": "unreachable", "error": type(exc).__name__}
    return body


@router.get("/metrics")
async def metrics() -> Response:
    data, content_type = render_metrics()
    return Response(content=data, media_type=content_type)


@router.post("/sync/teachers", status_code=202)
async def sync_teachers(
    request: Request,
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict:
    require_bearer_token(request)
    correlation_id = _correlation_id(x_correlation_id)
    proxy: SyncServiceProxy = request.app.state.sync_proxy
    upstream = await proxy.start_teachers(correlation_id)
    status, payload = _parse_upstream(upstream)
    if status >= 400:
        raise HTTPException(status_code=status, detail=payload)
    if isinstance(payload, dict):
        payload.setdefault("correlation_id", correlation_id)
        return payload
    return {"correlation_id": correlation_id, "detail": payload}


@router.post("/sync/students", status_code=202)
async def sync_students(
    request: Request,
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
) -> dict:
    require_bearer_token(request)
    correlation_id = _correlation_id(x_correlation_id)
    proxy: SyncServiceProxy = request.app.state.sync_proxy
    upstream = await proxy.start_students(correlation_id)
    status, payload = _parse_upstream(upstream)
    if status >= 400:
        raise HTTPException(status_code=status, detail=payload)
    if isinstance(payload, dict):
        payload.setdefault("correlation_id", correlation_id)
        return payload
    return {"correlation_id": correlation_id, "detail": payload}


@router.get("/sync/{correlation_id}")
async def get_sync_status(request: Request, correlation_id: str) -> dict:
    require_bearer_token(request)
    proxy: SyncServiceProxy = request.app.state.sync_proxy
    upstream = await proxy.get_sync_status(correlation_id)
    status, payload = _parse_upstream(upstream)
    if status >= 400:
        raise HTTPException(status_code=status, detail=payload)
    if isinstance(payload, dict):
        return payload
    return {"detail": payload}