"""add task timeline, queue audit and task progress columns

Revision ID: d8e1f4a7b203
Revises: a7c2e9d41b58
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

revision: str = "d8e1f4a7b203"
down_revision: str | Sequence[str] | None = "a7c2e9d41b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "task_timeline",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("percent", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_timeline_task_id_id", "task_timeline", ["task_id", "id"])
    op.create_table(
        "queue_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("project_name", sa.String(), nullable=True),
        sa.Column("task_type", sa.String(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_queue_audit_at", "queue_audit", ["at"])
    op.create_index("idx_queue_audit_action_at", "queue_audit", ["action", "at"])
    op.create_index("idx_queue_audit_task_id", "queue_audit", ["task_id"])
    with preserve_sqlite_indexes("tasks"), op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("progress", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("estimated_cost_amount", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("estimated_currency", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("input_digest", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with preserve_sqlite_indexes("tasks"), op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("input_digest")
        batch_op.drop_column("estimated_currency")
        batch_op.drop_column("estimated_cost_amount")
        batch_op.drop_column("last_synced_at")
        batch_op.drop_column("progress")
    op.drop_table("queue_audit")
    op.drop_table("task_timeline")
