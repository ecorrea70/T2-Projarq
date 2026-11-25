"""Fixtures: banco em memória, origem falsa e publicador que só registra."""
from typing import Any

import pytest

from app.application.scanner import SourceScanner
from app.core.config import Settings
from app.core.errors import TransientIntegrationError
from app.domain.models import AlunoRecord, CatalogEntry, ProfessorRecord
from app.infrastructure.db.models import Base
from app.infrastructure.db.repository import IEducarRepository
from app.infrastructure.db.session import create_db_engine, create_session_factory


def make_settings(**overrides: Any) -> Settings:
    base = {
        "database_url": "sqlite://",
        "rabbitmq_enabled": False,
        "ieducar_url": "http://ieducar.invalid",
        "ieducar_token": "token-de-teste",
        "ieducar_access_key": "chave-de-teste",
        "institution_id": 1,
        "school_id": "2",
        "year": 2026,
        "local_max_retries": 0,
        "local_retry_base_delay": 0.0,
        "scan_on_startup": False,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def repo() -> IEducarRepository:
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    return IEducarRepository(create_session_factory(engine))


class FakeIEducarClient:
    """Origem controlada: devolve o que o teste mandar, ou falha de propósito."""

    def __init__(
        self,
        catalog: list[dict] | None = None,
        teachers: list[dict] | None = None,
        students: list[dict] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.catalog = catalog or []
        self.teachers = teachers or []
        self.students = students or []
        self.fail_with = fail_with
        self.calls: list[str] = []

    @property
    def breaker_state(self) -> str:
        return "closed"

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def fetch_catalog(self) -> list[CatalogEntry]:
        self.calls.append("catalog")
        self._maybe_fail()
        return [CatalogEntry.model_validate(row) for row in self.catalog]

    async def fetch_teachers(self) -> list[ProfessorRecord]:
        self.calls.append("teachers")
        self._maybe_fail()
        return [ProfessorRecord.model_validate(row) for row in self.teachers]

    async def fetch_students(self) -> list[AlunoRecord]:
        self.calls.append("students")
        self._maybe_fail()
        return [AlunoRecord.model_validate(row) for row in self.students]

    async def aclose(self) -> None:
        return None


class RecordingPublisher:
    """Guarda os envelopes publicados em vez de falar com o RabbitMQ."""

    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish_change(self, envelope: Any) -> str:
        self.published.append(envelope)
        return envelope.message_id

    @property
    def routing_keys(self) -> list[str]:
        return [f"ieducar.{e.payload['entity_type']}.changed.v1" for e in self.published]

    @property
    def changes(self) -> list[tuple[str, str, str]]:
        return [
            (e.payload["entity_type"], e.payload["entity_key"], e.payload["change_type"])
            for e in self.published
        ]


def build_scanner(
    repo: IEducarRepository,
    client: FakeIEducarClient,
    publisher: RecordingPublisher | None = None,
    settings: Settings | None = None,
) -> tuple[SourceScanner, RecordingPublisher]:
    active_publisher = publisher or RecordingPublisher()
    scanner = SourceScanner(settings or make_settings(), repo, client, active_publisher)
    return scanner, active_publisher


CATALOG = [
    {
        "instituicao_id": 1,
        "escola_id": 2,
        "ano": 2026,
        "turma_id": 10,
        "turma_nome": "Turma A",
        "disciplina_id": 2,
        "disciplina_nome": "Matemática",
    }
]

TEACHERS = [
    {
        "professor_id": 7,
        "nome": "Ana Professora",
        "email": "ana@example.com",
        "vinculos": [{"turma_id": 10, "disciplina_id": 2}],
    }
]

STUDENTS = [
    {
        "aluno_id": 42,
        "nome": "Carlos Aluno",
        "email": "carlos@example.com",
        "turmas": [{"turma_id": 10, "ano": 2026, "escola_id": 2}],
    }
]


def transient() -> TransientIntegrationError:
    return TransientIntegrationError("i-Educar inacessível em registration (ConnectError).")
