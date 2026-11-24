"""Testes da camada HTTP (health, readiness, métricas e inspeção interna)."""
from fastapi.testclient import TestClient

from app.core.errors import PermanentDomainError, TransientIntegrationError
from app.main import create_app


class FakeConsumer:
    def __init__(self, ready: bool = True, replayed: int = 2) -> None:
        self.is_consuming = ready
        self.is_ready = ready
        self._replayed = replayed

    async def replay_dlq(self, limit: int = 50) -> int:
        return self._replayed


class FakeClientForApi:
    def __init__(self, state: str = "closed") -> None:
        self.circuit_breaker = type("CB", (), {"state": state})()
        self.teaching_called = False
        self.student_called = False
        self.error: Exception | None = None

    async def check_teaching_setup(self) -> None:
        self.teaching_called = True
        if self.error:
            raise self.error

    async def check_student_setup(self) -> None:
        self.student_called = True
        if self.error:
            raise self.error

    async def aclose(self) -> None:
        return None


class TestApiEndpoints:
    def test_health_and_correlation_header(self, settings, repo) -> None:
        client_stub = FakeClientForApi()
        app = create_app(settings, repo=repo, moodle_client=client_stub, consumer=FakeConsumer(True))
        with TestClient(app) as client:
            resp = client.get("/health", headers={"X-Correlation-Id": "corr-123"})
            assert resp.status_code == 200
            assert resp.headers["X-Correlation-Id"] == "corr-123"
            assert resp.json()["status"] == "ok"
            assert resp.json()["consumer_running"] is True

    def test_readiness_reflects_dependencies_and_cb(self, settings, repo) -> None:
        cfg = settings.model_copy(update={"rabbitmq_enabled": True})
        client_stub = FakeClientForApi(state="open")

        app_ok = create_app(cfg, repo=repo, moodle_client=client_stub, consumer=FakeConsumer(True))
        with TestClient(app_ok) as client:
            resp = client.get("/ready")
            assert resp.status_code == 200
            assert resp.json()["circuit_breaker"] == "open"

        app_down = create_app(cfg, repo=repo, moodle_client=client_stub, consumer=FakeConsumer(False))
        with TestClient(app_down) as client:
            resp = client.get("/ready")
            assert resp.status_code == 503
            assert resp.json()["rabbitmq"] == "down"

    def test_metrics_endpoint_exposes_prometheus_format(self, settings, repo) -> None:
        app = create_app(settings, repo=repo, moodle_client=FakeClientForApi())
        with TestClient(app) as client:
            resp = client.get("/metrics")
            assert resp.status_code == 200
            assert "moodle_circuit_breaker_state" in resp.text

    def test_setup_check_and_run_summary_and_replay(self, settings, repo) -> None:
        client_stub = FakeClientForApi()
        repo.claim_message(
            message_id="m-1",
            event_type="moodle.course.sync.requested",
            event_version="1.0",
            correlation_id="run-abc",
            idempotency_key="k-1",
            payload_hash="h-1",
        )
        repo.mark_message_succeeded("m-1", {"moodle_id": 20}, reconciled=False)

        app = create_app(settings, repo=repo, moodle_client=client_stub, consumer=FakeConsumer(True, replayed=3))
        with TestClient(app) as client:
            assert client.get("/internal/setup-check?kind=teachers").json() == {"status": "ok", "kind": "teachers"}
            assert client_stub.teaching_called is True

            client_stub.error = PermanentDomainError("Categoria ausente")
            assert client.get("/internal/setup-check?kind=students").status_code == 502

            client_stub.error = TransientIntegrationError("Timeout")
            assert client.get("/internal/setup-check?kind=students").status_code == 503

            run_resp = client.get("/internal/runs/run-abc")
            assert run_resp.status_code == 200
            assert run_resp.json()["summary"]["succeeded"] == 1

            replay_resp = client.post("/internal/dlq/replay?limit=10")
            assert replay_resp.status_code == 200
            assert replay_resp.json() == {"replayed": 3}
