from __future__ import annotations

import sqlalchemy as sa

from alembic import command


def test_claude_queue_migration_round_trip(alembic_cfg, migration_revisions) -> None:
    revision, parent = migration_revisions("*_add_claude_turn_queue.py")
    config, database = alembic_cfg
    command.upgrade(config, parent)
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        command.upgrade(config, revision)
        columns = {column["name"]: column for column in sa.inspect(engine).get_columns("agent_turns")}
        assert columns["prompt"]["nullable"] is True
        assert columns["action"]["nullable"] is True
        assert "input_tokens" in columns
        command.downgrade(config, parent)
        names = {column["name"] for column in sa.inspect(engine).get_columns("agent_turns")}
        assert "prompt" not in names
        assert "action" not in names
        assert "input_tokens" in names
    finally:
        engine.dispose()
