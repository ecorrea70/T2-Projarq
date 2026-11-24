"""Seleciona origem de dados (mock ou ieducar-service HTTP)."""
from __future__ import annotations

from app.core.config import Settings
from app.domain.models import AlunoRecord, CatalogEntry, ProfessorRecord
from app.infrastructure.ieducar.client import IEducarServiceClient
from app.infrastructure.ieducar import mock as mock_data


class IEducarSource:
    async def fetch_catalog(self) -> list[CatalogEntry]: ...
    async def fetch_teachers(self) -> list[ProfessorRecord]: ...
    async def fetch_students(self) -> list[AlunoRecord]: ...
    async def aclose(self) -> None: ...


class MockIEducarSource(IEducarSource):
    async def fetch_catalog(self) -> list[CatalogEntry]:
        return mock_data.mock_catalog()

    async def fetch_teachers(self) -> list[ProfessorRecord]:
        return mock_data.mock_teachers()

    async def fetch_students(self) -> list[AlunoRecord]:
        return mock_data.mock_students()

    async def aclose(self) -> None:
        return None


class HttpIEducarSource(IEducarSource):
    def __init__(self, client: IEducarServiceClient) -> None:
        self._client = client

    async def fetch_catalog(self) -> list[CatalogEntry]:
        return await self._client.fetch_catalog()

    async def fetch_teachers(self) -> list[ProfessorRecord]:
        return await self._client.fetch_teachers()

    async def fetch_students(self) -> list[AlunoRecord]:
        return await self._client.fetch_students()

    async def aclose(self) -> None:
        await self._client.aclose()


def build_ieducar_source(settings: Settings) -> IEducarSource:
    if settings.ieducar_source.lower() == "http":
        return HttpIEducarSource(IEducarServiceClient(settings))
    return MockIEducarSource()