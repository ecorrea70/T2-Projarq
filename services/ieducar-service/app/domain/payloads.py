"""Validação e normalização das respostas do i-Educar.

Portado de `app/adapters/ieducar_payloads.py` do T1 (mesmo grupo), trocando
`IntegrationError` por `SourceDataError`: aqui a distinção entre falha de
transporte e dado inválido da origem é explícita, porque a varredura decide
entre tentar de novo e registrar o problema.

Funções puras: nenhuma faz requisição, lê configuração ou escreve no banco.
Recebem o payload cru e devolvem o registro no formato do contrato, ou levantam
`SourceDataError` quando a resposta não é confiável.
"""
from app.core.errors import SourceDataError

# Situações que excluem uma enturmação, mesmo que ela esteja ativa.
ENROLMENT_FLAGS = ("transferido", "remanejado", "abandono", "falecido", "reclassificado")
BOOLEAN_VALUES = (None, 0, 1, "0", "1", False, True)
MAX_PAGES = 10000


def identifier(value):
    """Aceita só inteiros decimais, inclusive em texto, e nunca booleanos.

    `bool` é subclasse de `int` e passaria pelo isinstance, por isso o teste
    explícito: a intenção não se perde se a regra decimal mudar.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not str(value).isascii()
        or not str(value).isdecimal()
    ):
        raise SourceDataError("Identificador inválido na API do i-Educar.")
    return int(value)


def active(value):
    """O i-Educar representa situações como 1, "1" ou True."""
    return value in (1, "1", True)


def page_of(result, expected, max_pages=MAX_PAGES):
    """Confere o envelope de paginação e devolve as linhas e a última página."""
    rows, meta = result.get("data"), result.get("meta")
    if not isinstance(rows, list) or not isinstance(meta, dict):
        raise SourceDataError("Resposta paginada de matrículas inválida.")
    last = meta.get("last_page")
    if (
        type(last) is not int
        or last < expected
        or meta.get("current_page") != expected
        or last > max_pages
    ):
        raise SourceDataError("Paginação de matrículas inválida.")
    return rows, last


def active_classes(enrollments, registration_id):
    """Turmas em que o aluno está efetivamente enturmado."""
    classes = set()
    for enrollment in enrollments:
        if not isinstance(enrollment, dict) or "ativo" not in enrollment:
            raise SourceDataError("Enturmação inválida na API do i-Educar.")
        if identifier(enrollment.get("ref_cod_matricula")) != registration_id:
            raise SourceDataError("Enturmação não corresponde à matrícula.")
        if any(
            flag not in enrollment or enrollment[flag] not in BOOLEAN_VALUES
            for flag in ENROLMENT_FLAGS
        ):
            raise SourceDataError("Situação da enturmação ausente ou inválida.")
        if (
            not active(enrollment["ativo"])
            or any(active(enrollment[flag]) for flag in ENROLMENT_FLAGS)
            or enrollment.get("data_exclusao")
        ):
            continue
        classes.add(identifier(enrollment.get("ref_cod_turma")))
    return classes


def student_from_registration(row, year_scope, school_scope):
    """Normaliza uma matrícula, ou devolve None se ela não deve ser sincronizada."""
    if not isinstance(row, dict):
        raise SourceDataError("Matrícula inválida na API do i-Educar.")
    if "ativo" not in row:
        raise SourceDataError("Situação da matrícula ausente na API.")
    if not active(row.get("ativo")):
        return None
    student = row.get("student")
    if not isinstance(student, dict):
        raise SourceDataError("A API não incluiu o aluno da matrícula.")
    if "ativo" not in student:
        raise SourceDataError("Situação do aluno ausente na API.")
    if not active(student.get("ativo")):
        return None
    code = identifier(student.get("id"))
    if identifier(row.get("student_id")) != code:
        raise SourceDataError("Aluno divergente do vínculo da matrícula.")
    person = student.get("person")
    if not isinstance(person, dict):
        person = {}
    registration_id = identifier(row.get("id"))
    year = identifier(row.get("ano"))
    school = identifier(row.get("ref_ref_cod_escola"))
    if (year_scope and year != int(year_scope)) or (school_scope and school != int(school_scope)):
        raise SourceDataError("Matrícula fora do escopo solicitado.")
    enrollments = row.get("enrollments")
    if not isinstance(enrollments, list):
        raise SourceDataError("Enturmações ausentes na API do i-Educar.")
    classes = active_classes(enrollments, registration_id)
    return {
        "aluno_id": code,
        "nome": person.get("name") or "",
        "email": person.get("email"),
        "turmas": [{"turma_id": t, "ano": year, "escola_id": school} for t in sorted(classes)],
    }


def merge_student(students, record):
    """Reúne, num só aluno, matrículas vindas de páginas ou escolas diferentes."""
    code = record["aluno_id"]
    if code not in students:
        students[code] = record
        return
    previous = students[code]
    if any(previous[field] != record[field] for field in ("nome", "email")):
        raise SourceDataError("Dados divergentes para o mesmo aluno na API.")
    classes = {
        (r["turma_id"], r["ano"], r["escola_id"]) for r in previous["turmas"] + record["turmas"]
    }
    previous["turmas"] = [
        {"turma_id": t, "ano": y, "escola_id": s} for t, y, s in sorted(classes)
    ]


def teacher_links(result):
    """Agrupa os vínculos docentes vigentes por professor."""
    links = result.get("vinculos")
    if not isinstance(links, list):
        raise SourceDataError("Resposta de vínculos de professores inválida.")
    codes = {}
    for link in links:
        if not isinstance(link, dict) or "deleted_at" not in link:
            raise SourceDataError("Vínculo de professor inválido.")
        if link["deleted_at"] is not None:
            continue
        code = identifier(link.get("servidor_id"))
        classroom = identifier(link.get("turma_id"))
        disciplines = link.get("disciplinas")
        if not isinstance(disciplines, list) or not disciplines:
            raise SourceDataError("Disciplinas ausentes no vínculo docente.")
        for discipline in disciplines:
            if not isinstance(discipline, dict):
                raise SourceDataError("Disciplina inválida no vínculo docente.")
            codes.setdefault(code, set()).add((classroom, identifier(discipline.get("id"))))
    return codes


def teacher_from_record(result, code, links):
    """Normaliza um professor, ou devolve None se ele estiver inativo."""
    record = result.get("result")
    if not isinstance(record, dict) or identifier(record.get("servidor_id")) != code:
        raise SourceDataError("Dados do professor não correspondem ao vínculo.")
    if "ativo" not in record:
        raise SourceDataError("Situação do professor ausente na API.")
    if not active(record["ativo"]):
        return None
    return {
        "professor_id": code,
        "nome": record.get("nome") or "",
        "email": record.get("email"),
        "vinculos": [{"turma_id": t, "disciplina_id": d} for t, d in sorted(links)],
    }


def catalog_classes(result):
    """Turmas vigentes do catálogo, já validadas."""
    classes = result.get("turmas")
    if not isinstance(classes, list):
        raise SourceDataError("Catálogo de turmas inválido.")
    vigentes = []
    for classroom in classes:
        if not isinstance(classroom, dict) or "deleted_at" not in classroom:
            raise SourceDataError("Turma inválida no catálogo.")
        if classroom["deleted_at"] is None:
            vigentes.append(classroom)
    return vigentes


def class_scope(classroom, year_scope, school_scopes):
    """Confere que a turma está no escopo pedido e devolve seus identificadores."""
    code = identifier(classroom.get("id"))
    year = identifier(classroom.get("ano"))
    school = identifier(classroom.get("escola_id"))
    if year != int(year_scope) or str(school) not in school_scopes:
        raise SourceDataError("Turma fora do escopo solicitado.")
    return code, year, school


def disciplines_of(result):
    """Disciplinas da turma. O PHP serializa um mapa vazio como lista."""
    options = result.get("options")
    if options == []:
        options = {}
    if not isinstance(options, dict):
        raise SourceDataError("Catálogo de disciplinas inválido.")
    return options
