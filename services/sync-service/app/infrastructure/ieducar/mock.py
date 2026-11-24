"""Dados de demonstração até o ieducar-service estar disponível."""
from app.domain.models import AlunoRecord, CatalogEntry, ProfessorRecord, TurmaAluno, VinculoProfessor


def mock_catalog() -> list[CatalogEntry]:
    return [
        CatalogEntry(
            instituicao_id=1,
            escola_id=1,
            ano=2026,
            turma_id=10,
            turma_nome="Turma A",
            disciplina_id=2,
            disciplina_nome="Matemática",
        ),
        CatalogEntry(
            instituicao_id=1,
            escola_id=1,
            ano=2026,
            turma_id=11,
            turma_nome="Turma B",
            disciplina_id=3,
            disciplina_nome="História",
        ),
    ]


def mock_teachers() -> list[ProfessorRecord]:
    return [
        ProfessorRecord(
            professor_id=7,
            nome="Ana Professora",
            email="ana.prof@example.com",
            vinculos=[
                VinculoProfessor(turma_id=10, disciplina_id=2),
            ],
        ),
        ProfessorRecord(
            professor_id=8,
            nome="Bruno",
            email="bruno.prof@example.com",
            vinculos=[
                VinculoProfessor(turma_id=11, disciplina_id=3),
            ],
        ),
    ]


def mock_students() -> list[AlunoRecord]:
    return [
        AlunoRecord(
            aluno_id=1,
            nome="Carlos Aluno",
            email="carlos@example.com",
            turmas=[TurmaAluno(turma_id=10, ano=2026, escola_id=1)],
        ),
        AlunoRecord(
            aluno_id=2,
            nome="Diana",
            email="diana@example.com",
            turmas=[TurmaAluno(turma_id=10, ano=2026, escola_id=1)],
        ),
        AlunoRecord(
            aluno_id=3,
            nome="Eduardo Silva",
            email=None,
            turmas=[TurmaAluno(turma_id=11, ano=2026, escola_id=1)],
        ),
    ]