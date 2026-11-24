"""Identidades determinísticas (contrato T1 / v1)."""
from __future__ import annotations


def course_idnumber(instituicao_id: int, ano: int, turma_id: int, disciplina_id: int) -> str:
    return f"ieducar:curso:{instituicao_id}:{ano}:{turma_id}:{disciplina_id}"


def course_shortname(instituicao_id: int, ano: int, turma_id: int, disciplina_id: int) -> str:
    return f"ieducar-{instituicao_id}-{ano}-t{turma_id}-d{disciplina_id}"


def course_fullname(disciplina_nome: str, turma_nome: str, turma_id: int, ano: int) -> str:
    return f"{disciplina_nome} — {turma_nome} (turma {turma_id}) — {ano}"


def teacher_idnumber(professor_id: int) -> str:
    return f"ieducar:professor:{professor_id}"


def teacher_username(professor_id: int) -> str:
    return f"ieducar-professor-{professor_id}"


def student_idnumber(aluno_id: int) -> str:
    return f"ieducar:aluno:{aluno_id}"


def student_username(aluno_id: int) -> str:
    return f"ieducar-aluno-{aluno_id}"


def split_person_name(nome: str) -> tuple[str, str]:
    cleaned = (nome or "").strip()
    if not cleaned:
        return "Sem Nome", "-"
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0], "-"
    return parts[0], " ".join(parts[1:])