"""Normalização das respostas do i-Educar (funções puras portadas do T1)."""
import pytest

from app.core.errors import SourceDataError
from app.domain import payloads


class TestIdentificador:
    def test_aceita_inteiro_e_texto_decimal(self):
        assert payloads.identifier(7) == 7
        assert payloads.identifier("7") == 7

    @pytest.mark.parametrize("valor", [True, False, None, 1.5, "7a", "", "٧"])
    def test_recusa_o_que_nao_e_inteiro_decimal(self, valor):
        with pytest.raises(SourceDataError):
            payloads.identifier(valor)


class TestPaginacao:
    def test_envelope_valido_devolve_linhas_e_ultima_pagina(self):
        rows, last = payloads.page_of(
            {"data": [{"id": 1}], "meta": {"current_page": 1, "last_page": 2}}, 1
        )
        assert rows == [{"id": 1}]
        assert last == 2

    @pytest.mark.parametrize(
        "resposta",
        [
            {"data": "x", "meta": {"current_page": 1, "last_page": 1}},
            {"data": [], "meta": {"current_page": 2, "last_page": 1}},
            {"data": [], "meta": {"current_page": 1, "last_page": "1"}},
            {"data": []},
        ],
    )
    def test_recusa_envelope_invalido(self, resposta):
        with pytest.raises(SourceDataError):
            payloads.page_of(resposta, 1)

    def test_recusa_paginacao_acima_do_limite(self):
        with pytest.raises(SourceDataError):
            payloads.page_of(
                {"data": [], "meta": {"current_page": 1, "last_page": 50}}, 1, max_pages=10
            )


def _matricula(**overrides):
    base = {
        "id": 500,
        "student_id": 42,
        "ativo": 1,
        "ano": 2026,
        "ref_ref_cod_escola": 2,
        "student": {
            "id": 42,
            "ativo": 1,
            "person": {"id": 9, "name": "Carlos Aluno", "email": "carlos@example.com"},
        },
        "enrollments": [
            {
                "id": 1,
                "ref_cod_matricula": 500,
                "ref_cod_turma": 10,
                "ativo": 1,
                "transferido": 0,
                "remanejado": 0,
                "abandono": 0,
                "falecido": 0,
                "reclassificado": 0,
                "data_exclusao": None,
            }
        ],
    }
    base.update(overrides)
    return base


class TestAluno:
    def test_matricula_ativa_vira_registro_com_turma(self):
        record = payloads.student_from_registration(_matricula(), 2026, "2")
        assert record == {
            "aluno_id": 42,
            "nome": "Carlos Aluno",
            "email": "carlos@example.com",
            "turmas": [{"turma_id": 10, "ano": 2026, "escola_id": 2}],
        }

    def test_matricula_inativa_e_ignorada(self):
        assert payloads.student_from_registration(_matricula(ativo=0), 2026, "2") is None

    def test_aluno_inativo_e_ignorado(self):
        row = _matricula()
        row["student"]["ativo"] = 0
        assert payloads.student_from_registration(row, 2026, "2") is None

    @pytest.mark.parametrize("flag", payloads.ENROLMENT_FLAGS)
    def test_enturmacao_com_situacao_impeditiva_nao_gera_turma(self, flag):
        row = _matricula()
        row["enrollments"][0][flag] = 1
        record = payloads.student_from_registration(row, 2026, "2")
        assert record["turmas"] == []

    def test_enturmacao_excluida_nao_gera_turma(self):
        row = _matricula()
        row["enrollments"][0]["data_exclusao"] = "2026-03-01"
        record = payloads.student_from_registration(row, 2026, "2")
        assert record["turmas"] == []

    def test_aluno_divergente_do_vinculo_e_recusado(self):
        row = _matricula(student_id=99)
        with pytest.raises(SourceDataError):
            payloads.student_from_registration(row, 2026, "2")

    def test_matricula_fora_do_escopo_e_recusada(self):
        with pytest.raises(SourceDataError):
            payloads.student_from_registration(_matricula(), 2025, "2")

    def test_merge_reune_turmas_de_paginas_diferentes(self):
        students = {}
        payloads.merge_student(students, payloads.student_from_registration(_matricula(), 2026, "2"))
        outra = _matricula(id=501)
        outra["enrollments"] = [
            {
                "id": 2,
                "ref_cod_matricula": 501,
                "ref_cod_turma": 11,
                "ativo": 1,
                "transferido": 0,
                "remanejado": 0,
                "abandono": 0,
                "falecido": 0,
                "reclassificado": 0,
                "data_exclusao": None,
            }
        ]
        payloads.merge_student(students, payloads.student_from_registration(outra, 2026, "2"))
        assert [t["turma_id"] for t in students[42]["turmas"]] == [10, 11]

    def test_merge_recusa_dados_divergentes_do_mesmo_aluno(self):
        students = {}
        payloads.merge_student(students, payloads.student_from_registration(_matricula(), 2026, "2"))
        outra = _matricula()
        outra["student"]["person"]["name"] = "Outro Nome"
        with pytest.raises(SourceDataError):
            payloads.merge_student(
                students, payloads.student_from_registration(outra, 2026, "2")
            )


class TestProfessor:
    def test_vinculos_agrupados_por_professor(self):
        links = payloads.teacher_links(
            {
                "vinculos": [
                    {
                        "servidor_id": 7,
                        "turma_id": 10,
                        "deleted_at": None,
                        "disciplinas": [{"id": 2}, {"id": 3}],
                    },
                    {
                        "servidor_id": 8,
                        "turma_id": 11,
                        "deleted_at": "2026-01-01",
                        "disciplinas": [{"id": 4}],
                    },
                ]
            }
        )
        assert links == {7: {(10, 2), (10, 3)}}

    def test_vinculo_sem_disciplina_e_recusado(self):
        with pytest.raises(SourceDataError):
            payloads.teacher_links(
                {"vinculos": [{"servidor_id": 7, "turma_id": 10, "deleted_at": None, "disciplinas": []}]}
            )

    def test_professor_ativo_vira_registro_ordenado(self):
        record = payloads.teacher_from_record(
            {"result": {"servidor_id": 7, "ativo": 1, "nome": "Ana", "email": "ana@example.com"}},
            7,
            {(11, 3), (10, 2)},
        )
        assert record["vinculos"] == [
            {"turma_id": 10, "disciplina_id": 2},
            {"turma_id": 11, "disciplina_id": 3},
        ]

    def test_professor_inativo_e_ignorado(self):
        assert (
            payloads.teacher_from_record({"result": {"servidor_id": 7, "ativo": 0}}, 7, set())
            is None
        )

    def test_professor_de_outro_id_e_recusado(self):
        with pytest.raises(SourceDataError):
            payloads.teacher_from_record({"result": {"servidor_id": 9, "ativo": 1}}, 7, set())


class TestCatalogo:
    def test_turma_excluida_fica_fora(self):
        vigentes = payloads.catalog_classes(
            {"turmas": [{"id": 10, "deleted_at": None}, {"id": 11, "deleted_at": "2026-01-01"}]}
        )
        assert [t["id"] for t in vigentes] == [10]

    def test_escopo_da_turma_confere_ano_e_escola(self):
        assert payloads.class_scope({"id": 10, "ano": 2026, "escola_id": 2}, 2026, ["2"]) == (
            10,
            2026,
            2,
        )

    def test_turma_de_outro_ano_e_recusada(self):
        with pytest.raises(SourceDataError):
            payloads.class_scope({"id": 10, "ano": 2025, "escola_id": 2}, 2026, ["2"])

    def test_mapa_vazio_serializado_como_lista_pelo_php(self):
        assert payloads.disciplines_of({"options": []}) == {}

    def test_disciplinas_invalidas_sao_recusadas(self):
        with pytest.raises(SourceDataError):
            payloads.disciplines_of({"options": "x"})
