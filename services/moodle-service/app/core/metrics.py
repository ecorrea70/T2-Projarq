"""Métricas Prometheus expostas pelo moodle-service."""
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

MESSAGES_PROCESSED = Counter(
    "moodle_messages_processed_total",
    "Total de mensagens processadas pelo moodle-service",
    ["event_type", "outcome"],
)

MESSAGE_PROCESSING_DURATION = Histogram(
    "moodle_message_processing_duration_seconds",
    "Duração do processamento de mensagens no moodle-service",
    ["event_type"],
)

MOODLE_API_REQUESTS = Counter(
    "moodle_api_requests_total",
    "Total de requisições enviadas ao Web Service REST do Moodle",
    ["wsfunction", "outcome"],
)

MOODLE_API_DURATION = Histogram(
    "moodle_api_request_duration_seconds",
    "Duração das chamadas ao Web Service REST do Moodle",
    ["wsfunction"],
)

CIRCUIT_BREAKER_STATE = Gauge(
    "moodle_circuit_breaker_state",
    "Estado do Circuit Breaker do Moodle (0=closed, 1=half_open, 2=open)",
)


def render_metrics() -> tuple[bytes, str]:
    """Serializa as métricas no formato esperado pelo Prometheus."""
    return generate_latest(), CONTENT_TYPE_LATEST
