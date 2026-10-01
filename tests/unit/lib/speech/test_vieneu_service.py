"""VieneuServiceManager 状态机：子进程与 HTTP 探活均在边界处替换，不起真实服务。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import respx

from lib.speech import vieneu_service
from lib.speech.vieneu_service import VieneuConfig, VieneuServiceError, VieneuServiceManager

_HEALTH = "http://127.0.0.1:8123/health"


class _FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self) -> _FakeStdout:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            await asyncio.sleep(3600)
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, *, pid: int = 4242, returncode: int | None = None, lines: list[bytes] | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdout = _FakeStdout(lines or [])
        self._done = asyncio.Event()

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, code: int = 0) -> None:
        self.returncode = code
        self._done.set()


class _Spawner:
    """替换 asyncio.create_subprocess_exec 与进程组信号发送。"""

    def __init__(self, proc_factory: Callable[[], _FakeProc]) -> None:
        self.proc_factory = proc_factory
        self.spawned: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.procs: list[_FakeProc] = []
        self.signals: list[Any] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> _FakeProc:
        self.spawned.append((args, kwargs))
        proc = self.proc_factory()
        self.procs.append(proc)
        return proc

    def send(self, proc: _FakeProc, sig: Any) -> None:
        self.signals.append(sig)
        proc.finish(-15)


@pytest.fixture
def spawner(monkeypatch: pytest.MonkeyPatch) -> _Spawner:
    sp = _Spawner(_FakeProc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", sp)
    return sp


def _manager(spawner: _Spawner, **overrides: Any) -> VieneuServiceManager:
    config = VieneuConfig(port=8123, startup_timeout_seconds=5, **overrides)

    async def loader() -> VieneuConfig:
        return config

    manager = VieneuServiceManager(loader, spawner.send)
    manager.poll_interval_seconds = 0.01
    return manager


@pytest.fixture
def health() -> Iterator[respx.Route]:
    with respx.mock(assert_all_called=False) as router:
        yield router.get(_HEALTH)


def _ok() -> httpx.Response:
    return httpx.Response(200, json={"status": "ok"})


class TestStartup:
    async def test_spawns_polls_health_then_ready(self, spawner: _Spawner, health: respx.Route):
        health.mock(side_effect=[httpx.ConnectError("no"), httpx.ConnectError("no"), _ok()])
        manager = _manager(spawner, device="cpu", int8=True)

        base_url = await manager.ensure_running()

        assert base_url == "http://127.0.0.1:8123/v1"
        status = manager.status()
        assert (status.state, status.pid, status.managed) == ("ready", 4242, True)
        args, kwargs = spawner.spawned[0]
        assert args == vieneu_service.DEFAULT_COMMAND
        assert kwargs["env"]["PORT"] == "8123"
        assert kwargs["env"]["VIENEU_DEVICE"] == "cpu"
        assert kwargs["env"]["VIENEU_PRECISION"] == "int8"
        await manager.stop()

    async def test_concurrent_calls_spawn_once(self, spawner: _Spawner, health: respx.Route):
        health.mock(side_effect=[httpx.ConnectError("no"), httpx.ConnectError("no"), _ok(), _ok(), _ok()])
        manager = _manager(spawner)

        urls = await asyncio.gather(*(manager.ensure_running() for _ in range(4)))

        assert len(set(urls)) == 1
        assert len(spawner.spawned) == 1
        await manager.stop()

    async def test_reuses_already_healthy_server(self, spawner: _Spawner, health: respx.Route):
        health.mock(return_value=_ok())
        manager = _manager(spawner)

        await manager.ensure_running()

        assert spawner.spawned == []
        status = manager.status()
        assert (status.state, status.pid, status.managed) == ("ready", None, False)

    async def test_startup_timeout_sets_error_and_kills_process(self, spawner: _Spawner, health: respx.Route):
        health.mock(side_effect=httpx.ConnectError("no"))
        manager = VieneuServiceManager(_loader(_short_timeout_config()), spawner.send)
        manager.poll_interval_seconds = 0.01

        with pytest.raises(VieneuServiceError, match="未就绪"):
            await manager.ensure_running()

        status = manager.status()
        assert status.state == "error"
        assert status.last_error is not None
        assert "未就绪" in status.last_error
        assert spawner.signals, "超时后应终止子进程"

    async def test_process_exit_during_startup_reports_logs(self, spawner: _Spawner, health: respx.Route):
        health.mock(side_effect=httpx.ConnectError("no"))
        spawner.proc_factory = lambda: _FakeProc(returncode=3, lines=[b"boom: missing model\n"])
        manager = _manager(spawner)

        with pytest.raises(VieneuServiceError, match="退出"):
            await manager.ensure_running()

        assert manager.status().state == "error"

    async def test_spawn_failure_sets_error(
        self, monkeypatch: pytest.MonkeyPatch, spawner: _Spawner, health: respx.Route
    ):
        health.mock(side_effect=httpx.ConnectError("no"))

        async def boom(*_a: Any, **_k: Any) -> Any:
            raise FileNotFoundError("uv")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
        manager = _manager(spawner)

        with pytest.raises(VieneuServiceError, match="无法启动"):
            await manager.ensure_running()
        assert manager.status().state == "error"

    async def test_disabled_config_refuses(self, spawner: _Spawner, health: respx.Route):
        manager = _manager(spawner, enabled=False)

        with pytest.raises(VieneuServiceError, match="未启用"):
            await manager.ensure_running()
        assert spawner.spawned == []

    async def test_crash_after_ready_is_reported_and_restarts(self, spawner: _Spawner, health: respx.Route):
        health.mock(side_effect=[httpx.ConnectError("no"), _ok()])
        manager = _manager(spawner)
        await manager.ensure_running()

        spawner.procs[0].finish(1)
        assert manager.status().state == "error"

        health.mock(side_effect=[httpx.ConnectError("no"), _ok()])
        await manager.ensure_running()
        assert manager.status().state == "ready"
        assert len(spawner.spawned) == 2
        await manager.stop()


def _loader(config: VieneuConfig) -> Callable[[], Any]:
    async def loader() -> VieneuConfig:
        return config

    return loader


def _short_timeout_config() -> VieneuConfig:
    # from_settings 对超时有 5s 下限；直接构造以便测试在毫秒级走完超时分支
    return VieneuConfig(port=8123, startup_timeout_seconds=0.05)


class TestStop:
    async def test_stop_terminates_and_is_idempotent(self, spawner: _Spawner, health: respx.Route):
        health.mock(side_effect=[httpx.ConnectError("no"), _ok()])
        manager = _manager(spawner)
        await manager.ensure_running()

        await manager.stop()
        await manager.stop()

        status = manager.status()
        assert (status.state, status.pid, status.base_url) == ("stopped", None, None)
        assert len(spawner.signals) == 1

    async def test_stop_does_not_touch_external_server(self, spawner: _Spawner, health: respx.Route):
        health.mock(return_value=_ok())
        manager = _manager(spawner)
        await manager.ensure_running()

        await manager.stop()

        assert spawner.signals == []
        assert manager.status().state == "stopped"


class TestIdle:
    async def test_idle_server_is_stopped(self, spawner: _Spawner, health: respx.Route):
        health.mock(side_effect=[httpx.ConnectError("no"), _ok()])
        manager = _manager(spawner, idle_minutes=1)
        manager.idle_check_seconds = 0.01
        await manager.ensure_running()

        manager._last_used -= 120
        await _wait_for(lambda: manager.status().state == "stopped")

        assert spawner.signals

    async def test_lease_blocks_idle_stop(self, spawner: _Spawner, health: respx.Route):
        health.mock(side_effect=[httpx.ConnectError("no"), _ok()])
        manager = _manager(spawner, idle_minutes=1)
        manager.idle_check_seconds = 0.01

        async with manager.lease():
            manager._last_used -= 120
            await asyncio.sleep(0.1)
            assert manager.status().state == "ready"
        await manager.stop()


async def _wait_for(predicate: Callable[[], bool], attempts: int = 200) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("条件在限定时间内未满足")


class TestConfigFromSettings:
    def test_defaults(self):
        config = VieneuConfig.from_settings({})
        assert config.enabled is True
        assert config.command == vieneu_service.DEFAULT_COMMAND
        assert (config.host, config.port, config.device) == ("127.0.0.1", 8000, "auto")
        assert config.base_url == "http://127.0.0.1:8000/v1"

    def test_parses_and_sanitizes(self):
        config = VieneuConfig.from_settings(
            {
                "vieneu_enabled": "false",
                "vieneu_command": "python -m apps.openai_speech --x 'a b'",
                "vieneu_workdir": " /opt/vieneu ",
                "vieneu_host": "0.0.0.0",
                "vieneu_port": "9001",
                "vieneu_device": "CUDA",
                "vieneu_int8": "true",
                "vieneu_idle_minutes": "0",
            }
        )
        assert config.enabled is False
        assert config.command == ("python", "-m", "apps.openai_speech", "--x", "a b")
        assert config.workdir == "/opt/vieneu"
        assert config.root_url == "http://127.0.0.1:9001"  # 通配监听地址回环访问
        assert (config.device, config.int8, config.idle_minutes) == ("cuda", True, 0)

    def test_invalid_values_fall_back(self):
        config = VieneuConfig.from_settings(
            {"vieneu_port": "99999", "vieneu_device": "tpu", "vieneu_command": "'unterminated"}
        )
        assert config.port == 8000
        assert config.device == "auto"
        assert config.command == vieneu_service.DEFAULT_COMMAND


class TestWorkdirResolution:
    def test_blank_setting_uses_the_checkout_inside_the_project(self):
        from lib.speech.vieneu_service import REPO_ROOT, resolve_workdir

        assert resolve_workdir("") == str(REPO_ROOT / "third_party" / "VieNeu-TTS")
        assert resolve_workdir("   ") == str(REPO_ROOT / "third_party" / "VieNeu-TTS")

    def test_relative_setting_is_relative_to_the_project_root(self):
        from lib.speech.vieneu_service import REPO_ROOT, resolve_workdir

        assert resolve_workdir("tools/vieneu") == str(REPO_ROOT / "tools" / "vieneu")

    def test_absolute_setting_is_kept(self):
        from lib.speech.vieneu_service import resolve_workdir

        assert resolve_workdir("/opt/vieneu") == "/opt/vieneu"

    def test_config_from_empty_settings_points_at_the_project_checkout(self):
        from lib.speech.vieneu_service import REPO_ROOT, VieneuConfig

        assert VieneuConfig.from_settings({}).workdir == str(REPO_ROOT / "third_party" / "VieNeu-TTS")
