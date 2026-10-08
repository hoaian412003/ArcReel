"""Persist queued Claude prompts and contextual action provenance."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a714e53bc982"
down_revision: str | Sequence[str] | None = "e91a6c2d4f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_turns", sa.Column("prompt", sa.Text(), nullable=True))
    op.add_column("agent_turns", sa.Column("action", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_turns") as batch:
        batch.drop_column("action")
        batch.drop_column("prompt")
