"""Impressão digital de um registro da origem.

A detecção de mudança do serviço é uma comparação de hash: cada registro lido é
reduzido a um sha256 do seu conteúdo canônico. Se o hash difere do guardado, o
registro mudou; se a chave desapareceu da leitura, o registro foi removido.

Não dependemos de nenhum campo de data do i-Educar, o que também detecta
exclusões, ao custo de ler tudo em cada ciclo.
"""
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal

EntityType = Literal["catalog", "teacher", "student"]
ChangeType = Literal["created", "updated", "removed"]


def fingerprint(record: dict[str, Any]) -> str:
    """Hash estável do registro: ordem de chave não muda o resultado."""
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EntityChange:
    entity_type: str
    entity_key: str
    change_type: str
    fingerprint: str | None


def catalog_key(record: dict[str, Any]) -> str:
    return f"{record['turma_id']}:{record['disciplina_id']}"


def teacher_key(record: dict[str, Any]) -> str:
    return str(record["professor_id"])


def student_key(record: dict[str, Any]) -> str:
    return str(record["aluno_id"])
