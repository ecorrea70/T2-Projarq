"""Configurações do ieducar-service."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    service_name: str = "ieducar-service"
    port: int = Field(default=8004, ge=1, le=65535)

    database_url: str = (
        "postgresql+psycopg://ieducar_service:ieducar_service@localhost:5432/ieducar_service"
    )
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    rabbitmq_enabled: bool = True

    # --- i-Educar (as duas APIs) ---
    ieducar_url: str = "http://ieducar"
    ieducar_token: str = ""
    ieducar_access_key: str = ""
    institution_id: int = Field(default=1, ge=1)
    school_id: str = "2,3"
    year: int = Field(default=2026, ge=2000)

    ieducar_connect_timeout: float = Field(default=5.0, gt=0)
    ieducar_read_timeout: float = Field(default=30.0, gt=0)
    local_max_retries: int = Field(default=2, ge=0)
    local_retry_base_delay: float = Field(default=1.0, ge=0)
    cb_failure_threshold: int = Field(default=5, ge=1)
    cb_recovery_seconds: float = Field(default=30.0, gt=0)

    # --- Varredura ---
    scan_interval_seconds: int = Field(default=60, ge=5)
    scan_on_startup: bool = True
    max_pages: int = Field(default=10000, ge=1)

    def schools(self) -> list[str]:
        return [part.strip() for part in self.school_id.split(",") if part.strip()]
