import asyncio

import pytest
from fastapi.testclient import TestClient

from app.application.sync_coordinator import SyncCoordinator
from app.core.errors import OriginUnavailableError
from app.infrastructure.ieducar.factory import IEducarSource
from app.infrastructure.messaging.schemas import MessageEnvelope
from app.main import create_app


class FakeCommandPublisher:
    def __init__(self) -> None:
        self.published: list[MessageEnvelope] = []

    async def publish_command(self, envelope: MessageEnvelope) -> str:
        self.published.append(envelope)
        return envelope.message_id


class FailingIEducarSource(IEducarSource):
    async def fetch_catalog(self, correlation_id: str | None = None):
        raise OriginUnavailableError("i-Educar indisponível.")

    async def fetch_teachers(self, correlation_id: str | None = None):
        raise OriginUnavailableError("i-Educar indisponível.")

    async def fetch_students(self, correlation_id: str | None = None):
        raise OriginUnavailableError("i-Educar indisponível.")

    async def aclose(self) -> None:
        return None


@pytest.fixture
def client(repo, settings) -> TestClient:
    coordinator = SyncCoordinator(settings, repo, publisher=None)
    app = create_app(settings=settings, repo=repo, coordinator=coordinator)
    with TestClient(app) as test_client:
        yield test_client


def test_sync_teachers_accepted_and_consistent_correlation_id(client: TestClient) -> None:
    response = client.post("/sync/teachers")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["phase_1_published"] == 0
    assert body["total_items"] > 0
    assert body["correlation_id"] == response.headers["X-Correlation-Id"]


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


def test_origin_unavailable_returns_503(repo, settings) -> None:
    coordinator = SyncCoordinator(settings, repo, publisher=None, ieducar=FailingIEducarSource())
    app = create_app(settings=settings, repo=repo, coordinator=coordinator)
    with TestClient(app) as test_client:
        response = test_client.post("/sync/teachers")
        assert response.status_code == 503


async def test_student_phase2_filters_failed_student_instead_of_cancelling_batch(repo, settings) -> None:
    publisher = FakeCommandPublisher()
    coordinator = SyncCoordinator(settings, repo, publisher=publisher)

    started = await coordinator.start_students(correlation_id="corr-students-filter")
    assert started["status"] == "accepted"

    # Fase 1: aluno 1 sucede, aluno 2 falha, aluno 3 falha.
    await coordinator.handle_moodle_event(
        {
            "correlation_id": "corr-students-filter",
            "idempotency_key": "user:student:1",
            "event_type": "moodle.user.synced",
            "payload": {"moodle_id": 10},
        }
    )
    await coordinator.handle_moodle_event(
        {
            "correlation_id": "corr-students-filter",
            "idempotency_key": "user:student:2",
            "event_type": "moodle.user.sync.failed",
            "payload": {"erro": "email inválido"},
        }
    )
    await coordinator.handle_moodle_event(
        {
            "correlation_id": "corr-students-filter",
            "idempotency_key": "user:student:3",
            "event_type": "moodle.user.sync.failed",
            "payload": {"erro": "sem email"},
        }
    )

    enrol_cmds = [e for e in publisher.published if e.event_type == "moodle.students.enrol.requested"]
    # Curso da turma 10 deve ser publicado contendo apenas aluno 1 (aluno 2 filtrado); turma 11 (só aluno 3) é skipped.
    assert len(enrol_cmds) == 1
    assert enrol_cmds[0].payload["students"] == [{"aluno_id": 1, "user_idnumber": "ieducar:aluno:1"}]

    # Conclui o lote publicado e verifica finalização + 409 ao repetir o mesmo correlation_id.
    await coordinator.handle_moodle_event(
        {
            "correlation_id": "corr-students-filter",
            "idempotency_key": enrol_cmds[0].idempotency_key,
            "event_type": "moodle.students.enrolled",
            "payload": {"resumo": {"vinculado": 1, "existente": 0, "erro": 0}},
        }
    )
    progress = coordinator.get_progress("corr-students-filter")
    assert progress is not None
    assert progress["status"] == "completed_with_errors"

    duplicate = await coordinator.start_students(correlation_id="corr-students-filter")
    assert duplicate["status"] == "duplicate_correlation_id"


async def test_phase2_is_published_only_once_under_concurrent_events(repo, settings) -> None:
    publisher = FakeCommandPublisher()
    coordinator = SyncCoordinator(settings, repo, publisher=publisher)

    started = await coordinator.start_teachers(correlation_id="corr-race-test")
    assert started["status"] == "accepted"

    phase1_events = [
        {
            "correlation_id": "corr-race-test",
            "idempotency_key": env.idempotency_key,
            "event_type": "moodle.course.synced"
            if env.event_type == "moodle.course.sync.requested"
            else "moodle.user.synced",
            "payload": {"moodle_id": 100},
        }
        for env in publisher.published
    ]
    await asyncio.gather(*(coordinator.handle_moodle_event(ev) for ev in phase1_events))

    phase2_cmds = [e for e in publisher.published if e.event_type == "moodle.teacher.enrol.requested"]
    assert len(phase2_cmds) == 2
