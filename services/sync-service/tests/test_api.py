import pytest
from fastapi.testclient import TestClient

from app.application.sync_coordinator import SyncCoordinator
from app.main import create_app


@pytest.fixture
def client(repo, settings) -> TestClient:
    coordinator = SyncCoordinator(settings, repo, publisher=None)
    app = create_app(settings=settings, repo=repo, coordinator=coordinator)
    with TestClient(app) as test_client:
        yield test_client


def test_sync_teachers_accepted(client: TestClient) -> None:
    response = client.post("/sync/teachers")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["phase_1_published"] == 0
    assert body["total_items"] > 0


def test_sync_conflict_409(client: TestClient) -> None:
    first = client.post("/sync/teachers")
    assert first.status_code == 202
    second = client.post("/sync/teachers")
    assert second.status_code == 409


def test_get_sync_progress(client: TestClient) -> None:
    started = client.post("/sync/students")
    correlation_id = started.json()["correlation_id"]
    progress = client.get(f"/sync/{correlation_id}")
    assert progress.status_code == 200
    assert progress.json()["kind"] == "students"