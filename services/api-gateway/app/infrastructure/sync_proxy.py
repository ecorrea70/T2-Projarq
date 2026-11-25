"""Cliente HTTP para o sync-service (sem expor a rede interna ao cliente)."""
from __future__ import annotations

import time

import httpx

from app.core.config import Settings
from app.core.metrics import PROXY_DURATION, PROXY_REQUESTS


class SyncServiceProxy:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.sync_service_url.rstrip("/"),
            timeout=settings.sync_http_timeout,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        correlation_id: str | None = None,
        metric_path: str | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if correlation_id:
            headers["X-Correlation-Id"] = correlation_id
        label_path = metric_path or path
        started = time.perf_counter()
        kwargs: dict = {"headers": headers}
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = await self._client.request(method, path, **kwargs)
        elapsed = time.perf_counter() - started
        PROXY_DURATION.labels(path=label_path).observe(elapsed)
        PROXY_REQUESTS.labels(method=method, path=label_path, status_code=str(response.status_code)).inc()
        return response

    async def start_teachers(self, correlation_id: str) -> httpx.Response:
        return await self._request("POST", "/sync/teachers", correlation_id=correlation_id)

    async def start_students(self, correlation_id: str) -> httpx.Response:
        return await self._request("POST", "/sync/students", correlation_id=correlation_id)

    async def get_sync_status(self, correlation_id: str) -> httpx.Response:
        return await self._request(
            "GET",
            f"/sync/{correlation_id}",
            correlation_id=correlation_id,
            metric_path="/sync/{correlation_id}",
        )

    async def health(self) -> httpx.Response:
        return await self._request("GET", "/health", timeout=self._settings.health_probe_timeout)

    async def ready(self) -> httpx.Response:
        return await self._request("GET", "/ready", timeout=self._settings.health_probe_timeout)
