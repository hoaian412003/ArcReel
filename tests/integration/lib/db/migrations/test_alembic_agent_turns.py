from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_MIGRATION_GLOB = "*_add_agent_turn_usage_accounting.py"


def test_agent_turn_migration_round_trip(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
) -> None:
    revision, parent = migration_revisions(_MIGRATION_GLOB)
    config, database = alembic_cfg
    command.upgrade(config, parent)
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        command.upgrade(config, revision)
        inspector = sa.inspect(engine)
        assert "agent_turns" in inspector.get_table_names()
        assert {column["name"] for column in inspector.get_columns("agent_turns")} >= {
            "session_id",
            "client_key",
            "input_tokens",
            "output_tokens",
            "usage_snapshot",
            "model_usage_snapshot",
            "total_cost_usd",
        }
        assert {index["name"] for index in inspector.get_indexes("agent_turns")} >= {
            "ix_agent_turn_session_created",
            "ix_agent_turn_status",
        }

        command.downgrade(config, parent)
        assert "agent_turns" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
