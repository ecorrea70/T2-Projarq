import pytest

from app.application.planner import plan_student_sync, plan_teacher_sync
from app.core.config import Settings
from app.core.errors import SyncPlanningError
from app.domain import mapping
from app.domain.models import AlunoRecord, CatalogEntry, ProfessorRecord, TurmaAluno, VinculoProfessor
from app.infrastructure.ieducar.mock import mock_catalog, mock_students, mock_teachers


def test_plan_teacher_sync_dedupes_courses_and_users() -> None:
    commands = plan_teacher_sync(mock_catalog(), mock_teachers(), Settings(institution_id=1))
    keys = {c.idempotency_key for c in commands}
    assert "course:ieducar:curso:1:2026:10:2" in keys
    assert "user:teacher:7" in keys
    assert any(c.phase == 2 for c in commands)


def test_plan_teacher_sync_includes_unlinked_catalog_disciplines() -> None:
    catalog = mock_catalog() + [
        CatalogEntry(
            instituicao_id=1,
            escola_id=1,
            ano=2026,
            turma_id=10,
            turma_nome="Turma A",
            disciplina_id=99,
            disciplina_nome="Artes",
        )
    ]
    commands = plan_teacher_sync(catalog, mock_teachers(), Settings(institution_id=1))
    keys = {c.idempotency_key for c in commands}
    assert "course:ieducar:curso:1:2026:10:99" in keys


def test_plan_teacher_sync_aborts_fail_fast_when_link_missing_from_catalog() -> None:
    bad_teacher = ProfessorRecord(
        professor_id=7,
        nome="Ana Professora",
        email="ana@example.com",
        vinculos=[VinculoProfessor(turma_id=999, disciplina_id=2)],
    )
    with pytest.raises(SyncPlanningError, match="catálogo"):
        plan_teacher_sync(mock_catalog(), [bad_teacher], Settings(institution_id=1))


def test_plan_student_sync_batches_by_course() -> None:
    commands = plan_student_sync(mock_catalog(), mock_students())
    phase1 = [c for c in commands if c.phase == 1]
    phase2 = [c for c in commands if c.phase == 2]
    assert len(phase1) == 3
    assert len(phase2) >= 1
    assert all(c.command_type == "moodle.students.enrol.requested" for c in phase2)


def test_plan_student_sync_aborts_fail_fast_on_unknown_school_scope() -> None:
    student_wrong_school = AlunoRecord(
        aluno_id=1,
        nome="Carlos Aluno",
        email="carlos@example.com",
        turmas=[TurmaAluno(turma_id=10, ano=2026, escola_id=99)],
    )
    with pytest.raises(SyncPlanningError, match="escopo"):
        plan_student_sync(mock_catalog(), [student_wrong_school])


def test_split_person_name_matches_t1_invariants() -> None:
    assert mapping.split_person_name("Ana Maria da Silva") == ("Ana Maria da", "Silva")
    assert mapping.split_person_name("Aluno001") == ("Aluno001", "-")
    assert mapping.split_person_name("   ") == ("", "")
