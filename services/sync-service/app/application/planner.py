"""Planejamento fail-fast de comandos moodle.*.requested (fase 1 e 2)."""
from __future__ import annotations

from collections import defaultdict

from app.core.config import Settings
from app.core.errors import SyncPlanningError
from app.domain import mapping
from app.domain.models import AlunoRecord, CatalogEntry, PlannedCommand, ProfessorRecord


def _index_catalog(catalog: list[CatalogEntry]) -> dict[tuple[int, int], tuple[CatalogEntry, dict]]:
    """Valida e indexa o catálogo de cursos de forma fail-fast (paridade T1)."""
    courses: dict[tuple[int, int], tuple[CatalogEntry, dict]] = {}
    for entry in catalog:
        if not entry.turma_nome.strip() or not entry.disciplina_nome.strip():
            raise SyncPlanningError("Nome de turma ou disciplina ausente no catálogo.")
        fullname = mapping.course_fullname(
            entry.disciplina_nome, entry.turma_nome, entry.turma_id, entry.ano
        )
        if len(fullname) > 254:
            raise SyncPlanningError("Nome do curso excede o limite de 254 caracteres do Moodle.")
        mapped = {
            "turma_id": entry.turma_id,
            "disciplina_id": entry.disciplina_id,
            "idnumber": mapping.course_idnumber(
                entry.instituicao_id, entry.ano, entry.turma_id, entry.disciplina_id
            ),
            "shortname": mapping.course_shortname(
                entry.instituicao_id, entry.ano, entry.turma_id, entry.disciplina_id
            ),
            "fullname": fullname,
        }
        key = (entry.turma_id, entry.disciplina_id)
        if key in courses and courses[key][1] != mapped:
            raise SyncPlanningError("Curso duplicado com dados divergentes no catálogo.")
        courses[key] = (entry, mapped)
    return courses


def plan_teacher_sync(
    catalog: list[CatalogEntry],
    teachers: list[ProfessorRecord],
    settings: Settings,
) -> list[PlannedCommand]:
    """Planeja cursos (todo o catálogo), contas docentes e vínculos com validação fail-fast."""
    del settings  # instituicao_id vem de cada entrada do catálogo (paridade T1).
    courses = _index_catalog(catalog)

    # Validação fail-fast antes de emitir qualquer comando (paridade T1).
    for teacher in teachers:
        if teacher.erro_origem:
            continue
        if not teacher.vinculos:
            raise SyncPlanningError("Professor sem vínculos docentes válidos na origem.")
        for vinculo in teacher.vinculos:
            if (vinculo.turma_id, vinculo.disciplina_id) not in courses:
                raise SyncPlanningError(
                    "Disciplina do professor não consta no catálogo da turma; nenhuma escrita realizada."
                )

    commands: list[PlannedCommand] = []

    # Fase 1: todos os cursos do catálogo (inclusive disciplinas sem professor vinculado).
    for key in sorted(courses):
        _, mapped = courses[key]
        commands.append(
            PlannedCommand(
                phase=1,
                command_type="moodle.course.sync.requested",
                routing_key="moodle.course.sync.requested.v1",
                idempotency_key=f"course:{mapped['idnumber']}",
                payload=mapped,
            )
        )

    # Fase 1 (usuários docentes) e Fase 2 (vínculos docentes).
    for teacher in teachers:
        if teacher.erro_origem:
            continue
        user_idnumber = mapping.teacher_idnumber(teacher.professor_id)
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
                    "idnumber": user_idnumber,
                    "username": mapping.teacher_username(teacher.professor_id),
                    "firstname": first or None,
                    "lastname": last or None,
                    "email": teacher.email,
                },
            )
        )

        seen_links: set[tuple[int, int]] = set()
        for vinculo in teacher.vinculos:
            key = (vinculo.turma_id, vinculo.disciplina_id)
            if key in seen_links:
                continue
            seen_links.add(key)
            _, mapped = courses[key]
            commands.append(
                PlannedCommand(
                    phase=2,
                    command_type="moodle.teacher.enrol.requested",
                    routing_key="moodle.teacher.enrol.requested.v1",
                    idempotency_key=f"teacher_enrol:{teacher.professor_id}:{mapped['idnumber']}",
                    payload={
                        "professor_id": teacher.professor_id,
                        "turma_id": key[0],
                        "disciplina_id": key[1],
                        "user_idnumber": user_idnumber,
                        "course_idnumber": mapped["idnumber"],
                    },
                )
            )

    return _dedupe_commands(commands)


def plan_student_sync(
    catalog: list[CatalogEntry],
    students: list[AlunoRecord],
) -> list[PlannedCommand]:
    """Planeja contas de alunos (fase 1) e matrículas em lotes de até 100 por curso (fase 2)."""
    courses = _index_catalog(catalog)
    classrooms: dict[tuple[int, int, int], set[tuple[int, int]]] = defaultdict(set)
    for key, (entry, _) in courses.items():
        classrooms[(entry.turma_id, entry.ano, entry.escola_id)].add(key)

    # Validação fail-fast de todos os vínculos de turma antes de emitir comandos (paridade T1).
    student_courses: dict[int, set[tuple[int, int]]] = {}
    for student in students:
        if student.erro_origem:
            continue
        keys: set[tuple[int, int]] = set()
        for turma in student.turmas:
            scope = (turma.turma_id, turma.ano, turma.escola_id)
            if scope not in classrooms:
                raise SyncPlanningError("Turma ativa do aluno sem disciplinas no catálogo do escopo.")
            keys.update(classrooms[scope])
        student_courses[student.aluno_id] = keys

    commands: list[PlannedCommand] = []
    course_students: dict[tuple[int, int], list[dict]] = defaultdict(list)

    for student in students:
        if student.erro_origem:
            continue
        user_idnumber = mapping.student_idnumber(student.aluno_id)
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
                    "idnumber": user_idnumber,
                    "username": mapping.student_username(student.aluno_id),
                    "firstname": first or None,
                    "lastname": last or None,
                    "email": student.email,
                },
            )
        )
        for key in sorted(student_courses[student.aluno_id]):
            course_students[key].append({"aluno_id": student.aluno_id, "user_idnumber": user_idnumber})

    batch_size = 100
    for key in sorted(course_students):
        roster = course_students[key]
        _, mapped = courses[key]
        course_id = mapped["idnumber"]
        for batch_index in range(0, len(roster), batch_size):
            chunk = roster[batch_index : batch_index + batch_size]
            commands.append(
                PlannedCommand(
                    phase=2,
                    command_type="moodle.students.enrol.requested",
                    routing_key="moodle.students.enrol.requested.v1",
                    idempotency_key=f"students_enrol:{course_id}:b{batch_index // batch_size}",
                    payload={
                        "turma_id": key[0],
                        "disciplina_id": key[1],
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
