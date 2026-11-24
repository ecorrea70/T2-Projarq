"""DTOs de domínio para extração (contrato ieducar-service)."""
from pydantic import BaseModel, Field


class VinculoProfessor(BaseModel):
    turma_id: int = Field(ge=1)
    disciplina_id: int = Field(ge=1)


class ProfessorRecord(BaseModel):
    professor_id: int = Field(ge=0)
    nome: str = ""
    email: str | None = None
    vinculos: list[VinculoProfessor] = Field(default_factory=list)
    erro_origem: str | None = None


class TurmaAluno(BaseModel):
    turma_id: int = Field(ge=1)
    ano: int = Field(ge=2000)
    escola_id: int = Field(ge=1)


class AlunoRecord(BaseModel):
    aluno_id: int = Field(ge=0)
    nome: str = ""
    email: str | None = None
    turmas: list[TurmaAluno] = Field(default_factory=list)
    erro_origem: str | None = None


class CatalogEntry(BaseModel):
    instituicao_id: int = Field(ge=1)
    escola_id: int = Field(ge=1)
    ano: int = Field(ge=2000)
    turma_id: int = Field(ge=1)
    turma_nome: str = ""
    disciplina_id: int = Field(ge=1)
    disciplina_nome: str = ""


class PlannedCommand(BaseModel):
    phase: int = Field(ge=1, le=2)
    command_type: str
    routing_key: str
    idempotency_key: str
    payload: dict