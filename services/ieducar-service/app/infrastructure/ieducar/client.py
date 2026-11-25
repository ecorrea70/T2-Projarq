"""Cliente das duas APIs do i-Educar.

O i-Educar expõe duas interfaces diferentes, e este é o único lugar do T2 que
conhece as duas, junto com as credenciais de cada uma:

- REST v2 `/api/registration`, autenticada por Bearer e paginada: alunos.
- API legada `/module/Api/*`, autenticada por `access_key` e `oper=get`:
  professores, turmas e disciplinas.

Para fora, o serviço oferece três leituras já normalizadas, no formato que o
sync-service espera.
"""
import time

import httpx

from app.core.config import Settings
from app.core.errors import (
    CircuitBreakerOpenError,
    IEducarServiceError,
    SourceDataError,
    TransientIntegrationError,
)
from app.core.logging import get_logger
from app.core.metrics import API_DURATION, API_REQUESTS
from app.core.retry import run_with_local_retry
from app.domain import payloads
from app.domain.models import AlunoRecord, CatalogEntry, ProfessorRecord
from app.infrastructure.ieducar.circuit_breaker import CircuitBreaker

PAGE_SIZE = 100
REGISTRATION_FIELDS = {
    "include": "student:id,person_id,ativo|student.person:id,name,email|"
    "enrollments:id,ref_cod_matricula,ref_cod_turma,ativo,transferido,remanejado,"
    "abandono,falecido,reclassificado,data_exclusao",
    "only": "id,student_id,pmieducar.matricula.ativo,ano,ref_ref_cod_escola",
    "order": "cod_matricula,asc",
    "show": PAGE_SIZE,
}

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 509}


class IEducarClient:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._settings = settings
        self._log = get_logger("ieducar_client")
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=settings.cb_failure_threshold,
            recovery_seconds=settings.cb_recovery_seconds,
        )
        self._client = http_client or httpx.AsyncClient(
            base_url=settings.ieducar_url.rstrip("/"),
            timeout=httpx.Timeout(
                connect=settings.ieducar_connect_timeout,
                read=settings.ieducar_read_timeout,
                write=settings.ieducar_read_timeout,
                pool=settings.ieducar_connect_timeout,
            ),
            follow_redirects=False,
        )
        self._owns_client = http_client is None

    @property
    def breaker_state(self) -> str:
        return self._breaker.state

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ HTTP

    def _scope(self) -> dict:
        return {
            "instituicao_id": self._settings.institution_id,
            "escola": self._settings.school_id,
            "ano": self._settings.year,
        }

    async def _request(self, path: str, params: dict, *, resource: str, legacy: bool) -> dict:
        """Uma chamada, com breaker, classificação de erro e métrica."""
        headers = {"Accept": "application/json"}
        query = dict(params)
        if legacy:
            query.update({"access_key": self._settings.ieducar_access_key, "oper": "get"})
        else:
            headers["Authorization"] = "Bearer " + self._settings.ieducar_token

        self._breaker.before_call()
        started = time.perf_counter()
        try:
            response = await self._client.get(path, params=query, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            self._breaker.record_transient_failure()
            API_REQUESTS.labels(resource=resource, outcome="transport_error").inc()
            raise TransientIntegrationError(
                f"i-Educar inacessível em {resource} ({type(exc).__name__})."
            ) from None
        except Exception:
            self._breaker.release_probe()
            raise
        finally:
            API_DURATION.labels(resource=resource).observe(time.perf_counter() - started)

        if response.status_code in _RETRYABLE_STATUS:
            self._breaker.record_transient_failure()
            API_REQUESTS.labels(resource=resource, outcome=f"http_{response.status_code}").inc()
            raise TransientIntegrationError(
                f"i-Educar respondeu {response.status_code} em {resource}."
            )
        if response.status_code >= 400:
            # 401/403/404: credencial ou rota erradas. Repetir não resolve.
            self._breaker.record_connectivity_ok()
            API_REQUESTS.labels(resource=resource, outcome=f"http_{response.status_code}").inc()
            raise SourceDataError(
                f"i-Educar recusou a consulta em {resource} (HTTP {response.status_code})."
            )

        try:
            result = response.json()
        except ValueError:
            self._breaker.record_connectivity_ok()
            API_REQUESTS.labels(resource=resource, outcome="invalid_json").inc()
            raise SourceDataError(f"Resposta não-JSON do i-Educar em {resource}.") from None

        if not isinstance(result, dict) or result.get("any_error_msg") or "exception" in result:
            self._breaker.record_connectivity_ok()
            API_REQUESTS.labels(resource=resource, outcome="rejected").inc()
            raise SourceDataError(f"A API do i-Educar recusou a consulta em {resource}.")

        self._breaker.record_success()
        API_REQUESTS.labels(resource=resource, outcome="ok").inc()
        return result

    async def _get(self, path: str, params: dict, *, resource: str, legacy: bool = False) -> dict:
        return await run_with_local_retry(
            lambda _attempt: self._request(path, params, resource=resource, legacy=legacy),
            max_retries=self._settings.local_max_retries,
            base_delay=self._settings.local_retry_base_delay,
        )

    async def _legacy(self, path: str, params: dict, *, resource: str) -> dict:
        return await self._get(
            path, {**self._scope(), **params}, resource=resource, legacy=True
        )

    async def ping(self) -> bool:
        """Checagem leve para o /ready: o i-Educar atende e aceita a credencial."""
        try:
            await self._legacy(
                "/module/Api/Turma",
                {"resource": "turmas-por-escola"},
                resource="ping",
            )
            return True
        except IEducarServiceError:
            return False

    # -------------------------------------------------------------- Leituras

    async def fetch_catalog(self) -> list[CatalogEntry]:
        """Turmas vigentes no escopo, multiplicadas pelas disciplinas de cada uma."""
        classes = payloads.catalog_classes(
            await self._legacy(
                "/module/Api/Turma",
                {"resource": "turmas-por-escola"},
                resource="turmas-por-escola",
            )
        )
        schools = self._settings.schools()
        courses: dict[tuple[int, int], dict] = {}
        for classroom in classes:
            code, year, school = payloads.class_scope(classroom, self._settings.year, schools)
            disciplines = payloads.disciplines_of(
                await self._get(
                    "/module/Api/ComponenteCurricular",
                    {
                        "resource": "componentes-curriculares-for-multiple-search",
                        "instituicao_id": self._settings.institution_id,
                        "turma_id": code,
                        "ano": year,
                        "allDisciplinesMulti": "true",
                    },
                    resource="componentes-curriculares",
                    legacy=True,
                )
            )
            for discipline, name in disciplines.items():
                record = {
                    "instituicao_id": int(self._settings.institution_id),
                    "ano": year,
                    "escola_id": school,
                    "turma_id": code,
                    "turma_nome": classroom.get("nome") or "",
                    "disciplina_id": payloads.identifier(discipline),
                    "disciplina_nome": name or "",
                }
                key = (code, record["disciplina_id"])
                if key in courses and courses[key] != record:
                    raise SourceDataError("Curso divergente no catálogo da API.")
                courses[key] = record
        self._log.info(
            "Catálogo lido com %d cursos.",
            len(courses),
            extra={"entity_type": "catalog", "records_read": len(courses)},
        )
        return [CatalogEntry.model_validate(record) for record in courses.values()]

    async def fetch_teachers(self) -> list[ProfessorRecord]:
        """Professores com vínculo vigente. Um professor problemático não derruba os demais."""
        links = payloads.teacher_links(
            await self._legacy(
                "/module/Api/Servidor",
                {"resource": "servidores-disciplinas-turmas"},
                resource="servidores-disciplinas-turmas",
            )
        )
        teachers: list[ProfessorRecord] = []
        for code in sorted(links):
            try:
                response = await self._get(
                    "/module/Api/Servidor",
                    {"resource": "dados-servidor", "servidor_id": code},
                    resource="dados-servidor",
                    legacy=True,
                )
                record = payloads.teacher_from_record(response, code, links[code])
                if record is not None:
                    teachers.append(ProfessorRecord.model_validate(record))
            except CircuitBreakerOpenError:
                # O i-Educar como um todo caiu: insistir nos próximos é inútil.
                raise
            except IEducarServiceError as exc:
                self._log.warning(
                    "Professor %s não pôde ser lido: %s",
                    code,
                    exc,
                    extra={
                        "entity_type": "teacher",
                        "entity_key": str(code),
                        "error_type": type(exc).__name__,
                    },
                )
                teachers.append(ProfessorRecord(professor_id=code, erro_origem=str(exc)))
        self._log.info(
            "Professores lidos: %d.",
            len(teachers),
            extra={"entity_type": "teacher", "records_read": len(teachers)},
        )
        return teachers

    async def fetch_students(self) -> list[AlunoRecord]:
        """Alunos ativos com suas enturmações, reunindo páginas e escolas."""
        students: dict[int, dict] = {}
        for school in self._settings.schools():
            await self._students_for_school(school, students)
        self._log.info(
            "Alunos lidos: %d.",
            len(students),
            extra={"entity_type": "student", "records_read": len(students)},
        )
        return [AlunoRecord.model_validate(record) for record in students.values()]

    async def _students_for_school(self, school: str, students: dict[int, dict]) -> None:
        # O laço tem limite próprio: terminar não deve depender apenas de a
        # paginação da resposta ser confiável.
        for page in range(1, self._settings.max_pages + 1):
            result = await self._get(
                "/api/registration",
                self._registration_params(school, page),
                resource="registration",
            )
            rows, last = payloads.page_of(result, page, self._settings.max_pages)
            for row in rows:
                record = payloads.student_from_registration(row, self._settings.year, school)
                if record is not None:
                    payloads.merge_student(students, record)
            if page == last:
                return
        raise SourceDataError("Paginação de matrículas excedeu o limite de páginas.")

    def _registration_params(self, school: str, page: int) -> dict:
        params = {**REGISTRATION_FIELDS, "page": page}
        for key, value in (
            ("institution", self._settings.institution_id),
            ("school", school),
            ("yearEq", self._settings.year),
        ):
            if value:
                params[key] = value
        return params
