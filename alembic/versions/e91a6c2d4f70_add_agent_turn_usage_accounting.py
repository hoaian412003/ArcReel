"""add agent turn usage accounting

Revision ID: e91a6c2d4f70
Revises: d8e1f4a7b203
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e91a6c2d4f70"
down_revision: str | Sequence[str] | None = "d8e1f4a7b203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_turns",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("client_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="accepted", nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_creation_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_read_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("usage_snapshot", sa.JSON(), nullable=True),
        sa.Column("model_usage_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "client_key", name="uq_agent_turn_session_client_key"),
    )
    op.create_index("ix_agent_turn_session_created", "agent_turns", ["session_id", "created_at"])
    op.create_index("ix_agent_turn_status", "agent_turns", ["status"])
    op.create_index(op.f("ix_agent_turns_user_id"), "agent_turns", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_turns_user_id"), table_name="agent_turns")
    op.drop_index("ix_agent_turn_status", table_name="agent_turns")
    op.drop_index("ix_agent_turn_session_created", table_name="agent_turns")
    op.drop_table("agent_turns")
