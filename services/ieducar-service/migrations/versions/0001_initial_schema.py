"""Esquema inicial do ieducar-service-db (impressões digitais e varreduras).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-24 00:00:00
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
        "source_fingerprints",
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("entity_key", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_scan_id", sa.String(length=64), nullable=False),
        sa.Column("last_correlation_id", sa.String(length=64), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("entity_type", "entity_key"),
    )
    op.create_index(
        "ix_source_fingerprints_entity_type",
        "source_fingerprints",
        ["entity_type"],
    )
    op.create_index(
        "ix_source_fingerprints_last_scan",
        "source_fingerprints",
        ["entity_type", "last_scan_id"],
    )

    op.create_table(
        "scan_runs",
        sa.Column("scan_id", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("baseline", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("full_republish", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("records_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changes_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changes_published", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("scan_id"),
    )
    op.create_index("ix_scan_runs_started_at", "scan_runs", ["started_at"])
    op.create_index("ix_scan_runs_correlation_id", "scan_runs", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_scan_runs_correlation_id", table_name="scan_runs")
    op.drop_index("ix_scan_runs_started_at", table_name="scan_runs")
    op.drop_table("scan_runs")
    op.drop_index("ix_source_fingerprints_last_scan", table_name="source_fingerprints")
    op.drop_index("ix_source_fingerprints_entity_type", table_name="source_fingerprints")
    op.drop_table("source_fingerprints")
