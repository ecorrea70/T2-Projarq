from app.application.planner import plan_student_sync, plan_teacher_sync
from app.core.config import Settings
from app.infrastructure.ieducar.mock import mock_catalog, mock_students, mock_teachers


def test_plan_teacher_sync_dedupes_courses_and_users() -> None:
    commands = plan_teacher_sync(mock_catalog(), mock_teachers(), Settings(institution_id=1))
    keys = {c.idempotency_key for c in commands}
    assert "course:ieducar:curso:1:2026:10:2" in keys
    assert "user:teacher:7" in keys
    assert any(c.phase == 2 for c in commands)


def test_plan_student_sync_batches_by_course() -> None:
    commands = plan_student_sync(mock_catalog(), mock_students())
    phase1 = [c for c in commands if c.phase == 1]
    phase2 = [c for c in commands if c.phase == 2]
    assert len(phase1) == 3
    assert len(phase2) >= 1
    assert all(c.command_type == "moodle.students.enrol.requested" for c in phase2)