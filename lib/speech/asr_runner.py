"""调用 ``tools/asr`` 的一次性语音识别进程，取回逐句起止时间。

每次检测起一个子进程：模型只在进程存活期间占用内存，同一批文件共用一次加载，进程退出即释放；
不托管常驻服务。``uv run --project`` 会在首次调用时自行创建虚拟环境并安装依赖，模型也在首次识别时下载。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.speech.asr_alignment import AsrSegment, AsrWord

logger = logging.getLogger(__name__)

SETTING_MODEL = "asr_model"
SETTING_WORKDIR = "asr_workdir"
SETTING_DEVICE = "asr_device"
SETTING_TIMEOUT = "asr_timeout_seconds"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKDIR_RELATIVE = "tools/asr"
DEFAULT_MODEL = "large-v3-turbo"
#: 首次调用要装依赖并下载约 1.5 GB 模型，超时给足。
DEFAULT_TIMEOUT_SECONDS = 1800
_VALID_DEVICES = ("auto", "cpu", "cuda")
_STDERR_TAIL_CHARS = 1500
_KILL_GRACE_SECONDS = 5.0


class AsrError(RuntimeError):
    """识别进程无法给出结果（未安装、超时、非零退出、输出无法解析）。"""


@dataclass(frozen=True)
class AsrConfig:
    model: str = DEFAULT_MODEL
    workdir: str = ""
    device: str = "auto"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_settings(cls, settings: Mapping[str, str]) -> AsrConfig:
        device = str(settings.get(SETTING_DEVICE, "") or "").strip().lower()
        try:
            timeout = float(str(settings.get(SETTING_TIMEOUT, "")).strip())
        except ValueError:
            timeout = DEFAULT_TIMEOUT_SECONDS
        return cls(
            model=str(settings.get(SETTING_MODEL, "") or "").strip() or DEFAULT_MODEL,
            workdir=resolve_workdir(str(settings.get(SETTING_WORKDIR, "") or "")),
            device=device if device in _VALID_DEVICES else "auto",
            timeout_seconds=timeout if 30 <= timeout <= 24 * 3600 else DEFAULT_TIMEOUT_SECONDS,
        )


def resolve_workdir(raw: str) -> str:
    """识别工具目录的绝对路径：留空取项目内的 ``tools/asr``，相对路径相对仓库根目录解析。"""
    value = raw.strip() or DEFAULT_WORKDIR_RELATIVE
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else REPO_ROOT / path)


async def load_config_from_db() -> AsrConfig:
    from lib.config.service import ConfigService
    from lib.db import async_session_factory

    async with async_session_factory() as session:
        return AsrConfig.from_settings(await ConfigService(session).get_all_settings())


#: 起子进程的函数，测试里替换：返回 (returncode, stdout, stderr)。
Runner = Callable[[Sequence[str], float], Awaitable[tuple[int, bytes, bytes]]]


async def _run_subprocess(args: Sequence[str], deadline_seconds: float) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=deadline_seconds)
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        raise
    assert proc.returncode is not None
    return proc.returncode, stdout, stderr


def build_command(config: AsrConfig, files: Sequence[Path], *, language: str) -> list[str]:
    workdir = Path(config.workdir)
    return [
        "uv",
        "run",
        "--project",
        str(workdir),
        "python",
        str(workdir / "detect.py"),
        "--model",
        config.model,
        "--language",
        language,
        "--device",
        config.device,
        *[str(f) for f in files],
    ]


def _parse_words(raw: Any) -> tuple[AsrWord, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        AsrWord(float(item["start"]), float(item["end"]), item["word"])
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("word"), str)
    )


def parse_output(stdout: bytes, files: Sequence[Path]) -> dict[Path, list[AsrSegment] | AsrError]:
    """解析 detect.py 的 JSON：每个文件要么是分段列表，要么是该文件的识别错误。"""
    try:
        raw: Any = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AsrError("语音识别输出不是有效的 JSON") from exc
    if not isinstance(raw, dict):
        raise AsrError("语音识别输出形状不符")
    parsed: dict[Path, list[AsrSegment] | AsrError] = {}
    for file in files:
        entry = raw.get(str(file))
        if not isinstance(entry, dict):
            parsed[file] = AsrError("识别结果缺失")
        elif "error" in entry:
            parsed[file] = AsrError(str(entry["error"]))
        else:
            segments = [
                AsrSegment(float(item["start"]), float(item["end"]), item["text"], _parse_words(item.get("words")))
                for item in entry.get("segments") or []
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            parsed[file] = segments
    return parsed


async def transcribe_files(
    files: Sequence[Path],
    *,
    language: str = "zh",
    config: AsrConfig | None = None,
    run: Runner | None = None,
) -> dict[Path, list[AsrSegment] | AsrError]:
    """对一批媒体文件做识别，整批共用一次模型加载。

    进程整体失败（没装 uv、目录不存在、超时、非零退出）抛 :class:`AsrError`；单个文件失败则在返回里
    以 ``AsrError`` 值表示，不影响同批其它文件。
    """
    if not files:
        return {}
    config = config or await load_config_from_db()
    if run is None:
        if shutil.which("uv") is None:
            raise AsrError("找不到 uv，无法运行语音识别工具")
        if not (Path(config.workdir) / "detect.py").is_file():
            raise AsrError(f"语音识别工具目录不存在：{config.workdir}")
    runner = run or _run_subprocess
    command = build_command(config, files, language=language)
    logger.info("语音识别 model=%s files=%d", config.model, len(files))
    try:
        returncode, stdout, stderr = await runner(command, config.timeout_seconds)
    except TimeoutError as exc:
        raise AsrError(f"语音识别超过 {int(config.timeout_seconds)} 秒未完成") from exc
    if returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL_CHARS:].strip()
        raise AsrError(f"语音识别进程退出码 {returncode}：{tail}")
    return parse_output(stdout, files)


__all__ = [
    "AsrConfig",
    "AsrError",
    "AsrSegment",
    "build_command",
    "load_config_from_db",
    "parse_output",
    "resolve_workdir",
    "transcribe_files",
]
