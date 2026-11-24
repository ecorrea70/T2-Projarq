"""Fixtures compartilhadas para os testes do moodle-service."""
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.errors import PermanentDomainError
from app.infrastructure.db.models import Base
from app.infrastructure.db.repository import SqlAlchemyStateRepository
from app.infrastructure.db.session import create_db_engine, create_session_factory


class FakePublisher:
    """Captura eventos, retries e mensagens enviadas à DLQ em memória."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.retries: list[tuple[str, bytes, dict[str, Any]]] = []
        self.dlq: list[tuple[bytes, dict[str, Any]]] = []

    async def publish_event(
        self,
        routing_key: str,
        payload: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> None:
        self.events.append((routing_key, payload, headers or {}))

    async def publish_retry(
        self,
        routing_key: str,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> None:
        self.retries.append((routing_key, raw_body, headers))

    async def publish_dlq(
        self,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> None:
        self.dlq.append((raw_body, headers))


class FakeMoodleGateway:
    """Simulador em memória da API Web Service do Moodle."""

    def __init__(self) -> None:
        self.users: list[dict[str, Any]] = []
        self.courses: list[dict[str, Any]] = []
        self.teachers_by_course: dict[int, set[int]] = {}
        self.students_by_course: dict[int, set[int]] = {}
        self.enrol_teacher_calls = 0
        self.enrol_students_calls = 0

    async def check_teaching_setup(self) -> None:
        return None

    async def check_student_setup(self) -> None:
        return None

    async def find_user(self, field: str, value: str) -> list[dict[str, Any]]:
        return [u for u in self.users if u.get(field) == value]

    async def create_user(self, user: dict[str, Any]) -> int:
        user_id = len(self.users) + 10
        self.users.append({**user, "id": user_id})
        return user_id

    async def find_course(self, field: str, value: str) -> list[dict[str, Any]]:
        return [c for c in self.courses if c.get(field) == value]

    async def create_course(self, course: dict[str, Any]) -> int:
        course_id = len(self.courses) + 20
        self.courses.append({**course, "id": course_id})
        return course_id

    async def course_teachers(self, course_id: int) -> set[int]:
        return set(self.teachers_by_course.get(course_id, set()))

    async def course_students(self, course_id: int) -> set[int]:
        return set(self.students_by_course.get(course_id, set()))

    async def enrol_teacher(self, user_id: int, course_id: int) -> None:
        self.enrol_teacher_calls += 1
        self.teachers_by_course.setdefault(course_id, set()).add(user_id)

    async def enrol_students(self, user_ids: list[int], course_id: int) -> None:
        self.enrol_students_calls += 1
        self.students_by_course.setdefault(course_id, set()).update(user_ids)


def make_envelope_dict(
    event_type: str,
    payload: dict[str, Any],
    *,
    message_id: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str = "test:key:1",
) -> dict[str, Any]:
    return {
        "message_id": message_id or str(uuid4()),
        "event_type": event_type,
        "event_version": "1.0",
        "correlation_id": correlation_id or str(uuid4()),
        "causation_id": None,
        "producer": "pytest",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": idempotency_key,
        "payload": payload,
    }


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        rabbitmq_enabled=False,
        moodle_url="http://moodle.local:8080",
        moodle_token="test-moodle-token",
        moodle_category_id=1,
        moodle_teacher_role_id=3,
        moodle_student_role_id=5,
        local_max_retries=2,
        local_retry_base_delay=0.0,
        rabbitmq_max_retries=3,
        cb_failure_threshold=5,
        cb_recovery_seconds=30.0,
    )


@pytest.fixture
def repo() -> SqlAlchemyStateRepository:
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return SqlAlchemyStateRepository(create_session_factory(engine))


@pytest.fixture
def gateway() -> FakeMoodleGateway:
    return FakeMoodleGateway()


@pytest.fixture
def publisher() -> FakePublisher:
    return FakePublisher()
