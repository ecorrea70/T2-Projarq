import pytest

from app.core.config import Settings
from app.infrastructure.db.models import Base
from app.infrastructure.db.repository import SyncRepository
from app.infrastructure.db.session import create_db_engine, create_session_factory


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        rabbitmq_enabled=False,
        ieducar_source="mock",
    )


@pytest.fixture
def repo(settings: Settings) -> SyncRepository:
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    return SyncRepository(create_session_factory(engine))