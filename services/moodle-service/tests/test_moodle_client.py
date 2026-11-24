"""Testes unitários do MoodleClient e do CircuitBreaker."""
import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.errors import (
    CircuitBreakerOpenError,
    PermanentDomainError,
    PostCommitNotificationError,
    TransientIntegrationError,
)
from app.infrastructure.moodle.circuit_breaker import CircuitBreaker
from app.infrastructure.moodle.client import MoodleClient


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _mock_client(handler, settings) -> MoodleClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, follow_redirects=False)
    return MoodleClient(settings, http_client=http_client)


class TestCircuitBreaker:
    def test_transitions_closed_open_half_open_closed(self) -> None:
        clock = FakeClock(100.0)
        cb = CircuitBreaker(failure_threshold=3, recovery_seconds=10.0, clock=clock)
        assert cb.state == "closed"

        for _ in range(2):
            cb.before_call()
            cb.record_transient_failure()
            assert cb.state == "closed"

        cb.before_call()
        cb.record_transient_failure()
        assert cb.state == "open"

        with pytest.raises(CircuitBreakerOpenError):
            cb.before_call()

        clock.advance(10.0)
        assert cb.state == "half_open"
        cb.before_call()
        with pytest.raises(CircuitBreakerOpenError):
            cb.before_call()  # Apenas 1 sonda por vez em half-open.

        cb.record_success()
        assert cb.state == "closed"
        assert cb.consecutive_failures == 0

    def test_half_open_failure_reopens_circuit(self) -> None:
        clock = FakeClock(10.0)
        cb = CircuitBreaker(failure_threshold=1, recovery_seconds=5.0, clock=clock)
        cb.before_call()
        cb.record_transient_failure()
        assert cb.state == "open"

        clock.advance(5.0)
        cb.before_call()
        cb.record_transient_failure()
        assert cb.state == "open"

    def test_domain_errors_do_not_open_circuit(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_seconds=10.0)
        for _ in range(10):
            cb.before_call()
            cb.record_connectivity_ok()
        assert cb.state == "closed"


class TestMoodleClient:
    async def test_create_user_uses_unique_random_password_and_manual_auth(self, settings) -> None:
        captured: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            captured.append(form)
            return httpx.Response(200, json=[{"id": len(captured) + 10}])

        client = _mock_client(handler, settings)
        id1 = await client.create_user({"username": "ieducar-aluno-1", "idnumber": "ieducar:aluno:1"})
        id2 = await client.create_user({"username": "ieducar-aluno-2", "idnumber": "ieducar:aluno:2"})
        await client.aclose()

        assert (id1, id2) == (11, 12)
        assert captured[0]["users[0][password]"] != captured[1]["users[0][password]"]
        assert captured[0]["users[0][auth]"] == "manual"
        assert captured[0]["users[0][preferences][0][type]"] == "auth_forcepasswordchange"
        assert "users[0][createpassword]" not in captured[0]

    async def test_redirect_and_moodle_exception_raise_permanent_without_tripping_cb(self, settings) -> None:
        responses = iter([
            httpx.Response(302, headers={"Location": "http://other.local"}),
            httpx.Response(200, json={"exception": "moodle_exception", "message": "token-secret"}),
            httpx.Response(200, json={"errorcode": "Message was not sent."}),
        ])
        client = _mock_client(lambda _: next(responses), settings)

        with pytest.raises(PermanentDomainError):
            await client.find_user("idnumber", "ieducar:aluno:1")

        with pytest.raises(PermanentDomainError) as exc_info:
            await client.find_user("idnumber", "ieducar:aluno:1")
        assert "token-secret" not in str(exc_info.value)

        with pytest.raises(PostCommitNotificationError):
            await client.enrol_students([10], 20)

        assert client.circuit_breaker.state == "closed"
        assert client.circuit_breaker.consecutive_failures == 0
        await client.aclose()

    async def test_setup_checks_validate_functions_and_category(self, settings) -> None:
        required_teaching = [
            "core_user_get_users_by_field",
            "core_user_create_users",
            "core_course_get_categories",
            "core_course_get_courses_by_field",
            "core_course_create_courses",
            "core_enrol_get_enrolled_users",
            "enrol_manual_enrol_users",
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            fn = form["wsfunction"]
            if fn == "core_webservice_get_site_info":
                return httpx.Response(200, json={"functions": [{"name": f} for f in required_teaching]})
            if fn == "core_course_get_categories":
                return httpx.Response(200, json=[{"id": 1}])
            return httpx.Response(200, json={})

        client = _mock_client(handler, settings)
        await client.check_teaching_setup()
        await client.check_student_setup()
        await client.aclose()

        def missing_fn_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"functions": [{"name": "core_webservice_get_site_info"}]})

        bad_client = _mock_client(missing_fn_handler, settings)
        with pytest.raises(PermanentDomainError, match="enrol_manual_enrol_users"):
            await bad_client.check_student_setup()
        await bad_client.aclose()

    async def test_find_course_enforces_exact_identity_and_id_gt_1(self, settings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"courses": [{"id": 20, "idnumber": "ieducar:curso:1:2026:10:2"}], "warnings": []},
            )

        client = _mock_client(handler, settings)
        found = await client.find_course("idnumber", "ieducar:curso:1:2026:10:2")
        assert found[0]["id"] == 20

        with pytest.raises(PermanentDomainError):
            await client.find_course("idnumber", "outro-idnumber")
        await client.aclose()

    async def test_enrol_and_role_filtering(self, settings) -> None:
        calls: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            calls.append(form)
            if form["wsfunction"] == "core_enrol_get_enrolled_users":
                return httpx.Response(
                    200,
                    json=[
                        {"id": 7, "roles": [{"roleid": 3}]},
                        {"id": 8, "roles": [{"roleid": 5}]},
                    ],
                )
            return httpx.Response(200, content=b"null", headers={"Content-Type": "application/json"})

        client = _mock_client(handler, settings)
        assert await client.course_teachers(20) == {7}
        assert await client.course_students(20) == {8}
        await client.enrol_teacher(7, 20)
        await client.enrol_students([8, 9], 20)
        assert calls[2]["enrolments[0][roleid]"] == "3"
        assert calls[3]["enrolments[1][roleid]"] == "5"
        assert calls[3]["enrolments[1][userid]"] == "9"
        await client.aclose()

    async def test_http_503_and_timeout_raise_transient_and_trip_cb(self, settings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        cb = CircuitBreaker(failure_threshold=2, recovery_seconds=30.0)
        client = MoodleClient(
            settings,
            circuit_breaker=cb,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(TransientIntegrationError):
            await client.find_user("idnumber", "x")
        with pytest.raises(TransientIntegrationError):
            await client.find_user("idnumber", "x")
        assert cb.state == "open"
        with pytest.raises(CircuitBreakerOpenError):
            await client.find_user("idnumber", "x")
        await client.aclose()
