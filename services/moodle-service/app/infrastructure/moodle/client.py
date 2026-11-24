"""Cliente assíncrono resiliente para o Web Service REST do Moodle."""
import secrets
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import (
    PermanentDomainError,
    PostCommitNotificationError,
    TransientIntegrationError,
)
from app.core.logging import get_logger
from app.core.metrics import MOODLE_API_DURATION, MOODLE_API_REQUESTS
from app.infrastructure.moodle.circuit_breaker import CircuitBreaker


class MoodleClient:
    """Encapsula as 8 funções Web Service REST do Moodle com timeout e Circuit Breaker."""

    def __init__(
        self,
        settings: Settings,
        circuit_breaker: CircuitBreaker | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            failure_threshold=settings.cb_failure_threshold,
            recovery_seconds=settings.cb_recovery_seconds,
        )
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=settings.moodle_connect_timeout,
                read=settings.moodle_read_timeout,
                write=settings.moodle_read_timeout,
                pool=settings.moodle_connect_timeout,
            ),
        )
        self._injected_drops: set[str] = set()
        self._log = get_logger("moodle_client")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def call(self, function: str, params: dict[str, Any]) -> Any:
        if not self.settings.moodle_token:
            raise PermanentDomainError("MOODLE_TOKEN não configurado.")

        self.circuit_breaker.before_call()
        payload = {
            "wstoken": self.settings.moodle_token,
            "moodlewsrestformat": "json",
            "wsfunction": function,
            **params,
        }
        started = time.perf_counter()
        self._log.info("Moodle %s", function, extra={"wsfunction": function})
        try:
            response = await self._client.post(
                f"{self.settings.moodle_url}/webservice/rest/server.php",
                data=payload,
            )
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            elapsed_s = time.perf_counter() - started
            MOODLE_API_DURATION.labels(wsfunction=function).observe(elapsed_s)
            MOODLE_API_REQUESTS.labels(wsfunction=function, outcome="transient_error").inc()
            self.circuit_breaker.record_transient_failure()
            self._log.warning(
                "Moodle %s falhou (erro transitório: %s)",
                function,
                type(exc).__name__,
                extra={
                    "wsfunction": function,
                    "duration_ms": round(elapsed_s * 1000, 2),
                    "error_type": type(exc).__name__,
                    "cb_state": self.circuit_breaker.state,
                },
            )
            raise TransientIntegrationError(
                "Falha de comunicação com o Moodle; reexecute ou aguarde retry automático."
            ) from None
        except Exception:
            self.circuit_breaker.release_probe()
            raise

        elapsed_s = time.perf_counter() - started
        MOODLE_API_DURATION.labels(wsfunction=function).observe(elapsed_s)

        if 300 <= response.status_code < 400:
            self.circuit_breaker.record_connectivity_ok()
            MOODLE_API_REQUESTS.labels(wsfunction=function, outcome="redirect_blocked").inc()
            raise PermanentDomainError("Redirecionamento inesperado bloqueado ao acessar o Moodle.")

        if response.status_code == 429 or response.status_code >= 500:
            MOODLE_API_REQUESTS.labels(wsfunction=function, outcome="transient_error").inc()
            self.circuit_breaker.record_transient_failure()
            self._log.warning(
                "Moodle %s retornou HTTP %d",
                function,
                response.status_code,
                extra={
                    "wsfunction": function,
                    "duration_ms": round(elapsed_s * 1000, 2),
                    "status": response.status_code,
                    "cb_state": self.circuit_breaker.state,
                },
            )
            raise TransientIntegrationError(
                f"Moodle indisponível temporariamente (HTTP {response.status_code})."
            )

        if response.status_code >= 400:
            self.circuit_breaker.record_connectivity_ok()
            MOODLE_API_REQUESTS.labels(wsfunction=function, outcome="permanent_error").inc()
            raise PermanentDomainError(f"Requisição recusada pelo Moodle (HTTP {response.status_code}).")

        try:
            result = response.json()
        except ValueError:
            MOODLE_API_REQUESTS.labels(wsfunction=function, outcome="invalid_json").inc()
            self.circuit_breaker.record_transient_failure()
            raise TransientIntegrationError("Resposta não JSON recebida do Moodle.") from None

        if isinstance(result, dict) and ("exception" in result or "errorcode" in result):
            self.circuit_breaker.record_connectivity_ok()
            if result.get("errorcode") == "Message was not sent.":
                MOODLE_API_REQUESTS.labels(wsfunction=function, outcome="notification_error").inc()
                raise PostCommitNotificationError(
                    "O Moodle falhou ao enviar uma notificação; confira a configuração de e-mail/SMTP."
                )
            MOODLE_API_REQUESTS.labels(wsfunction=function, outcome="moodle_error").inc()
            raise PermanentDomainError(
                f"O Moodle recusou {function}; confira função no serviço, token, permissões e dados."
            )

        self.circuit_breaker.record_success()
        MOODLE_API_REQUESTS.labels(wsfunction=function, outcome="ok").inc()
        self._log.info(
            "Moodle %s ok",
            function,
            extra={
                "wsfunction": function,
                "duration_ms": round(elapsed_s * 1000, 2),
                "cb_state": self.circuit_breaker.state,
            },
        )
        return result

    async def find_user(self, field: str, value: str) -> list[dict[str, Any]]:
        users = await self.call("core_user_get_users_by_field", {"field": field, "values[0]": value})
        if not isinstance(users, list) or any(
            not isinstance(user, dict) or type(user.get("id")) is not int for user in users
        ):
            raise PermanentDomainError("Resposta de consulta de usuários inválida do Moodle.")
        return users

    async def create_user(self, user: dict[str, Any]) -> int:
        # Senha aleatória individual, sem envio de mensagens nem exposição na resposta.
        params = {f"users[0][{key}]": value for key, value in user.items()}
        result = await self.call(
            "core_user_create_users",
            {
                **params,
                "users[0][password]": secrets.token_urlsafe(32) + "aA1!",
                "users[0][auth]": "manual",
                "users[0][preferences][0][type]": "auth_forcepasswordchange",
                "users[0][preferences][0][value]": "1",
            },
        )
        if (
            not isinstance(result, list)
            or len(result) != 1
            or not isinstance(result[0], dict)
            or type(result[0].get("id")) is not int
        ):
            raise PermanentDomainError("Resposta de criação de usuário inválida no Moodle.")
        return result[0]["id"]

    async def check_teaching_setup(self) -> None:
        await self.check_functions(
            {
                "core_user_get_users_by_field",
                "core_user_create_users",
                "core_course_get_categories",
                "core_course_get_courses_by_field",
                "core_course_create_courses",
                "core_enrol_get_enrolled_users",
                "enrol_manual_enrol_users",
            }
        )
        categories = await self.call(
            "core_course_get_categories",
            {"criteria[0][key]": "id", "criteria[0][value]": self.settings.moodle_category_id},
        )
        if (
            not isinstance(categories, list)
            or len(categories) != 1
            or not isinstance(categories[0], dict)
            or categories[0].get("id") != self.settings.moodle_category_id
        ):
            raise PermanentDomainError("Categoria Moodle não encontrada ou não acessível.")

    async def check_student_setup(self) -> None:
        await self.check_functions(
            {
                "core_user_get_users_by_field",
                "core_user_create_users",
                "core_course_get_courses_by_field",
                "core_enrol_get_enrolled_users",
                "enrol_manual_enrol_users",
            }
        )

    async def check_functions(self, required: set[str]) -> None:
        info = await self.call("core_webservice_get_site_info", {})
        if not isinstance(info, dict) or not isinstance(info.get("functions"), list):
            raise PermanentDomainError("Resposta de funções disponíveis do Moodle inválida.")
        available = {f.get("name") for f in info["functions"] if isinstance(f, dict)}
        missing = required - available
        if missing:
            raise PermanentDomainError("Adicione ao serviço Moodle: " + ", ".join(sorted(missing)))

    async def find_course(self, field: str, value: str) -> list[dict[str, Any]]:
        result = await self.call("core_course_get_courses_by_field", {"field": field, "value": value})
        if not isinstance(result, dict) or not isinstance(result.get("courses"), list) or result.get("warnings"):
            raise PermanentDomainError("Consulta de cursos inválida ou com avisos no Moodle.")
        courses = result["courses"]
        if any(
            not isinstance(c, dict)
            or type(c.get("id")) is not int
            or c["id"] <= 1
            or str(c.get(field)) != str(value)
            for c in courses
        ):
            raise PermanentDomainError("Identidade de curso inválida no Moodle.")
        return courses

    async def create_course(self, course: dict[str, Any]) -> int:
        result = await self.call(
            "core_course_create_courses",
            {
                **{f"courses[0][{key}]": value for key, value in course.items()},
                "courses[0][categoryid]": self.settings.moodle_category_id,
            },
        )
        if (
            not isinstance(result, list)
            or len(result) != 1
            or not isinstance(result[0], dict)
            or type(result[0].get("id")) is not int
            or result[0]["id"] <= 1
        ):
            raise PermanentDomainError("Resposta de criação de curso inválida no Moodle.")

        course_id = result[0]["id"]
        idnumber = str(course.get("idnumber", ""))
        if self._should_drop_after_create(idnumber):
            self._injected_drops.add(idnumber)
            self._log.warning(
                "Injeção de falha (Cenário 3): resposta HTTP simuladamente perdida após criação do curso.",
                extra={"wsfunction": "core_course_create_courses", "idempotency_key": idnumber},
            )
            raise TransientIntegrationError(
                "Resposta HTTP simuladamente perdida após criação do curso no Moodle."
            )
        return course_id

    def _should_drop_after_create(self, idnumber: str) -> bool:
        if not self.settings.fault_inject_drop_after_course_create:
            return False
        if idnumber in self._injected_drops:
            return False
        target = self.settings.fault_inject_target_idnumber
        return not target or target == idnumber

    async def course_teachers(self, course_id: int) -> set[int]:
        return await self.course_users_with_role(course_id, self.settings.moodle_teacher_role_id)

    async def course_students(self, course_id: int) -> set[int]:
        return await self.course_users_with_role(course_id, self.settings.moodle_student_role_id)

    async def course_users_with_role(self, course_id: int, role_id: int) -> set[int]:
        users = await self.call(
            "core_enrol_get_enrolled_users",
            {"courseid": course_id, "options[0][name]": "onlyactive", "options[0][value]": 1},
        )
        if not isinstance(users, list):
            raise PermanentDomainError("Resposta de participantes inválida no Moodle.")
        matched: set[int] = set()
        for user in users:
            if (
                not isinstance(user, dict)
                or type(user.get("id")) is not int
                or not isinstance(user.get("roles"), list)
            ):
                raise PermanentDomainError("Papéis dos participantes ausentes na API Moodle.")
            if any(isinstance(role, dict) and role.get("roleid") == role_id for role in user["roles"]):
                matched.add(user["id"])
        return matched

    async def enrol_teacher(self, user_id: int, course_id: int) -> None:
        await self.enrol_users([user_id], course_id, self.settings.moodle_teacher_role_id)

    async def enrol_students(self, user_ids: list[int], course_id: int) -> None:
        await self.enrol_users(user_ids, course_id, self.settings.moodle_student_role_id)

    async def enrol_users(self, user_ids: list[int], course_id: int, role_id: int) -> None:
        if not user_ids:
            return
        params: dict[str, Any] = {}
        for index, user_id in enumerate(user_ids):
            params.update(
                {
                    f"enrolments[{index}][roleid]": role_id,
                    f"enrolments[{index}][userid]": user_id,
                    f"enrolments[{index}][courseid]": course_id,
                }
            )
        result = await self.call("enrol_manual_enrol_users", params)
        if result is not None:
            raise PermanentDomainError("Resposta inesperada de matrícula no Moodle.")
