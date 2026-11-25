"""Exceções sanitizadas de domínio e integração do sync-service."""


class SyncPlanningError(ValueError):
    """Erro fail-fast no planejamento de comandos antes de qualquer escrita."""


class OriginUnavailableError(RuntimeError):
    """Indica falha de comunicação ou indisponibilidade do ieducar-service (Cenário 1)."""
