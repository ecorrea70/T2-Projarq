"""Casos de uso idempotentes e política de retry local com reconciliação."""
import asyncio
from collections.abc import Awaitable, Callable
import random
from typing import Any, TypeVar

from email_validator import EmailNotValidError, validate_email
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import (
    CircuitBreakerOpenError,
    PermanentDomainError,
    PostCommitNotificationError,
    TransientIntegrationError,
)
from app.core.logging import get_logger
from app.domain.contracts import MoodleGateway, StateRepository
from app.domain.models import HandlerOutcome
from app.infrastructure.messaging.schemas import (
    CourseSyncRequestedPayload,
    MessageEnvelope,
    StudentsEnrolRequestedPayload,
    TeacherEnrolRequestedPayload,
    UserSyncRequestedPayload,
)

T = TypeVar("T")
_log = get_logger("handlers")


async def run_with_local_retry(
    operation: Callable[[int], Awaitable[T]],
    max_retries: int,
    base_delay: float,
) -> T:
    """Executa operação com retry exponencial curto para falhas transitórias."""
    for attempt in range(1, max_retries + 2):
        try:
            return await operation(attempt)
        except CircuitBreakerOpenError:
            raise
        except TransientIntegrationError as exc:
            if attempt > max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            if delay > 0:
                delay += random.uniform(0.0, 0.2)
            _log.warning(
                "Falha transitória na tentativa %d; reagendando retry local em %.2fs: %s",
                attempt,
                delay,
                exc,
                extra={"attempt": attempt},
            )
            if delay > 0:
                await asyncio.sleep(delay)
    raise RuntimeError("Loop de retry encerrado inesperadamente.")


async def _resolve_entity_moodle_id(
    gateway: MoodleGateway,
    repo: StateRepository,
    entity_type: str,
    idnumber: str,
    correlation_id: str,
) -> int | None:
    cached = repo.get_entity_by_idnumber(idnumber)
    if cached is not None:
        return int(cached.moodle_id)

    if entity_type == "course":
        found = await gateway.find_course("idnumber", idnumber)
    else:
        found = await gateway.find_user("idnumber", idnumber)

    if len(found) > 1:
        raise PermanentDomainError(f"Mais de um registro possui a identidade {idnumber}.")
    if not found:
        return None

    moodle_id = int(found[0]["id"])
    repo.upsert_entity(
        entity_type=entity_type,
        idnumber=idnumber,
        moodle_id=moodle_id,
        shortname_or_username=found[0].get("shortname") or found[0].get("username"),
        email=found[0].get("email"),
        origin_id=None,
        last_sync_status="existing",
        last_correlation_id=correlation_id,
    )
    return moodle_id


async def handle_course_sync(
    envelope: MessageEnvelope,
    gateway: MoodleGateway,
    repo: StateRepository,
    *,
    redelivered: bool = False,
    attempt: int = 1,
) -> HandlerOutcome:
    """Sincroniza um curso de forma idempotente via idnumber antes de criar."""
    try:
        payload = CourseSyncRequestedPayload.model_validate(envelope.payload)
    except ValidationError as exc:
        raise PermanentDomainError(f"Payload inválido para curso: {exc.errors()[0]['msg']}") from None

    existing = await gateway.find_course("idnumber", payload.idnumber)
    if len(existing) > 1:
        raise PermanentDomainError("Mais de um curso possui a mesma identidade.")

    if existing:
        moodle_id = int(existing[0]["id"])
        is_reconciled = redelivered or attempt > 1
        repo.upsert_entity(
            entity_type="course",
            idnumber=payload.idnumber,
            moodle_id=moodle_id,
            shortname_or_username=payload.shortname,
            email=None,
            origin_id=f"{payload.turma_id}:{payload.disciplina_id}",
            last_sync_status="reconciled" if is_reconciled else "existing",
            last_correlation_id=envelope.correlation_id,
        )
        return HandlerOutcome(
            event_type="moodle.course.synced",
            reconciled=is_reconciled,
            result={
                "turma_id": payload.turma_id,
                "disciplina_id": payload.disciplina_id,
                "idnumber": payload.idnumber,
                "shortname": payload.shortname,
                "status": "existente",
                "reconciled": is_reconciled,
                "moodle_id": moodle_id,
            },
        )

    if await gateway.find_course("shortname", payload.shortname):
        raise PermanentDomainError("Nome curto ocupado por curso sem a identidade esperada.")

    moodle_id = await gateway.create_course(
        {
            "idnumber": payload.idnumber,
            "shortname": payload.shortname,
            "fullname": payload.fullname,
        }
    )
    repo.upsert_entity(
        entity_type="course",
        idnumber=payload.idnumber,
        moodle_id=moodle_id,
        shortname_or_username=payload.shortname,
        email=None,
        origin_id=f"{payload.turma_id}:{payload.disciplina_id}",
        last_sync_status="created",
        last_correlation_id=envelope.correlation_id,
    )
    return HandlerOutcome(
        event_type="moodle.course.synced",
        reconciled=False,
        result={
            "turma_id": payload.turma_id,
            "disciplina_id": payload.disciplina_id,
            "idnumber": payload.idnumber,
            "shortname": payload.shortname,
            "status": "criado",
            "reconciled": False,
            "moodle_id": moodle_id,
        },
    )


async def handle_user_sync(
    envelope: MessageEnvelope,
    gateway: MoodleGateway,
    repo: StateRepository,
    *,
    redelivered: bool = False,
    attempt: int = 1,
) -> HandlerOutcome:
    """Sincroniza conta de professor ou aluno preservando validação de identidade prévia."""
    try:
        payload = UserSyncRequestedPayload.model_validate(envelope.payload)
    except ValidationError as exc:
        raise PermanentDomainError(f"Payload inválido para usuário: {exc.errors()[0]['msg']}") from None

    # Identidade é verificada antes de exigir nome/e-mail: contas já sincronizadas
    # continuam reconhecidas mesmo se o cadastro de origem estiver incompleto.
    existing = await gateway.find_user("idnumber", payload.idnumber)
    if len(existing) > 1:
        label = "aluno" if payload.kind == "student" else "professor"
        raise PermanentDomainError(f"Mais de uma conta possui a identidade do {label}.")

    if existing:
        moodle_id = int(existing[0]["id"])
        is_reconciled = redelivered or attempt > 1
        repo.upsert_entity(
            entity_type=payload.kind,
            idnumber=payload.idnumber,
            moodle_id=moodle_id,
            shortname_or_username=payload.username,
            email=payload.email,
            origin_id=str(payload.origin_id),
            last_sync_status="reconciled" if is_reconciled else "existing",
            last_correlation_id=envelope.correlation_id,
        )
        return HandlerOutcome(
            event_type="moodle.user.synced",
            reconciled=is_reconciled,
            result={
                "kind": payload.kind,
                "origin_id": payload.origin_id,
                "idnumber": payload.idnumber,
                "username": payload.username,
                "status": "existente",
                "reconciled": is_reconciled,
                "moodle_id": moodle_id,
            },
        )

    firstname = " ".join((payload.firstname or "").split())
    lastname = " ".join((payload.lastname or "").split())
    if not firstname:
        raise PermanentDomainError("Cadastro sem nome.")
    if not lastname:
        lastname = "-"
    if len(firstname) > 100 or len(lastname) > 100:
        raise PermanentDomainError("Nome e sobrenome devem ter até 100 caracteres cada.")

    try:
        email = validate_email(str(payload.email or "").strip(), check_deliverability=False).normalized
        if len(email) > 100:
            raise PermanentDomainError("E-mail excede o limite do Moodle.")
    except EmailNotValidError:
        raise PermanentDomainError("Cadastro sem e-mail válido.") from None

    if await gateway.find_user("username", payload.username):
        raise PermanentDomainError("Login ocupado por uma conta sem o vínculo esperado.")
    if await gateway.find_user("email", email):
        raise PermanentDomainError("E-mail já utilizado por outra conta no Moodle.")

    moodle_id = await gateway.create_user(
        {
            "idnumber": payload.idnumber,
            "username": payload.username,
            "firstname": firstname,
            "lastname": lastname,
            "email": email,
        }
    )
    repo.upsert_entity(
        entity_type=payload.kind,
        idnumber=payload.idnumber,
        moodle_id=moodle_id,
        shortname_or_username=payload.username,
        email=email,
        origin_id=str(payload.origin_id),
        last_sync_status="created",
        last_correlation_id=envelope.correlation_id,
    )
    return HandlerOutcome(
        event_type="moodle.user.synced",
        reconciled=False,
        result={
            "kind": payload.kind,
            "origin_id": payload.origin_id,
            "idnumber": payload.idnumber,
            "username": payload.username,
            "status": "criado",
            "reconciled": False,
            "moodle_id": moodle_id,
        },
    )


async def handle_teacher_enrol(
    envelope: MessageEnvelope,
    gateway: MoodleGateway,
    repo: StateRepository,
    settings: Settings,
    *,
    redelivered: bool = False,
    attempt: int = 1,
) -> HandlerOutcome:
    """Matricula um professor no curso com papel docente (roleid=3) e confirma papel."""
    try:
        payload = TeacherEnrolRequestedPayload.model_validate(envelope.payload)
    except ValidationError as exc:
        raise PermanentDomainError(f"Payload inválido para matrícula docente: {exc.errors()[0]['msg']}") from None

    user_id = await _resolve_entity_moodle_id(
        gateway, repo, "teacher", payload.user_idnumber, envelope.correlation_id
    )
    if user_id is None:
        raise PermanentDomainError("Professor não encontrado no Moodle para matrícula.")

    course_id = await _resolve_entity_moodle_id(
        gateway, repo, "course", payload.course_idnumber, envelope.correlation_id
    )
    if course_id is None:
        raise PermanentDomainError("Curso não encontrado no Moodle para matrícula docente.")

    participants = await gateway.course_teachers(course_id)
    warning: str | None = None
    if user_id in participants:
        status = "existente"
        is_reconciled = redelivered or attempt > 1
    else:
        try:
            await gateway.enrol_teacher(user_id, course_id)
        except PostCommitNotificationError as exc:
            warning = str(exc)

        confirmed = await gateway.course_teachers(course_id)
        if user_id not in confirmed:
            raise PermanentDomainError(
                warning or "Matrícula enviada, mas papel docente não confirmado; reexecute para conferir."
            )
        status = "vinculado"
        is_reconciled = bool(warning) or redelivered or attempt > 1

    repo.upsert_enrolment(
        course_idnumber=payload.course_idnumber,
        user_idnumber=payload.user_idnumber,
        role_id=settings.moodle_teacher_role_id,
        moodle_course_id=course_id,
        moodle_user_id=user_id,
        status="reconciled_after_warning" if warning else ("existing" if status == "existente" else "enrolled"),
        warning_message=warning,
        last_correlation_id=envelope.correlation_id,
    )
    result: dict[str, Any] = {
        "professor_id": payload.professor_id,
        "turma_id": payload.turma_id,
        "disciplina_id": payload.disciplina_id,
        "user_idnumber": payload.user_idnumber,
        "course_idnumber": payload.course_idnumber,
        "moodle_user_id": user_id,
        "moodle_course_id": course_id,
        "status": status,
        "reconciled": is_reconciled,
    }
    if warning:
        result["aviso"] = warning
    return HandlerOutcome(
        event_type="moodle.teacher.enrolled",
        reconciled=is_reconciled,
        result=result,
    )


async def handle_students_enrol(
    envelope: MessageEnvelope,
    gateway: MoodleGateway,
    repo: StateRepository,
    settings: Settings,
    *,
    redelivered: bool = False,
    attempt: int = 1,
) -> HandlerOutcome:
    """Matricula lote de até 100 alunos em curso existente, sem nunca criar cursos."""
    try:
        payload = StudentsEnrolRequestedPayload.model_validate(envelope.payload)
    except ValidationError as exc:
        raise PermanentDomainError(f"Payload inválido para matrícula de alunos: {exc.errors()[0]['msg']}") from None

    course_id = await _resolve_entity_moodle_id(
        gateway, repo, "course", payload.course_idnumber, envelope.correlation_id
    )
    if course_id is None:
        raise PermanentDomainError(
            "Curso ausente no Moodle; sincronize os cursos dos professores antes de matricular alunos."
        )

    items_by_idnumber: dict[str, dict[str, Any]] = {}
    ordered_items: list[dict[str, Any]] = []
    seen_users: set[str] = set()
    for student in payload.students:
        if student.user_idnumber in seen_users:
            continue
        seen_users.add(student.user_idnumber)
        item: dict[str, Any] = {
            "aluno_id": student.aluno_id,
            "user_idnumber": student.user_idnumber,
            "moodle_course_id": course_id,
        }
        try:
            user_id = await _resolve_entity_moodle_id(
                gateway, repo, "student", student.user_idnumber, envelope.correlation_id
            )
            if user_id is None:
                item.update(status="erro", mensagem="Aluno não encontrado no Moodle para matrícula.")
            else:
                item["moodle_user_id"] = user_id
                items_by_idnumber[student.user_idnumber] = item
        except PermanentDomainError as exc:
            item.update(status="erro", mensagem=str(exc))
        ordered_items.append(item)

    existing = await gateway.course_students(course_id)
    pending: list[dict[str, Any]] = []
    any_reconciled = False

    for item in ordered_items:
        if "status" in item:
            continue
        if item["moodle_user_id"] in existing:
            item["status"] = "existente"
            if redelivered or attempt > 1:
                any_reconciled = True
            repo.upsert_enrolment(
                course_idnumber=payload.course_idnumber,
                user_idnumber=item["user_idnumber"],
                role_id=settings.moodle_student_role_id,
                moodle_course_id=course_id,
                moodle_user_id=item["moodle_user_id"],
                status="existing",
                warning_message=None,
                last_correlation_id=envelope.correlation_id,
            )
        else:
            pending.append(item)

    if pending:
        warning: str | None = None
        try:
            await gateway.enrol_students([i["moodle_user_id"] for i in pending], course_id)
        except PostCommitNotificationError as exc:
            warning = str(exc)

        confirmed = await gateway.course_students(course_id)
        for item in pending:
            if item["moodle_user_id"] in confirmed:
                item["status"] = "vinculado"
                if warning:
                    item["aviso"] = warning
                    any_reconciled = True
                elif redelivered or attempt > 1:
                    any_reconciled = True
                repo.upsert_enrolment(
                    course_idnumber=payload.course_idnumber,
                    user_idnumber=item["user_idnumber"],
                    role_id=settings.moodle_student_role_id,
                    moodle_course_id=course_id,
                    moodle_user_id=item["moodle_user_id"],
                    status="reconciled_after_warning" if warning else "enrolled",
                    warning_message=warning,
                    last_correlation_id=envelope.correlation_id,
                )
            else:
                item.update(
                    status="erro",
                    mensagem=warning
                    or "Matrícula enviada, mas papel Estudante não confirmado; reexecute para conferir.",
                )

    summary = {
        status: sum(1 for item in ordered_items if item.get("status") == status)
        for status in ("vinculado", "existente", "erro")
    }
    return HandlerOutcome(
        event_type="moodle.students.enrolled",
        reconciled=any_reconciled,
        result={
            "turma_id": payload.turma_id,
            "disciplina_id": payload.disciplina_id,
            "course_idnumber": payload.course_idnumber,
            "moodle_course_id": course_id,
            "total": len(ordered_items),
            "reconciled": any_reconciled,
            "resumo": summary,
            "resultados": ordered_items,
        },
    )
