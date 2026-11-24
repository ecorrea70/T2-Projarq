"""Esquema inicial do sync-service-db (execuções e itens).

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
        "sync_runs",
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("phase_2_published", sa.Boolean(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("correlation_id"),
    )
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"])
    op.create_index("ix_sync_runs_kind_status", "sync_runs", ["kind", "status"])

    op.create_table(
        "sync_run_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.Integer(), nullable=False),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("correlation_id", "idempotency_key", name="uq_sync_item_key"),
    )
    op.create_index("ix_sync_run_items_correlation_id", "sync_run_items", ["correlation_id"])
    op.create_foreign_key(
        "fk_sync_run_items_correlation",
        "sync_run_items",
        "sync_runs",
        ["correlation_id"],
        ["correlation_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sync_run_items_correlation", "sync_run_items", type_="foreignkey")
    op.drop_index("ix_sync_run_items_correlation_id", table_name="sync_run_items")
    op.drop_table("sync_run_items")
    op.drop_index("ix_sync_runs_kind_status", table_name="sync_runs")
    op.drop_index("ix_sync_runs_status", table_name="sync_runs")
    op.drop_table("sync_runs")