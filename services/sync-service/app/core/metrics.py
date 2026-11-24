"""Métricas Prometheus do sync-service."""
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

SYNC_RUNS_STARTED = Counter(
    "sync_runs_started_total",
    "Sincronizações iniciadas",
    labelnames=("kind",),
)
SYNC_RUNS_FINISHED = Counter(
    "sync_runs_finished_total",
    "Sincronizações finalizadas",
    labelnames=("kind", "status"),
)
MOODLE_EVENTS_HANDLED = Counter(
    "sync_moodle_events_total",
    "Eventos do moodle-service processados",
    labelnames=("event_type", "outcome"),
)
COMMAND_PUBLISH = Counter(
    "sync_commands_published_total",
    "Comandos publicados para o moodle-service",
    labelnames=("command_type",),
)
SYNC_DURATION = Histogram(
    "sync_run_duration_seconds",
    "Duração até conclusão da sincronização",
    labelnames=("kind",),
    buckets=(1, 5, 15, 60, 120, 300, 600, 1800, 3600),
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST