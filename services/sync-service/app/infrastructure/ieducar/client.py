"""Cliente HTTP do ieducar-service (contrato previsto; uso futuro)."""
from __future__ import annotations

import httpx

from app.core.config import Settings
from app.domain.models import AlunoRecord, CatalogEntry, ProfessorRecord


class IEducarServiceClient:
    """Consome GET /internal/* do ieducar-service quando IEDUCAR_SOURCE=http."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = http_client or httpx.AsyncClient(
            base_url=settings.ieducar_service_url.rstrip("/"),
            timeout=settings.ieducar_http_timeout,
        )
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_catalog(self) -> list[CatalogEntry]:
        response = await self._client.get("/internal/catalog")
        response.raise_for_status()
        return [CatalogEntry.model_validate(row) for row in response.json()]

    async def fetch_teachers(self) -> list[ProfessorRecord]:
        response = await self._client.get("/internal/teachers")
        response.raise_for_status()
        return [ProfessorRecord.model_validate(row) for row in response.json()]

    async def fetch_students(self) -> list[AlunoRecord]:
        response = await self._client.get("/internal/students")
        response.raise_for_status()
        return [AlunoRecord.model_validate(row) for row in response.json()]