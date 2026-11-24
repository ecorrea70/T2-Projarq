"""Planejamento fail-fast de comandos moodle.*.requested (fase 1 e 2)."""
from __future__ import annotations

from app.core.config import Settings
from app.domain import mapping
from app.domain.models import AlunoRecord, CatalogEntry, PlannedCommand, ProfessorRecord


def _catalog_index(catalog: list[CatalogEntry]) -> dict[tuple[int, int], CatalogEntry]:
    return {(c.turma_id, c.disciplina_id): c for c in catalog}


def plan_teacher_sync(
    catalog: list[CatalogEntry],
    teachers: list[ProfessorRecord],
    settings: Settings,
) -> list[PlannedCommand]:
    commands: list[PlannedCommand] = []
    cat = _catalog_index(catalog)
    seen_teachers: set[int] = set()
    inst = settings.institution_id

    for teacher in teachers:
        if teacher.erro_origem:
            continue
        for vinculo in teacher.vinculos:
            entry = cat.get((vinculo.turma_id, vinculo.disciplina_id))
            if entry is None:
                continue
            idnum = mapping.course_idnumber(inst, entry.ano, entry.turma_id, entry.disciplina_id)
            course_key = f"course:{idnum}"
            commands.append(
                PlannedCommand(
                    phase=1,
                    command_type="moodle.course.sync.requested",
                    routing_key="moodle.course.sync.requested.v1",
                    idempotency_key=course_key,
                    payload={
                        "turma_id": entry.turma_id,
                        "disciplina_id": entry.disciplina_id,
                        "idnumber": idnum,
                        "shortname": mapping.course_shortname(
                            inst, entry.ano, entry.turma_id, entry.disciplina_id
                        ),
                        "fullname": mapping.course_fullname(
                            entry.disciplina_nome, entry.turma_nome, entry.turma_id, entry.ano
                        ),
                    },
                )
            )

            if teacher.professor_id not in seen_teachers:
                seen_teachers.add(teacher.professor_id)
                user_id = mapping.teacher_idnumber(teacher.professor_id)
                first, last = mapping.split_person_name(teacher.nome)
                commands.append(
                    PlannedCommand(
                        phase=1,
                        command_type="moodle.user.sync.requested",
                        routing_key="moodle.user.sync.requested.v1",
                        idempotency_key=f"user:teacher:{teacher.professor_id}",
                        payload={
                            "kind": "teacher",
                            "origin_id": teacher.professor_id,
                            "idnumber": user_id,
                            "username": mapping.teacher_username(teacher.professor_id),
                            "firstname": first,
                            "lastname": last,
                            "email": teacher.email,
                        },
                    )
                )

            user_id = mapping.teacher_idnumber(teacher.professor_id)
            enrol_key = f"teacher_enrol:{teacher.professor_id}:{idnum}"
            commands.append(
                PlannedCommand(
                    phase=2,
                    command_type="moodle.teacher.enrol.requested",
                    routing_key="moodle.teacher.enrol.requested.v1",
                    idempotency_key=enrol_key,
                    payload={
                        "professor_id": teacher.professor_id,
                        "turma_id": entry.turma_id,
                        "disciplina_id": entry.disciplina_id,
                        "user_idnumber": user_id,
                        "course_idnumber": idnum,
                    },
                )
            )

    return _dedupe_commands(commands)


def plan_student_sync(
    catalog: list[CatalogEntry],
    students: list[AlunoRecord],
) -> list[PlannedCommand]:
    commands: list[PlannedCommand] = []
    cat_by_turma: dict[int, list[CatalogEntry]] = {}
    for row in catalog:
        cat_by_turma.setdefault(row.turma_id, []).append(row)

    for student in students:
        if student.erro_origem:
            continue
        first, last = mapping.split_person_name(student.nome)
        commands.append(
            PlannedCommand(
                phase=1,
                command_type="moodle.user.sync.requested",
                routing_key="moodle.user.sync.requested.v1",
                idempotency_key=f"user:student:{student.aluno_id}",
                payload={
                    "kind": "student",
                    "origin_id": student.aluno_id,
                    "idnumber": mapping.student_idnumber(student.aluno_id),
                    "username": mapping.student_username(student.aluno_id),
                    "firstname": first,
                    "lastname": last,
                    "email": student.email,
                },
            )
        )

    course_students: dict[str, list[dict]] = {}
    for student in students:
        if student.erro_origem:
            continue
        user_id = mapping.student_idnumber(student.aluno_id)
        for turma in student.turmas:
            for entry in cat_by_turma.get(turma.turma_id, []):
                if entry.ano != turma.ano:
                    continue
                course_id = mapping.course_idnumber(
                    entry.instituicao_id, entry.ano, entry.turma_id, entry.disciplina_id
                )
                course_students.setdefault(course_id, []).append(
                    {"aluno_id": student.aluno_id, "user_idnumber": user_id}
                )

    batch_size = 100
    for course_id, roster in course_students.items():
        entry = next(
            (
                c
                for c in catalog
                if mapping.course_idnumber(c.instituicao_id, c.ano, c.turma_id, c.disciplina_id)
                == course_id
            ),
            None,
        )
        if entry is None:
            continue
        for batch_index in range(0, len(roster), batch_size):
            chunk = roster[batch_index : batch_index + batch_size]
            commands.append(
                PlannedCommand(
                    phase=2,
                    command_type="moodle.students.enrol.requested",
                    routing_key="moodle.students.enrol.requested.v1",
                    idempotency_key=f"students_enrol:{course_id}:b{batch_index // batch_size}",
                    payload={
                        "turma_id": entry.turma_id,
                        "disciplina_id": entry.disciplina_id,
                        "course_idnumber": course_id,
                        "students": chunk,
                    },
                )
            )

    return _dedupe_commands(commands)


def _dedupe_commands(commands: list[PlannedCommand]) -> list[PlannedCommand]:
    seen: set[str] = set()
    ordered: list[PlannedCommand] = []
    for cmd in commands:
        if cmd.idempotency_key in seen:
            continue
        seen.add(cmd.idempotency_key)
        ordered.append(cmd)
    return ordered