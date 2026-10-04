"""Browser-assisted authentication for the bundled Claude Code CLI."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import claude_agent_sdk

_AUTH_URL_RE = re.compile(r"https://claude\.com/cai/oauth/authorize\?[^\s]+")


class ClaudeAuthBusyError(RuntimeError):
    pass


class ClaudeAuthLoginNotFoundError(LookupError):
    pass


def bundled_cli_path() -> Path:
    return Path(claude_agent_sdk.__file__).resolve().parent / "_bundled" / "claude"


def _public_status(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "logged_in": payload.get("loggedIn") is True,
        "auth_method": payload.get("authMethod"),
        "api_provider": payload.get("apiProvider"),
        "email": payload.get("email"),
        "organization": payload.get("orgName"),
        "subscription_type": payload.get("subscriptionType"),
    }


class ClaudeAuthManager:
    """Own at most one host-level Claude OAuth flow at a time."""

    def __init__(self, *, cli_path: Path | None = None, command_timeout: float = 15.0) -> None:
        self._cli_path = cli_path or bundled_cli_path()
        self._command_timeout = command_timeout
        self._lock = asyncio.Lock()
        self._login_id: str | None = None
        self._login_url: str | None = None
        self._login_process: asyncio.subprocess.Process | None = None

    async def status(self) -> dict[str, Any]:
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._cli_path),
                "auth",
                "status",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=self._command_timeout)
            payload = json.loads(stdout.decode("utf-8", errors="replace")) if process.returncode == 0 else {}
        except (OSError, TimeoutError, json.JSONDecodeError):
            payload = {}
        status = _public_status(payload)
        status["login_pending"] = self._login_process is not None and self._login_process.returncode is None
        return status

    async def is_logged_in(self) -> bool:
        return bool((await self.status())["logged_in"])

    async def start_login(self) -> dict[str, str]:
        async with self._lock:
            if self._login_process is not None and self._login_process.returncode is None:
                if self._login_id and self._login_url:
                    return {"login_id": self._login_id, "authorization_url": self._login_url}
                raise ClaudeAuthBusyError("Claude login is already starting")
            await self._clear_login()

            env = os.environ.copy()
            # A server inside Docker cannot open the user's browser. The web UI
            # opens the authorization URL emitted by the CLI instead.
            env["BROWSER"] = "true"
            process = await asyncio.create_subprocess_exec(
                str(self._cli_path),
                "auth",
                "login",
                "--claudeai",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            self._login_process = process
            self._login_id = uuid4().hex

            output = bytearray()
            try:
                while len(output) < 128_000:
                    assert process.stdout is not None
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=self._command_timeout)
                    if not line:
                        break
                    output.extend(line)
                    match = _AUTH_URL_RE.search(output.decode("utf-8", errors="replace"))
                    if match:
                        self._login_url = match.group(0)
                        return {"login_id": self._login_id, "authorization_url": self._login_url}
            except TimeoutError as exc:
                await self._clear_login()
                raise ClaudeAuthBusyError("Claude login did not provide an authorization URL") from exc

            await self._clear_login()
            raise ClaudeAuthBusyError("Claude login exited before providing an authorization URL")

    async def complete_login(self, login_id: str, code: str) -> dict[str, Any]:
        async with self._lock:
            process = self._login_process
            if process is None or self._login_id != login_id or process.returncode is not None:
                await self._clear_login()
                raise ClaudeAuthLoginNotFoundError(login_id)
            if process.stdin is None:
                raise ClaudeAuthBusyError("Claude login input is unavailable")
            process.stdin.write(f"{code.strip()}\n".encode())
            await process.stdin.drain()
            process.stdin.close()
            try:
                exit_code = await asyncio.wait_for(process.wait(), timeout=90.0)
            except TimeoutError as exc:
                await self._clear_login()
                raise ClaudeAuthBusyError("Claude login timed out") from exc
            succeeded = exit_code == 0
            await self._clear_login(terminate=False)
            if not succeeded:
                raise ClaudeAuthBusyError("Claude rejected the authorization code")
        return await self.status()

    async def logout(self) -> dict[str, Any]:
        async with self._lock:
            await self._clear_login()
            process = await asyncio.create_subprocess_exec(
                str(self._cli_path),
                "auth",
                "logout",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=self._command_timeout)
        return await self.status()

    async def shutdown(self) -> None:
        async with self._lock:
            await self._clear_login()

    async def _clear_login(self, *, terminate: bool = True) -> None:
        process = self._login_process
        self._login_process = None
        self._login_id = None
        self._login_url = None
        if not terminate or process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except TimeoutError:
            process.kill()
            await process.wait()
