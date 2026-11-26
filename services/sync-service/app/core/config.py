"""Configurações do sync-service."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    service_name: str = "sync-service"
    port: int = Field(default=8002, ge=1, le=65535)

    database_url: str = "postgresql+psycopg://sync_service:sync_service@localhost:5432/sync_service"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    rabbitmq_enabled: bool = True
    rabbitmq_prefetch_count: int = Field(default=10, ge=1)

    # Origem i-Educar: mock até o ieducar-service do colega ficar pronto.
    ieducar_source: str = Field(default="mock", description="mock | http")
    ieducar_service_url: str = "http://ieducar-service:8004"
    ieducar_http_timeout: float = Field(default=30.0, gt=0)

    moodle_service_url: str = "http://moodle-service:8003"
    moodle_setup_check_enabled: bool = True
    moodle_setup_check_timeout: float = Field(default=10.0, gt=0)

    institution_id: int = Field(default=1, ge=1)
    sync_lock_scope: str = Field(default="global", description="global | per_kind")

    # Caminho automático: resumos de varredura do ieducar-service (ADR-007).
    ieducar_notices_enabled: bool = True
    ieducar_notice_retry_ttl_ms: int = Field(default=15_000, ge=1_000)
    ieducar_notice_max_attempts: int = Field(default=10, ge=1)