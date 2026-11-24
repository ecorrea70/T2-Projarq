"""Testes da borda pública."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.infrastructure.sync_proxy import SyncServiceProxy
from app.main import create_app


class MockSyncProxy(SyncServiceProxy):
    def __init__(self) -> None:
        super().__init__(Settings(sync_service_url="http://mock"), client=httpx.AsyncClient())

    async def start_teachers(self, correlation_id: str) -> httpx.Response:
        return httpx.Response(
            202,
            json={
                "status": "accepted",
                "correlation_id": correlation_id,
                "kind": "teachers",
                "total_items": 3,
                "phase_1_published": 2,
            },
        )

    async def start_students(self, correlation_id: str) -> httpx.Response:
        return httpx.Response(202, json={"status": "accepted", "correlation_id": correlation_id, "kind": "students"})

    async def get_sync_status(self, correlation_id: str) -> httpx.Response:
        return httpx.Response(200, json={"correlation_id": correlation_id, "status": "running", "kind": "teachers"})

    async def health(self) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "service": "sync-service"})

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client() -> TestClient:
    settings = Settings(sync_api_token="segredo-demo")
    app = create_app(settings=settings, sync_proxy=MockSyncProxy())
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "api-gateway"


def test_sync_requires_bearer(client: TestClient) -> None:
    assert client.post("/sync/teachers").status_code == 401


def test_sync_rejects_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/sync/teachers",
        headers={"Authorization": "Bearer errado"},
    )
    assert response.status_code == 401


def test_sync_teachers_proxies_and_returns_correlation_header(client: TestClient) -> None:
    response = client.post(
        "/sync/teachers",
        headers={
            "Authorization": "Bearer segredo-demo",
            "X-Correlation-Id": "corr-123",
        },
    )
    assert response.status_code == 202
    assert response.json()["correlation_id"] == "corr-123"
    assert response.headers.get("X-Correlation-Id") == "corr-123"


def test_sync_status_proxy(client: TestClient) -> None:
    response = client.get(
        "/sync/corr-123",
        headers={"Authorization": "Bearer segredo-demo"},
    )
    assert response.status_code == 200
    assert response.json()["correlation_id"] == "corr-123"


def test_missing_token_config_returns_503() -> None:
    app = create_app(settings=Settings(sync_api_token=""), sync_proxy=MockSyncProxy())
    with TestClient(app) as client:
        response = client.post("/sync/teachers", headers={"Authorization": "Bearer x"})
        assert response.status_code == 503