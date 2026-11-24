"""Contratos (Protocols) entre aplicação, adaptador Moodle, banco e mensageria."""
from typing import Any, Protocol

from app.domain.models import ClaimResult


class MoodleGateway(Protocol):
    async def find_user(self, field: str, value: str) -> list[dict[str, Any]]: ...
    async def create_user(self, user: dict[str, Any]) -> int: ...
    async def check_teaching_setup(self) -> None: ...
    async def check_student_setup(self) -> None: ...
    async def find_course(self, field: str, value: str) -> list[dict[str, Any]]: ...
    async def create_course(self, course: dict[str, Any]) -> int: ...
    async def course_teachers(self, course_id: int) -> set[int]: ...
    async def course_students(self, course_id: int) -> set[int]: ...
    async def enrol_teacher(self, user_id: int, course_id: int) -> None: ...
    async def enrol_students(self, user_ids: list[int], course_id: int) -> None: ...


class StateRepository(Protocol):
    def ping(self) -> bool: ...
    def claim_message(
        self,
        message_id: str,
        event_type: str,
        event_version: str,
        correlation_id: str,
        idempotency_key: str,
        payload_hash: str,
        allow_replay: bool = False,
    ) -> ClaimResult: ...
    def mark_message_succeeded(
        self,
        message_id: str,
        result: dict[str, Any],
        reconciled: bool = False,
    ) -> None: ...
    def mark_message_retry(self, message_id: str, error_message: str) -> None: ...
    def mark_message_dead_lettered(
        self,
        message_id: str,
        error_message: str,
        result: dict[str, Any] | None = None,
    ) -> None: ...
    def get_entity_by_idnumber(self, idnumber: str) -> Any | None: ...
    def upsert_entity(
        self,
        entity_type: str,
        idnumber: str,
        moodle_id: int,
        shortname_or_username: str | None,
        email: str | None,
        origin_id: str | None,
        last_sync_status: str,
        last_correlation_id: str,
    ) -> Any: ...
    def upsert_enrolment(
        self,
        course_idnumber: str,
        user_idnumber: str,
        role_id: int,
        moodle_course_id: int,
        moodle_user_id: int,
        status: str,
        warning_message: str | None,
        last_correlation_id: str,
    ) -> Any: ...
    def get_run_summary(self, correlation_id: str) -> dict[str, Any]: ...


class EventPublisher(Protocol):
    async def publish_event(
        self,
        routing_key: str,
        payload: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> None: ...
    async def publish_retry(
        self,
        routing_key: str,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> None: ...
    async def publish_dlq(
        self,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> None: ...
