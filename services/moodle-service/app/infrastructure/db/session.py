"""Configuração da engine e fábrica de sessões SQLAlchemy."""
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def create_db_engine(database_url: str) -> Engine:
    """Cria a engine suportando PostgreSQL em produção e SQLite em memória nos testes."""
    if database_url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in database_url or database_url == "sqlite://":
            kwargs["poolclass"] = StaticPool
        return create_engine(database_url, **kwargs)
    return create_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Cria a fábrica de sessões sincronizadas para o repositório."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
