"""A varredura: baseline silencioso, detecção por hash, resumo e falha da origem."""
import copy
import json

from app.application.fingerprint import student_key, teacher_key
from app.infrastructure.messaging.schemas import SCAN_SUMMARY_ROUTING_KEY
from tests.conftest import (
    CATALOG,
    STUDENTS,
    TEACHERS,
    FakeIEducarClient,
    build_scanner,
    transient,
)


def _by_key(rows: list[dict], key_of) -> dict[str, dict]:
    return {key_of(row): row for row in rows}


class TestBaseline:
    async def test_primeira_varredura_nao_publica_nada_mas_grava_tudo(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, STUDENTS)
        scanner, publisher = build_scanner(repo, client)

        result = await scanner.scan(trigger="manual")

        assert result["status"] == "succeeded"
        assert result["records_read"] == 3
        assert result["changes_published"] == 0
        assert result["summary_published"] is False
        assert sorted(result["baseline"]) == ["catalog", "student", "teacher"]
        assert publisher.published == []
        assert repo.has_any("teacher") is True

    async def test_segunda_varredura_sem_mudanca_fica_silenciosa(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, STUDENTS)
        scanner, publisher = build_scanner(repo, client)

        await scanner.scan(trigger="manual")
        result = await scanner.scan(trigger="schedule")

        assert result["changes_detected"] == 0
        assert result["changes_published"] == 0
        assert result["summary_published"] is False
        assert publisher.published == []


class TestResumoDaVarredura:
    async def test_uma_mudanca_publica_um_resumo_marcando_so_aquele_tipo(self, repo):
        client = FakeIEducarClient(CATALOG, copy.deepcopy(TEACHERS), STUDENTS)
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        client.teachers[0]["email"] = "ana.nova@example.com"
        result = await scanner.scan(trigger="schedule")

        assert result["changes_published"] == 1
        assert result["summary_published"] is True
        assert len(publisher.published) == 1
        assert publisher.routing_keys == [SCAN_SUMMARY_ROUTING_KEY]

        envelope = publisher.published[0]
        assert envelope.event_type == "ieducar.scan.changed"
        assert envelope.event_version == "1.0"
        assert envelope.producer == "ieducar-service"
        assert envelope.idempotency_key == f"ieducar:scan:{result['scan_id']}"
        assert envelope.causation_id == result["scan_id"]

        entities = envelope.payload["entities"]
        assert entities["teacher"] == {
            "changed": True,
            "changes_detected": 1,
            "fetch_from": "/internal/teachers",
        }
        assert entities["student"]["changed"] is False
        assert entities["catalog"]["changed"] is False
        assert publisher.changed_entities == [["teacher"]]

    async def test_resumo_nao_carrega_registro_nem_diz_qual_registro_mudou(self, repo):
        client = FakeIEducarClient(CATALOG, copy.deepcopy(TEACHERS), STUDENTS)
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        client.teachers[0]["email"] = "ana.nova@example.com"
        await scanner.scan(trigger="schedule")

        corpo = json.dumps(publisher.published[0].payload, ensure_ascii=False)
        assert "ana.nova@example.com" not in corpo
        assert "entity_key" not in corpo
        assert "professor_id" not in corpo

    async def test_varias_mudancas_do_mesmo_tipo_continuam_um_resumo_so(self, repo):
        dois = copy.deepcopy(TEACHERS) + [
            {
                "professor_id": 8,
                "nome": "Bruno Professor",
                "email": "bruno@example.com",
                "vinculos": [{"turma_id": 10, "disciplina_id": 2}],
            }
        ]
        client = FakeIEducarClient(CATALOG, dois, STUDENTS)
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        client.teachers[0]["email"] = "ana.nova@example.com"
        client.teachers[1]["email"] = "bruno.novo@example.com"
        result = await scanner.scan(trigger="schedule")

        assert result["changes_published"] == 2
        assert len(publisher.published) == 1
        entities = publisher.published[0].payload["entities"]
        assert entities["teacher"]["changes_detected"] == 2
        assert publisher.changed_entities == [["teacher"]]

    async def test_mudanca_em_tipos_diferentes_marca_os_dois_no_mesmo_resumo(self, repo):
        client = FakeIEducarClient(
            CATALOG, copy.deepcopy(TEACHERS), copy.deepcopy(STUDENTS)
        )
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        client.teachers[0]["email"] = "ana.nova@example.com"
        client.students[0]["nome"] = "Carlos Aluno Silva"
        await scanner.scan(trigger="schedule")

        assert len(publisher.published) == 1
        assert publisher.changed_entities == [["student", "teacher"]]

    async def test_resumo_carrega_scan_id_e_gatilho(self, repo):
        client = FakeIEducarClient(CATALOG, copy.deepcopy(TEACHERS), STUDENTS)
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        client.teachers[0]["nome"] = "Ana Maria Professora"
        result = await scanner.scan(trigger="schedule")

        payload = publisher.published[0].payload
        assert payload["scan_id"] == result["scan_id"]
        assert payload["trigger"] == "schedule"
        assert payload["full_republish"] is False

    async def test_idempotency_key_e_uma_por_varredura(self, repo):
        client = FakeIEducarClient(CATALOG, copy.deepcopy(TEACHERS), STUDENTS)
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        client.teachers[0]["nome"] = "Ana Maria Professora"
        await scanner.scan(trigger="schedule")
        client.teachers[0]["nome"] = "Ana Maria Professora Silva"
        await scanner.scan(trigger="schedule")

        chaves = [e.idempotency_key for e in publisher.published]
        assert len(chaves) == 2
        assert len(set(chaves)) == 2
        assert all(k.startswith("ieducar:scan:") for k in chaves)


class TestDeteccao:
    """O tipo da mudança é do diff: o resumo só diz em qual entidade houve mudança."""

    def test_registro_novo_vira_created(self, repo):
        mudancas = repo.diff_entities(
            "student",
            _by_key(STUDENTS, student_key),
            scan_id="scan-1",
            correlation_id="corr-1",
        )

        assert [(c.entity_key, c.change_type) for c in mudancas] == [("42", "created")]

    def test_registro_alterado_vira_updated(self, repo):
        alunos = copy.deepcopy(STUDENTS)
        repo.diff_entities(
            "student", _by_key(alunos, student_key), scan_id="s1", correlation_id="c1"
        )

        alunos[0]["nome"] = "Carlos Aluno Silva"
        mudancas = repo.diff_entities(
            "student", _by_key(alunos, student_key), scan_id="s2", correlation_id="c2"
        )

        assert [(c.entity_key, c.change_type) for c in mudancas] == [("42", "updated")]

    def test_registro_que_desapareceu_vira_removed(self, repo):
        repo.diff_entities(
            "student", _by_key(STUDENTS, student_key), scan_id="s1", correlation_id="c1"
        )

        mudancas = repo.diff_entities(
            "student", {}, scan_id="s2", correlation_id="c2"
        )

        assert [(c.entity_key, c.change_type) for c in mudancas] == [("42", "removed")]
        assert mudancas[0].fingerprint is None

    def test_registro_que_volta_depois_de_remocao_vira_created(self, repo):
        registros = _by_key(STUDENTS, student_key)
        repo.diff_entities("student", registros, scan_id="s1", correlation_id="c1")
        repo.diff_entities("student", {}, scan_id="s2", correlation_id="c2")

        mudancas = repo.diff_entities(
            "student", registros, scan_id="s3", correlation_id="c3"
        )

        assert [(c.entity_key, c.change_type) for c in mudancas] == [("42", "created")]

    def test_mesmo_registro_duas_vezes_nao_gera_mudanca(self, repo):
        registros = _by_key(TEACHERS, teacher_key)
        repo.diff_entities("teacher", registros, scan_id="s1", correlation_id="c1")

        assert repo.diff_entities(
            "teacher", registros, scan_id="s2", correlation_id="c2"
        ) == []

    async def test_ordem_das_chaves_nao_conta_como_mudanca(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, copy.deepcopy(STUDENTS))
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        aluno = client.students[0]
        client.students = [
            {
                "turmas": aluno["turmas"],
                "email": aluno["email"],
                "nome": aluno["nome"],
                "aluno_id": aluno["aluno_id"],
            }
        ]
        result = await scanner.scan(trigger="schedule")

        assert result["changes_published"] == 0
        assert result["summary_published"] is False
        assert publisher.published == []


class TestRepublishTotal:
    async def test_full_marca_todos_os_tipos_em_um_resumo_so(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, STUDENTS)
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        result = await scanner.scan(trigger="manual", full=True)

        assert result["changes_published"] == 3
        assert result["summary_published"] is True
        assert len(publisher.published) == 1
        payload = publisher.published[0].payload
        assert payload["full_republish"] is True
        assert publisher.changed_entities == [["catalog", "student", "teacher"]]


class TestFalhaDaOrigem:
    async def test_origem_fora_do_ar_registra_falha_e_nao_publica(self, repo):
        client = FakeIEducarClient(fail_with=transient())
        scanner, publisher = build_scanner(repo, client)

        result = await scanner.scan(trigger="schedule")

        assert result["status"] == "failed"
        assert publisher.published == []
        scan = repo.get_scan(result["scan_id"])
        assert scan["status"] == "failed"
        assert "i-Educar inacessível" in scan["error_message"]

    async def test_falha_nao_corrompe_o_baseline_seguinte(self, repo):
        falhando = FakeIEducarClient(fail_with=transient())
        scanner, publisher = build_scanner(repo, falhando)
        await scanner.scan(trigger="schedule")

        voltou = FakeIEducarClient(CATALOG, TEACHERS, STUDENTS)
        scanner_ok, publisher_ok = build_scanner(repo, voltou)
        result = await scanner_ok.scan(trigger="schedule")

        assert result["status"] == "succeeded"
        assert result["changes_published"] == 0
        assert publisher_ok.published == []


class TestAuditoria:
    async def test_varredura_interrompida_nao_bloqueia_a_proxima(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, STUDENTS)
        scanner, _ = build_scanner(repo, client)
        repo.start_scan("scan-morto", "corr-morta", "schedule")

        assert repo.has_running_scan() is True
        assert repo.mark_stale_scans_interrupted() == 1
        assert repo.has_running_scan() is False

        result = await scanner.scan(trigger="manual")
        assert result["status"] == "succeeded"
        assert repo.get_scan("scan-morto")["status"] == "interrupted"

    async def test_historico_registra_contadores_por_entidade(self, repo):
        client = FakeIEducarClient(CATALOG, copy.deepcopy(TEACHERS), STUDENTS)
        scanner, _ = build_scanner(repo, client)
        await scanner.scan(trigger="startup")
        client.teachers[0]["email"] = "outro@example.com"
        result = await scanner.scan(trigger="manual")

        assert result["by_entity"]["teacher"]["changes_published"] == 1
        assert result["by_entity"]["student"]["changes_published"] == 0
        historico = repo.latest_scans(10)
        assert len(historico) == 2
        assert historico[0]["changes_published"] == 1
        assert historico[0]["trigger"] == "manual"
