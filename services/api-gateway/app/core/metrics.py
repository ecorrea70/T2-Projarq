"""Métricas do gateway."""
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

PROXY_REQUESTS = Counter(
    "gateway_proxy_requests_total",
    "Requisições encaminhadas ao sync-service",
    labelnames=("method", "path", "status_code"),
)
PROXY_DURATION = Histogram(
    "gateway_proxy_duration_seconds",
    "Latência do encaminhamento ao sync-service",
    labelnames=("path",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST