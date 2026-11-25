"""A varredura: baseline silencioso, detecção por hash e falha da origem."""
import copy

from tests.conftest import (
    CATALOG,
    STUDENTS,
    TEACHERS,
    FakeIEducarClient,
    RecordingPublisher,
    build_scanner,
    transient,
)


class TestBaseline:
    async def test_primeira_varredura_nao_publica_nada_mas_grava_tudo(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, STUDENTS)
        scanner, publisher = build_scanner(repo, client)

        result = await scanner.scan(trigger="manual")

        assert result["status"] == "succeeded"
        assert result["records_read"] == 3
        assert result["changes_published"] == 0
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
        assert publisher.published == []


class TestDeteccao:
    async def test_mudanca_em_um_registro_publica_um_aviso(self, repo):
        client = FakeIEducarClient(CATALOG, copy.deepcopy(TEACHERS), STUDENTS)
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        client.teachers[0]["email"] = "ana.nova@example.com"
        result = await scanner.scan(trigger="schedule")

        assert result["changes_published"] == 1
        assert publisher.changes == [("teacher", "7", "updated")]
        envelope = publisher.published[0]
        assert envelope.event_type == "ieducar.teacher.changed"
        assert envelope.event_version == "1.0"
        assert envelope.producer == "ieducar-service"
        assert envelope.payload["identity"] == {"professor_id": 7}
        assert envelope.payload["fetch_from"] == "/internal/teachers"
        # Aviso magro: a mensagem não carrega o registro.
        assert "email" not in envelope.payload

    async def test_registro_novo_vira_created(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, copy.deepcopy(STUDENTS))
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        client.students.append(
            {
                "aluno_id": 43,
                "nome": "Dani Aluna",
                "email": "dani@example.com",
                "turmas": [{"turma_id": 10, "ano": 2026, "escola_id": 2}],
            }
        )
        result = await scanner.scan(trigger="schedule")

        assert publisher.changes == [("student", "43", "created")]
        assert result["changes_published"] == 1

    async def test_registro_que_desapareceu_vira_removed(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, copy.deepcopy(STUDENTS))
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        client.students = []
        await scanner.scan(trigger="schedule")

        assert publisher.changes == [("student", "42", "removed")]
        assert publisher.published[0].payload["fingerprint"] is None

    async def test_registro_que_volta_depois_de_remocao_vira_created(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, copy.deepcopy(STUDENTS))
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")
        guardados = copy.deepcopy(client.students)

        client.students = []
        await scanner.scan(trigger="schedule")
        client.students = guardados
        await scanner.scan(trigger="schedule")

        assert publisher.changes == [
            ("student", "42", "removed"),
            ("student", "42", "created"),
        ]

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
        assert publisher.published == []

    async def test_idempotency_key_repete_para_a_mesma_mudanca(self, repo):
        client = FakeIEducarClient(CATALOG, copy.deepcopy(TEACHERS), STUDENTS)
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")
        client.teachers[0]["nome"] = "Ana Maria Professora"
        await scanner.scan(trigger="schedule")

        primeira = publisher.published[0].idempotency_key
        await scanner.scan(trigger="manual", full=True)
        republicado = [
            e.idempotency_key
            for e in publisher.published
            if e.payload["entity_key"] == "7" and e.payload["entity_type"] == "teacher"
        ]

        assert primeira in republicado


class TestRepublishTotal:
    async def test_full_publica_um_aviso_por_registro_vigente(self, repo):
        client = FakeIEducarClient(CATALOG, TEACHERS, STUDENTS)
        scanner, publisher = build_scanner(repo, client)
        await scanner.scan(trigger="manual")

        result = await scanner.scan(trigger="manual", full=True)

        assert result["changes_published"] == 3
        assert sorted(c[0] for c in publisher.changes) == ["catalog", "student", "teacher"]
        assert {c[2] for c in publisher.changes} == {"updated"}


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
