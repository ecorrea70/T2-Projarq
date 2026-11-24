"""Configurações validadas do moodle-service via variáveis de ambiente."""
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    service_name: str = "moodle-service"
    port: int = Field(default=8003, ge=1, le=65535)

    database_url: str = "postgresql+psycopg://moodle_service:moodle_service@localhost:5432/moodle_service"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    rabbitmq_enabled: bool = True

    moodle_url: str = "http://localhost:8080"
    moodle_token: str = ""
    moodle_category_id: int = Field(default=1, ge=1)
    moodle_teacher_role_id: int = Field(default=3, ge=1)
    moodle_student_role_id: int = Field(default=5, ge=1)

    moodle_connect_timeout: float = Field(default=5.0, gt=0)
    moodle_read_timeout: float = Field(default=15.0, gt=0)

    local_max_retries: int = Field(default=2, ge=0)
    local_retry_base_delay: float = Field(default=1.0, ge=0)

    rabbitmq_max_retries: int = Field(default=3, ge=0)
    rabbitmq_retry_ttl_ms: int = Field(default=10000, ge=100)
    rabbitmq_prefetch_count: int = Field(default=5, ge=1)

    cb_failure_threshold: int = Field(default=5, ge=1)
    cb_recovery_seconds: float = Field(default=30.0, gt=0)

    fault_inject_drop_after_course_create: bool = False
    fault_inject_target_idnumber: str = ""

    @field_validator("moodle_url")
    @classmethod
    def validate_moodle_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        parsed = urlsplit(cleaned)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("MOODLE_URL inválida: informe HTTP/HTTPS sem credenciais, query ou fragmento.")
        return cleaned

    @field_validator("fault_inject_target_idnumber")
    @classmethod
    def normalize_target_idnumber(cls, value: str) -> str:
        return value.strip()
