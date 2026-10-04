"""跨进程信号总线：SQLite 部署下是安全的空操作。"""

from __future__ import annotations

from lib.infra import pg_bus
from lib.infra.pg_bus import CHANNEL_WAKE, PgListener, notify


async def test_notify_is_a_noop_without_postgres(monkeypatch):
    class _NoConnections:
        def begin(self):
            raise AssertionError("SQLite 部署不该为通知打开数据库连接")

    monkeypatch.setattr(pg_bus, "async_engine", _NoConnections())

    assert await notify(CHANNEL_WAKE) is None
    assert await notify(CHANNEL_WAKE, {"op": "x"}) is None


async def test_listener_does_not_start_a_connection_without_postgres():
    listener = PgListener({CHANNEL_WAKE: lambda _payload: None})

    await listener.start()

    assert listener._task is None
    await listener.stop()
