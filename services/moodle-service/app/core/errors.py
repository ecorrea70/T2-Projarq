"""Hierarquia de exceções sanitizadas do moodle-service.

Mensagens de erro nunca devem expor tokens, URLs completas com segredos
ou payloads internos brutos do Moodle.
"""


class MoodleServiceError(Exception):
    """Erro base sanitizado do serviço."""


class TransientIntegrationError(MoodleServiceError):
    """Falha temporária (rede, timeout, 5xx/429, broker ou circuito aberto) passível de retry."""


class CircuitBreakerOpenError(TransientIntegrationError):
    """Indica que o Circuit Breaker impediu a chamada ao Moodle por estar aberto."""


class PermanentDomainError(MoodleServiceError):
    """Erro permanente de negócio, conflito de identidade ou validação; não deve sofrer retry."""


class PostCommitNotificationError(MoodleServiceError):
    """Erro retornado pelo Moodle após provável efetivação (ex.: falha no envio de e-mail SMTP)."""
