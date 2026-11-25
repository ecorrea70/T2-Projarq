"""Cliente HTTP do ieducar-service com propagação de X-Correlation-Id e erro sanitizado."""
from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.errors import OriginUnavailableError
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

    async def _get_json(self, path: str, correlation_id: str | None = None):
        headers = {"X-Correlation-Id": correlation_id} if correlation_id else {}
        try:
            response = await self._client.get(path, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise OriginUnavailableError("Tempo limite excedido ao consultar o ieducar-service.") from None
        except httpx.HTTPStatusError as exc:
            raise OriginUnavailableError(
                f"O ieducar-service retornou erro HTTP {exc.response.status_code}."
            ) from None
        except (httpx.HTTPError, ValueError):
            raise OriginUnavailableError("Falha de comunicação com o ieducar-service.") from None

    async def fetch_catalog(self, correlation_id: str | None = None) -> list[CatalogEntry]:
        rows = await self._get_json("/internal/catalog", correlation_id=correlation_id)
        return [CatalogEntry.model_validate(row) for row in rows]

    async def fetch_teachers(self, correlation_id: str | None = None) -> list[ProfessorRecord]:
        rows = await self._get_json("/internal/teachers", correlation_id=correlation_id)
        return [ProfessorRecord.model_validate(row) for row in rows]

    async def fetch_students(self, correlation_id: str | None = None) -> list[AlunoRecord]:
        rows = await self._get_json("/internal/students", correlation_id=correlation_id)
        return [AlunoRecord.model_validate(row) for row in rows]
