"""VieNeu-TTS 本地服务的托管生命周期。

VieNeu-TTS（https://github.com/pnnbao97/VieNeu-TTS，Apache-2.0）自带 OpenAI 兼容服务
``apps/openai_speech.py``。启动方式与接口（出自该文件与 README）：

- 启动：在 VieNeu-TTS 仓库目录内执行 ``uv run python -m apps.openai_speech``；
- 监听地址与引擎全部走环境变量：``HOST``（默认 127.0.0.1）、``PORT``（默认 8000）、
  ``VIENEU_BACKEND``（auto|onnx|pytorch）、``VIENEU_DEVICE``（auto|cuda|cpu）、
  ``VIENEU_PRECISION``（fp32|int8，仅 CPU）；
- 端点：``GET /health``、``GET /v1/voices``、``POST /v1/audio/speech``。

本模块只负责按需拉起 / 探活 / 空闲回收 / 关停这一个子进程；启动命令与工作目录由系统设置
提供（默认命令是对上述 README 的假设，用户的安装方式不同时可在设置里改写）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import signal
import sys
import time
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

VieneuState = Literal["stopped", "starting", "ready", "error"]

# 系统设置键（system_settings 表）。
SETTING_ENABLED = "vieneu_enabled"
SETTING_COMMAND = "vieneu_command"
SETTING_WORKDIR = "vieneu_workdir"
SETTING_HOST = "vieneu_host"
SETTING_PORT = "vieneu_port"
SETTING_DEVICE = "vieneu_device"
SETTING_INT8 = "vieneu_int8"
SETTING_STARTUP_TIMEOUT = "vieneu_startup_timeout_seconds"
SETTING_IDLE_MINUTES = "vieneu_idle_minutes"

#: 仓库根目录；VieNeu-TTS 检出缺省放在其下的 ``third_party/VieNeu-TTS``（已加入 .gitignore）。
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKDIR_RELATIVE = "third_party/VieNeu-TTS"

#: 托管服务的等待队列长度与排队超时（秒），见 ``_build_env``。
SERVER_QUEUE_SLOTS = 64
SERVER_QUEUE_TIMEOUT_SECONDS = 1800

DEFAULT_COMMAND: tuple[str, ...] = ("uv", "run", "python", "-m", "apps.openai_speech")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_STARTUP_TIMEOUT_SECONDS = 180
DEFAULT_IDLE_MINUTES = 15

_VALID_DEVICES = ("auto", "cpu", "cuda")
_LOG_RING_SIZE = 500
_STOP_GRACE_SECONDS = 10.0
_IDLE_CHECK_SECONDS = 30.0


class VieneuServiceError(RuntimeError):
    """托管服务无法就绪（未启用、启动失败、超时、进程退出）。"""


def resolve_workdir(raw: str) -> str:
    """VieNeu-TTS 检出目录的绝对路径：留空取项目内缺省位置，相对路径相对仓库根目录解析。"""
    value = raw.strip() or DEFAULT_WORKDIR_RELATIVE
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else REPO_ROOT / path)


@dataclass(frozen=True)
class VieneuConfig:
    enabled: bool = True
    command: tuple[str, ...] = DEFAULT_COMMAND
    workdir: str = ""
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    device: str = "auto"
    int8: bool = False
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS
    idle_minutes: float = DEFAULT_IDLE_MINUTES  # 0 = 不自动回收

    @property
    def client_host(self) -> str:
        """客户端访问用的主机名：监听通配地址时回环访问。"""
        return "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host

    @property
    def root_url(self) -> str:
        return f"http://{self.client_host}:{self.port}"

    @property
    def base_url(self) -> str:
        return f"{self.root_url}/v1"

    @classmethod
    def from_settings(cls, settings: Mapping[str, str]) -> VieneuConfig:
        """从 system_settings 键值构造；缺失 / 非法值回落默认，不抛错。"""

        def _int(key: str, default: int, *, lo: int, hi: int) -> int:
            try:
                value = int(str(settings.get(key, "")).strip())
            except ValueError:
                return default
            return value if lo <= value <= hi else default

        command_raw = str(settings.get(SETTING_COMMAND, "") or "").strip()
        try:
            command = tuple(shlex.split(command_raw)) if command_raw else DEFAULT_COMMAND
        except ValueError:
            command = DEFAULT_COMMAND
        device = str(settings.get(SETTING_DEVICE, "") or "").strip().lower()
        return cls(
            enabled=str(settings.get(SETTING_ENABLED, "true") or "true").strip().lower() not in ("false", "0", "no"),
            command=command or DEFAULT_COMMAND,
            workdir=resolve_workdir(str(settings.get(SETTING_WORKDIR, "") or "")),
            host=str(settings.get(SETTING_HOST, "") or "").strip() or DEFAULT_HOST,
            port=_int(SETTING_PORT, DEFAULT_PORT, lo=1, hi=65535),
            device=device if device in _VALID_DEVICES else "auto",
            int8=str(settings.get(SETTING_INT8, "") or "").strip().lower() in ("true", "1", "yes"),
            startup_timeout_seconds=_int(SETTING_STARTUP_TIMEOUT, DEFAULT_STARTUP_TIMEOUT_SECONDS, lo=5, hi=3600),
            idle_minutes=_int(SETTING_IDLE_MINUTES, DEFAULT_IDLE_MINUTES, lo=0, hi=24 * 60),
        )


@dataclass(frozen=True)
class VieneuStatus:
    state: VieneuState
    pid: int | None
    last_error: str | None
    base_url: str | None
    #: True = 由本进程拉起并负责回收；False = 复用了端口上已有的外部服务
    managed: bool
    logs: list[str] = field(default_factory=list)


ConfigLoader = Callable[[], Awaitable[VieneuConfig]]


async def load_config_from_db() -> VieneuConfig:
    """读系统设置构造配置。"""
    from lib.config.service import ConfigService
    from lib.db import async_session_factory

    async with async_session_factory() as session:
        settings = await ConfigService(session).get_all_settings()
    return VieneuConfig.from_settings(settings)


def _build_env(config: VieneuConfig) -> dict[str, str]:
    env = dict(os.environ)
    env["HOST"] = config.host
    env["PORT"] = str(config.port)
    env["VIENEU_DEVICE"] = config.device
    if config.int8:
        env["VIENEU_PRECISION"] = "int8"
    # CPU 上服务一次只合成一路，其余请求按这两项排队：缺省队列只有一个位置、等 10 秒就返回 429，
    # 多个视频同时配音时会大面积失败。放宽成「排得下整集、等得到合成完」，用户自己设了的不覆盖。
    env.setdefault("VIENEU_QUEUE", str(SERVER_QUEUE_SLOTS))
    env.setdefault("VIENEU_QUEUE_TIMEOUT", str(SERVER_QUEUE_TIMEOUT_SECONDS))
    return env


def send_signal(proc: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    # 命令常是 `uv run ...`，真正的服务是其子进程：以进程组整体发信号，避免遗留孤儿。
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(proc.pid), sig)
        elif sig == signal.SIGTERM:
            proc.terminate()
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


class VieneuServiceManager:
    """单进程、单飞的 VieNeu 服务管理器。"""

    def __init__(
        self,
        config_loader: ConfigLoader = load_config_from_db,
        signaller: Callable[[asyncio.subprocess.Process, signal.Signals], None] = send_signal,
    ) -> None:
        self._config_loader = config_loader
        self._signal = signaller
        self._lock = asyncio.Lock()
        self._state: VieneuState = "stopped"
        self._proc: asyncio.subprocess.Process | None = None
        self._config: VieneuConfig | None = None
        self._last_error: str | None = None
        self._logs: deque[str] = deque(maxlen=_LOG_RING_SIZE)
        self._log_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._last_used = time.monotonic()
        self._inflight = 0
        self._managed = False
        self.poll_interval_seconds = 0.5

    # ── 状态 ────────────────────────────────────────────────

    def status(self) -> VieneuStatus:
        self._refresh_exit()
        return VieneuStatus(
            state=self._state,
            pid=self._proc.pid if self._proc is not None else None,
            last_error=self._last_error,
            base_url=self._config.base_url if self._config and self._state == "ready" else None,
            managed=self._managed,
            logs=list(self._logs),
        )

    def _refresh_exit(self) -> None:
        """进程已退出但状态仍是 starting/ready 时，落到 error（崩溃）。"""
        if self._proc is not None and self._proc.returncode is not None and self._state in ("starting", "ready"):
            self._state = "error"
            self._last_error = f"VieNeu 服务进程已退出 (code={self._proc.returncode})"

    # ── 启动 / 租约 ──────────────────────────────────────────

    async def ensure_running(self) -> str:
        """保证服务可用并返回 OpenAI 兼容 base_url（``http://host:port/v1``）。并发调用单飞。"""
        self._last_used = time.monotonic()
        self._refresh_exit()
        if self._state == "ready" and self._config is not None:
            return self._config.base_url
        async with self._lock:
            self._refresh_exit()
            if self._state == "ready" and self._config is not None:
                return self._config.base_url
            config = await self._config_loader()
            if not config.enabled:
                raise VieneuServiceError("VieNeu 本地服务未启用（vieneu_enabled=false）")
            self._config = config
            await self._start_locked(config)
            return config.base_url

    @contextlib.asynccontextmanager
    async def lease(self) -> AsyncGenerator[str]:
        """持有期间服务不会被空闲回收；产出 base_url。"""
        self._inflight += 1
        try:
            yield await self.ensure_running()
        finally:
            self._inflight -= 1
            self._last_used = time.monotonic()

    async def _probe_health(self, config: VieneuConfig) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{config.root_url}/health")
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        try:
            return response.json().get("status") == "ok"
        except ValueError:
            return False

    async def _start_locked(self, config: VieneuConfig) -> None:
        await self._cleanup_process()
        self._last_error = None
        self._logs.clear()

        # 端口上已有健康的服务（用户自行启动 / 上次遗留）：直接复用，不重复拉起，也不负责回收。
        if await self._probe_health(config):
            self._managed = False
            self._state = "ready"
            return

        self._state = "starting"
        self._managed = True
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *config.command,
                cwd=config.workdir or None,
                env=_build_env(config),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
            )
        except (OSError, ValueError) as exc:
            self._fail(f"无法启动 VieNeu 服务: {exc}")
        assert self._proc is not None
        self._log_task = asyncio.create_task(self._pump_logs(self._proc))

        deadline = time.monotonic() + config.startup_timeout_seconds
        while True:
            exit_code = self._proc.returncode
            if exit_code is not None:
                await self._cleanup_process()
                self._fail(f"VieNeu 服务启动期间退出 (code={exit_code})：{self._tail()}")
            if await self._probe_health(config):
                break
            if time.monotonic() >= deadline:
                await self._cleanup_process()
                self._fail(f"VieNeu 服务在 {config.startup_timeout_seconds:g}s 内未就绪：{self._tail()}")
            await asyncio.sleep(self.poll_interval_seconds)

        self._state = "ready"
        logger.info("VieNeu 服务就绪 pid=%s url=%s", self._proc.pid, config.base_url)
        self._ensure_idle_task()

    def _tail(self, n: int = 5) -> str:
        return " | ".join(list(self._logs)[-n:])

    def _fail(self, message: str) -> None:
        self._state = "error"
        self._last_error = message
        logger.warning(message)
        raise VieneuServiceError(message)

    async def _pump_logs(self, proc: asyncio.subprocess.Process) -> None:
        stream = proc.stdout
        if stream is None:
            return
        try:
            async for raw in stream:
                self._logs.append(raw.decode("utf-8", errors="replace").rstrip())
        except Exception:
            logger.debug("VieNeu 日志读取中断", exc_info=True)

    # ── 停止 ────────────────────────────────────────────────

    async def stop(self) -> None:
        """停止由本管理器拉起的进程（外部复用的服务不动）。幂等。"""
        async with self._lock:
            await self._cleanup_process()
            if self._idle_task is not None:
                self._idle_task.cancel()
                self._idle_task = None
            self._state = "stopped"
            self._last_error = None

    async def _cleanup_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            self._signal(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_SECONDS)
            except TimeoutError:
                self._signal(proc, signal.SIGKILL if sys.platform != "win32" else signal.SIGTERM)
                with contextlib.suppress(TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_SECONDS)
        log_task, self._log_task = self._log_task, None
        if log_task is not None:
            log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await log_task
        if self._state in ("starting", "ready"):
            self._state = "stopped"

    # ── 空闲回收 ─────────────────────────────────────────────

    def _ensure_idle_task(self) -> None:
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_loop())

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(self.idle_check_seconds)
            if self._state != "ready" or not self._managed:
                if self._state != "ready":
                    return
                continue
            config = self._config
            if config is None or config.idle_minutes <= 0 or self._inflight > 0:
                continue
            if time.monotonic() - self._last_used >= config.idle_minutes * 60:
                logger.info("VieNeu 服务空闲 %s 分钟，自动停止", config.idle_minutes)
                # stop() 会取消当前任务自身：先摘掉引用，避免自我 cancel 中断清理。
                self._idle_task = None
                await self.stop()
                return

    idle_check_seconds: float = _IDLE_CHECK_SECONDS


_manager: VieneuServiceManager | None = None


def get_vieneu_manager() -> VieneuServiceManager:
    global _manager
    if _manager is None:
        _manager = VieneuServiceManager()
    return _manager


async def shutdown_vieneu_manager() -> None:
    """应用关停钩子：停止托管进程。"""
    global _manager
    if _manager is not None:
        await _manager.stop()
        _manager = None
