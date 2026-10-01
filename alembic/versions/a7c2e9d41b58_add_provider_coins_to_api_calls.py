"""add provider_coins to api_calls

Revision ID: a7c2e9d41b58
Revises: b66f6d617e3f
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.db.migration_helpers import preserve_sqlite_indexes

revision: str = "a7c2e9d41b58"
down_revision: str | Sequence[str] | None = "b66f6d617e3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("api_calls", schema=None) as batch_op:
        batch_op.add_column(sa.Column("provider_coins", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with preserve_sqlite_indexes("api_calls"), op.batch_alter_table("api_calls", schema=None) as batch_op:
        batch_op.drop_column("provider_coins")
