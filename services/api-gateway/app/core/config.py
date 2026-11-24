"""Configurações do api-gateway."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "api-gateway"
    port: int = Field(default=8000, ge=1, le=65535)

    sync_api_token: str = ""
    sync_service_url: str = "http://sync-service:8002"
    sync_http_timeout: float = Field(default=30.0, gt=0)

    aggregate_health: bool = True
    health_probe_timeout: float = Field(default=3.0, gt=0)