"""Autenticação Bearer do contrato público T1/T2."""
import secrets

from fastapi import HTTPException, Request

from app.core.config import Settings


def require_bearer_token(request: Request) -> None:
    settings: Settings = request.app.state.settings
    configured = settings.sync_api_token.strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="SYNC_API_TOKEN não configurado no api-gateway.",
        )

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization Bearer ausente ou inválido.")

    provided = header.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(provided, configured):
        raise HTTPException(status_code=401, detail="Token inválido.")