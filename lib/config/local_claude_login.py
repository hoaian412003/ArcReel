"""探测宿主机上 Claude Code CLI 的登录态（`claude auth status`）。

Agent 子进程用的是 claude-agent-sdk 随包的 CLI，登录态存在系统钥匙串 / ``~/.claude``，
与用户在终端 `claude login` 的是同一份，所以这里用同一个可执行文件查询。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_STATUS_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class LocalLoginStatus:
    cli_found: bool
    logged_in: bool
    auth_method: str | None = None
    email: str | None = None
    subscription_type: str | None = None


def find_claude_cli() -> str | None:
    """与 SDK 的查找顺序一致：先随包 CLI，再 PATH。"""
    try:
        import claude_agent_sdk

        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        for candidate in (bundled, bundled.with_suffix(".exe")):
            if candidate.is_file():
                return str(candidate)
    except ImportError:
        pass
    return shutil.which("claude")


async def read_local_login_status() -> LocalLoginStatus:
    cli = find_claude_cli()
    if cli is None:
        return LocalLoginStatus(cli_found=False, logged_in=False)
    try:
        proc = await asyncio.create_subprocess_exec(
            cli,
            "auth",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_STATUS_TIMEOUT_SECONDS)
        data = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except (TimeoutError, OSError, ValueError):
        logger.warning("claude auth status failed", exc_info=True)
        return LocalLoginStatus(cli_found=True, logged_in=False)
    if not isinstance(data, dict):
        return LocalLoginStatus(cli_found=True, logged_in=False)
    return LocalLoginStatus(
        cli_found=True,
        logged_in=bool(data.get("loggedIn")),
        auth_method=_str_or_none(data.get("authMethod")),
        email=_str_or_none(data.get("email")),
        subscription_type=_str_or_none(data.get("subscriptionType")),
    )


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
