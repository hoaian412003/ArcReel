"""把 SQLite 数据库的全部 ORM 表数据迁移到 PostgreSQL。

用法：
    uv run python scripts/migrate_sqlite_to_postgres.py \
        --source projects/arcreel.db \
        --target postgresql+asyncpg://arcreel:123123123@localhost:5432/arcreel [--replace]

目标库先经 alembic 升到 head，再按外键依赖顺序逐表拷贝，最后重置自增序列。
目标表非空时默认拒绝执行，``--replace`` 会先清空目标表。worker_lease 为进程租约，不迁移。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import Integer, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from lib.agent.agent_session_store.models import register_models as register_agent_session_models
from lib.db.base import Base
from lib.db.models import register_models as register_db_models

BATCH = 500
SKIP_TABLES = {"alembic_version", "worker_lease"}


def _run_alembic_upgrade(target_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": target_url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env)


async def _count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f'SELECT count(*) FROM "{table}"'))).scalar_one()


async def _migrate(source_path: Path, target_url: str, replace: bool) -> None:
    register_agent_session_models()
    register_db_models()
    tables = [t for t in Base.metadata.sorted_tables if t.name not in SKIP_TABLES]

    src = create_async_engine(f"sqlite+aiosqlite:///{source_path}")
    dst = create_async_engine(target_url)
    try:
        non_empty = [t.name for t in tables if await _count(dst, t.name)]
        if non_empty and not replace:
            raise SystemExit(f"目标库已有数据：{', '.join(non_empty)}；确认覆盖请加 --replace")
        if replace:
            async with dst.begin() as conn:
                names = ", ".join(f'"{t.name}"' for t in tables)
                await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))

        for table in tables:
            copied = 0
            async with src.connect() as sconn:
                result = await sconn.stream(table.select())
                async for rows in result.partitions(BATCH):
                    async with dst.begin() as dconn:
                        await dconn.execute(table.insert(), [dict(r._mapping) for r in rows])
                    copied += len(rows)
            print(f"{table.name}: {copied}")

        async with dst.begin() as conn:
            for table in tables:
                for col in table.primary_key.columns:
                    if isinstance(col.type, Integer) and col.autoincrement is not False:
                        await conn.execute(
                            text(
                                f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', '{col.name}'), "
                                f'COALESCE((SELECT MAX("{col.name}") FROM "{table.name}"), 1), '
                                f'(SELECT MAX("{col.name}") IS NOT NULL FROM "{table.name}"))'
                            )
                        )

        for table in tables:
            s, d = await _count(src, table.name), await _count(dst, table.name)
            if s != d:
                raise SystemExit(f"行数不一致 {table.name}: sqlite={s} postgres={d}")
        print("OK：全部表行数一致")
    finally:
        await src.dispose()
        await dst.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"源库不存在：{args.source}")
    _run_alembic_upgrade(args.target)
    asyncio.run(_migrate(args.source, args.target, args.replace))


if __name__ == "__main__":
    main()
