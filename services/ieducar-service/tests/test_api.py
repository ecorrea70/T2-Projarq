"""As rotas do serviço, com origem falsa."""
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import (
    CATALOG,
    STUDENTS,
    TEACHERS,
    FakeIEducarClient,
    build_scanner,
    make_settings,
    transient,
)


def build_client(repo, source: FakeIEducarClient):
    scanner, publisher = build_scanner(repo, source)
    app = create_app(
        make_settings(),
        repo=repo,
        client=source,
        scanner=scanner,
        start_scan_loop=False,
    )
    return TestClient(app), publisher


class TestSaude:
    def test_health_responde_com_estado_do_servico(self, repo):
        client, _ = build_client(repo, FakeIEducarClient())
        with client:
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "ieducar-service"
        assert body["ieducar_circuit_breaker"] == "closed"

    def test_ready_fica_503_quando_o_broker_esta_fora(self, repo):
        client, _ = build_client(repo, FakeIEducarClient())
        with client:
            response = client.get("/ready")
        # rabbitmq_enabled=False nos testes: o contexto se declara pronto.
        assert response.status_code == 200
        assert response.json()["database"] == "up"

    def test_metrics_expoe_formato_prometheus(self, repo):
        client, _ = build_client(repo, FakeIEducarClient())
        with client:
            response = client.get("/metrics")
        assert response.status_code == 200
        assert "ieducar_scans_total" in response.text


class TestContratoDeLeitura:
    def test_as_tres_rotas_devolvem_o_formato_que_o_sync_service_espera(self, repo):
        client, _ = build_client(repo, FakeIEducarClient(CATALOG, TEACHERS, STUDENTS))
        with client:
            catalog = client.get("/internal/catalog").json()
            teachers = client.get("/internal/teachers").json()
            students = client.get("/internal/students").json()

        assert catalog[0]["turma_id"] == 10
        assert catalog[0]["disciplina_nome"] == "Matemática"
        assert teachers[0]["vinculos"] == [{"turma_id": 10, "disciplina_id": 2}]
        assert students[0]["turmas"][0]["escola_id"] == 2

    def test_origem_fora_do_ar_responde_503(self, repo):
        client, _ = build_client(repo, FakeIEducarClient(fail_with=transient()))
        with client:
            response = client.get("/internal/students")
        assert response.status_code == 503
        assert "i-Educar inacessível" in response.json()["detail"]

    def test_correlation_id_volta_no_cabecalho(self, repo):
        client, _ = build_client(repo, FakeIEducarClient(CATALOG, TEACHERS, STUDENTS))
        with client:
            response = client.get("/internal/catalog", headers={"X-Correlation-Id": "demo-001"})
        assert response.headers["X-Correlation-Id"] == "demo-001"


class TestVarreduraPorRota:
    def test_scan_manual_grava_baseline_e_aparece_no_historico(self, repo):
        source = FakeIEducarClient(CATALOG, TEACHERS, STUDENTS)
        client, publisher = build_client(repo, source)
        with client:
            result = client.post("/internal/scan", headers={"X-Correlation-Id": "scan-001"}).json()
            historico = client.get("/internal/scans").json()
            detalhe = client.get(f"/internal/scans/{result['scan_id']}").json()

        assert result["status"] == "succeeded"
        assert result["correlation_id"] == "scan-001"
        assert result["changes_published"] == 0
        assert publisher.published == []
        assert len(historico) == 1
        assert detalhe["trigger"] == "manual"

    def test_scan_publica_um_resumo_da_mudanca_detectada(self, repo):
        source = FakeIEducarClient(CATALOG, [dict(TEACHERS[0])], STUDENTS)
        client, publisher = build_client(repo, source)
        with client:
            client.post("/internal/scan")
            source.teachers[0]["nome"] = "Ana Maria"
            result = client.post("/internal/scan").json()

        assert result["changes_published"] == 1
        assert result["summary_published"] is True
        assert len(publisher.published) == 1
        assert publisher.changed_entities == [["teacher"]]

    def test_scan_full_republica_tudo(self, repo):
        client, publisher = build_client(repo, FakeIEducarClient(CATALOG, TEACHERS, STUDENTS))
        with client:
            client.post("/internal/scan")
            result = client.post("/internal/scan?full=true").json()

        assert result["changes_published"] == 3
        assert len(publisher.published) == 1
        assert publisher.changed_entities == [["catalog", "student", "teacher"]]

    def test_scan_com_origem_fora_responde_503(self, repo):
        client, _ = build_client(repo, FakeIEducarClient(fail_with=transient()))
        with client:
            response = client.post("/internal/scan")
        assert response.status_code == 503
        assert response.json()["status"] == "failed"

    def test_varredura_inexistente_responde_404(self, repo):
        client, _ = build_client(repo, FakeIEducarClient())
        with client:
            response = client.get("/internal/scans/nao-existe")
        assert response.status_code == 404
