"""Hierarquia de exceções do ieducar-service.

A distinção importa para a varredura: erro transitório merece nova tentativa,
erro de dados da origem não melhora com retry e precisa ser visto por alguém.
Nenhuma mensagem deve conter token, access_key ou URL com segredo.
"""


class IEducarServiceError(Exception):
    """Erro base sanitizado do serviço."""


class TransientIntegrationError(IEducarServiceError):
    """Falha temporária (rede, timeout, 5xx, circuito aberto) passível de retry."""


class CircuitBreakerOpenError(TransientIntegrationError):
    """O circuit breaker impediu a chamada ao i-Educar por estar aberto."""


class SourceDataError(IEducarServiceError):
    """A origem respondeu, mas com dados inválidos ou fora do escopo. Retry não resolve."""
