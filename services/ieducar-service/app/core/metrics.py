"""Métricas Prometheus do ieducar-service."""
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

SCANS_TOTAL = Counter(
    "ieducar_scans_total",
    "Varreduras da origem concluídas, por gatilho e resultado.",
    labelnames=("trigger", "status"),
)
SCAN_DURATION = Histogram(
    "ieducar_scan_duration_seconds",
    "Duração de uma varredura completa da origem.",
)
RECORDS_READ = Counter(
    "ieducar_records_read_total",
    "Registros lidos da origem, por tipo de entidade.",
    labelnames=("entity_type",),
)
CHANGES_DETECTED = Counter(
    "ieducar_changes_detected_total",
    "Mudanças detectadas na origem, por entidade e tipo de mudança.",
    labelnames=("entity_type", "change_type"),
)
SCAN_SUMMARIES_PUBLISHED = Counter(
    "ieducar_scan_summaries_published_total",
    "Resumos de varredura publicados em integration.events, por gatilho.",
    labelnames=("trigger",),
)
API_REQUESTS = Counter(
    "ieducar_api_requests_total",
    "Chamadas às APIs do i-Educar, por recurso e resultado.",
    labelnames=("resource", "outcome"),
)
API_DURATION = Histogram(
    "ieducar_api_request_duration_seconds",
    "Duração das chamadas às APIs do i-Educar.",
    labelnames=("resource",),
)
CIRCUIT_BREAKER_STATE = Gauge(
    "ieducar_circuit_breaker_state",
    "Estado do circuit breaker do i-Educar (0 fechado, 1 meio-aberto, 2 aberto).",
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
