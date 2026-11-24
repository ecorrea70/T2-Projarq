"""Testes unitários dos handlers idempotentes e invariantes herdadas do T1."""
import json

from app.application.dispatcher import MessageDispatcher
from app.core.errors import PostCommitNotificationError
from tests.conftest import make_envelope_dict


def _course_payload(disciplina_id: int = 2) -> dict:
    return {
        "turma_id": 10,
        "disciplina_id": disciplina_id,
        "idnumber": f"ieducar:curso:1:2026:10:{disciplina_id}",
        "shortname": f"ieducar-1-2026-t10-d{disciplina_id}",
        "fullname": f"Disciplina {disciplina_id} — Turma A (turma 10) — 2026",
    }


def _user_payload(kind: str = "student", origin_id: int = 1, **overrides) -> dict:
    label = "aluno" if kind == "student" else "professor"
    base = {
        "kind": kind,
        "origin_id": origin_id,
        "idnumber": f"ieducar:{label}:{origin_id}",
        "username": f"ieducar-{label}-{origin_id}",
        "firstname": "Ana",
        "lastname": "Silva",
        "email": f"{label}{origin_id}@escola.edu.br",
    }
    return {**base, **overrides}


class TestCourseSyncHandler:
    async def test_creates_course_and_recognizes_existing_without_duplicate(
        self, settings, gateway, repo, publisher
    ) -> None:
        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)

        first = make_envelope_dict("moodle.course.sync.requested", _course_payload())
        out1 = await dispatcher.handle_raw_message(
            json.dumps(first).encode(), "moodle.course.sync.requested.v1"
        )
        assert out1.action == "ack"
        assert out1.result["status"] == "criado"
        assert len(gateway.courses) == 1

        # Novo message_id para o mesmo curso: reconhece como existente.
        second = make_envelope_dict("moodle.course.sync.requested", _course_payload())
        out2 = await dispatcher.handle_raw_message(
            json.dumps(second).encode(), "moodle.course.sync.requested.v1"
        )
        assert out2.action == "ack"
        assert out2.result["status"] == "existente"
        assert len(gateway.courses) == 1
        assert repo.get_entity_by_idnumber("ieducar:curso:1:2026:10:2").moodle_id == 20

    async def test_shortname_conflict_is_sent_to_dlq_without_adopting(
        self, settings, gateway, repo, publisher
    ) -> None:
        gateway.courses.append({"id": 50, "shortname": "ieducar-1-2026-t10-d2"})
        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)

        env = make_envelope_dict("moodle.course.sync.requested", _course_payload())
        out = await dispatcher.handle_raw_message(
            json.dumps(env).encode(), "moodle.course.sync.requested.v1"
        )
        assert out.action == "dlq"
        assert len(gateway.courses) == 1
        assert len(publisher.dlq) == 1
        assert publisher.events[-1][0] == "moodle.course.sync.failed.v1"


class TestUserSyncHandler:
    async def test_existing_identity_survives_missing_email(
        self, settings, gateway, repo, publisher
    ) -> None:
        gateway.users.append({"id": 8, "idnumber": "ieducar:aluno:1"})
        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)

        env = make_envelope_dict("moodle.user.sync.requested", _user_payload(email=None, firstname=None))
        out = await dispatcher.handle_raw_message(
            json.dumps(env).encode(), "moodle.user.sync.requested.v1"
        )
        assert out.action == "ack"
        assert out.result["status"] == "existente"
        assert out.result["moodle_id"] == 8

    async def test_single_word_name_defaults_lastname_to_dash(
        self, settings, gateway, repo, publisher
    ) -> None:
        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)
        env = make_envelope_dict("moodle.user.sync.requested", _user_payload(firstname="Aluno001", lastname=""))
        out = await dispatcher.handle_raw_message(
            json.dumps(env).encode(), "moodle.user.sync.requested.v1"
        )
        assert out.action == "ack"
        assert out.result["status"] == "criado"
        assert gateway.users[0]["lastname"] == "-"

    async def test_username_and_email_conflicts_go_to_dlq(
        self, settings, gateway, repo, publisher
    ) -> None:
        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)
        gateway.users = [{"id": 99, "username": "ieducar-aluno-1"}]
        env1 = make_envelope_dict("moodle.user.sync.requested", _user_payload())
        out1 = await dispatcher.handle_raw_message(
            json.dumps(env1).encode(), "moodle.user.sync.requested.v1"
        )
        assert out1.action == "dlq"

        gateway.users = [{"id": 99, "email": "aluno2@escola.edu.br"}]
        env2 = make_envelope_dict("moodle.user.sync.requested", _user_payload(origin_id=2))
        out2 = await dispatcher.handle_raw_message(
            json.dumps(env2).encode(), "moodle.user.sync.requested.v1"
        )
        assert out2.action == "dlq"
        assert len(gateway.users) == 1

    async def test_duplicate_message_id_is_ignored_via_inbox(
        self, settings, gateway, repo, publisher
    ) -> None:
        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)
        env = make_envelope_dict("moodle.user.sync.requested", _user_payload())
        raw = json.dumps(env).encode()

        out1 = await dispatcher.handle_raw_message(raw, "moodle.user.sync.requested.v1")
        out2 = await dispatcher.handle_raw_message(raw, "moodle.user.sync.requested.v1")

        assert out1.action == "ack"
        assert out2.action == "duplicate"
        assert len(gateway.users) == 1
        assert len(publisher.events) == 1


class TestEnrolmentHandlers:
    async def test_teacher_enrol_and_no_false_confirmation(
        self, settings, gateway, repo, publisher
    ) -> None:
        gateway.courses.append({"id": 20, "idnumber": "ieducar:curso:1:2026:10:2"})
        gateway.users.append({"id": 10, "idnumber": "ieducar:professor:7"})
        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)

        env = make_envelope_dict(
            "moodle.teacher.enrol.requested",
            {
                "professor_id": 7,
                "turma_id": 10,
                "disciplina_id": 2,
                "user_idnumber": "ieducar:professor:7",
                "course_idnumber": "ieducar:curso:1:2026:10:2",
            },
        )
        out = await dispatcher.handle_raw_message(
            json.dumps(env).encode(), "moodle.teacher.enrol.requested.v1"
        )
        assert out.action == "ack"
        assert out.result["status"] == "vinculado"

        # Simula ausência de confirmação de papel no Moodle.
        gateway.teachers_by_course = {}
        async def noop_enrol(user_id, course_id):
            return None
        gateway.enrol_teacher = noop_enrol
        env2 = make_envelope_dict(
            "moodle.teacher.enrol.requested",
            {
                "professor_id": 7,
                "turma_id": 10,
                "disciplina_id": 2,
                "user_idnumber": "ieducar:professor:7",
                "course_idnumber": "ieducar:curso:1:2026:10:2",
            },
        )
        out2 = await dispatcher.handle_raw_message(
            json.dumps(env2).encode(), "moodle.teacher.enrol.requested.v1"
        )
        assert out2.action == "dlq"

    async def test_students_enrol_never_creates_missing_course(
        self, settings, gateway, repo, publisher
    ) -> None:
        gateway.users.append({"id": 10, "idnumber": "ieducar:aluno:1"})
        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)

        env = make_envelope_dict(
            "moodle.students.enrol.requested",
            {
                "turma_id": 10,
                "disciplina_id": 2,
                "course_idnumber": "ieducar:curso:1:2026:10:999",
                "students": [{"aluno_id": 1, "user_idnumber": "ieducar:aluno:1"}],
            },
        )
        out = await dispatcher.handle_raw_message(
            json.dumps(env).encode(), "moodle.students.enrol.requested.v1"
        )
        assert out.action == "dlq"
        assert len(gateway.courses) == 0

    async def test_students_batch_isolates_missing_user_and_reconciles_smtp_warning(
        self, settings, gateway, repo, publisher
    ) -> None:
        gateway.courses.append({"id": 20, "idnumber": "ieducar:curso:1:2026:10:2"})
        gateway.users.append({"id": 10, "idnumber": "ieducar:aluno:1"})

        original_enrol = gateway.enrol_students

        async def enrol_then_smtp_error(user_ids: list[int], course_id: int) -> None:
            await original_enrol(user_ids, course_id)
            raise PostCommitNotificationError("Falha SMTP após commit.")

        gateway.enrol_students = enrol_then_smtp_error
        dispatcher = MessageDispatcher(settings, gateway, repo, publisher)

        env = make_envelope_dict(
            "moodle.students.enrol.requested",
            {
                "turma_id": 10,
                "disciplina_id": 2,
                "course_idnumber": "ieducar:curso:1:2026:10:2",
                "students": [
                    {"aluno_id": 1, "user_idnumber": "ieducar:aluno:1"},
                    {"aluno_id": 2, "user_idnumber": "ieducar:aluno:2"},
                ],
            },
        )
        out = await dispatcher.handle_raw_message(
            json.dumps(env).encode(), "moodle.students.enrol.requested.v1"
        )
        assert out.action == "ack"
        assert out.reconciled is True
        assert out.result["resumo"] == {"vinculado": 1, "existente": 0, "erro": 1}
        assert "aviso" in out.result["resultados"][0]
        assert out.result["resultados"][1]["status"] == "erro"
