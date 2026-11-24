"""Esquema inicial do moodle-service-db (inbox, entidades e matrículas).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-23 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_messages",
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.String(length=16), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("reconciled", sa.Boolean(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index("ix_processed_messages_correlation_id", "processed_messages", ["correlation_id"])
    op.create_index("ix_processed_messages_idempotency_key", "processed_messages", ["idempotency_key"])

    op.create_table(
        "moodle_entities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("idnumber", sa.String(length=255), nullable=False),
        sa.Column("moodle_id", sa.Integer(), nullable=False),
        sa.Column("shortname_or_username", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("origin_id", sa.String(length=64), nullable=True),
        sa.Column("last_sync_status", sa.String(length=32), nullable=False),
        sa.Column("last_correlation_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idnumber"),
    )
    op.create_index("ix_moodle_entities_idnumber", "moodle_entities", ["idnumber"])
    op.create_index("ix_moodle_entities_last_correlation_id", "moodle_entities", ["last_correlation_id"])

    op.create_table(
        "moodle_enrolments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_idnumber", sa.String(length=255), nullable=False),
        sa.Column("user_idnumber", sa.String(length=255), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("moodle_course_id", sa.Integer(), nullable=False),
        sa.Column("moodle_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("warning_message", sa.Text(), nullable=True),
        sa.Column("last_correlation_id", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_idnumber", "user_idnumber", "role_id", name="uq_moodle_enrolment_key"),
    )
    op.create_index("ix_moodle_enrolments_course_idnumber", "moodle_enrolments", ["course_idnumber"])
    op.create_index("ix_moodle_enrolments_user_idnumber", "moodle_enrolments", ["user_idnumber"])
    op.create_index("ix_moodle_enrolments_last_correlation_id", "moodle_enrolments", ["last_correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_moodle_enrolments_last_correlation_id", table_name="moodle_enrolments")
    op.drop_index("ix_moodle_enrolments_user_idnumber", table_name="moodle_enrolments")
    op.drop_index("ix_moodle_enrolments_course_idnumber", table_name="moodle_enrolments")
    op.drop_table("moodle_enrolments")

    op.drop_index("ix_moodle_entities_last_correlation_id", table_name="moodle_entities")
    op.drop_index("ix_moodle_entities_idnumber", table_name="moodle_entities")
    op.drop_table("moodle_entities")

    op.drop_index("ix_processed_messages_idempotency_key", table_name="processed_messages")
    op.drop_index("ix_processed_messages_correlation_id", table_name="processed_messages")
    op.drop_table("processed_messages")
