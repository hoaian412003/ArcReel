from __future__ import annotations

from typing import Any

from server.claude_runtime.auth import ClaudeAuthManager


class FakeReader:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    async def readline(self) -> bytes:
        return next(self._lines, b"")


class FakeWriter:
    def __init__(self) -> None:
        self.value = b""
        self.closed = False

    def write(self, value: bytes) -> None:
        self.value += value

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, *, stdout: bytes = b"", lines: list[bytes] | None = None, running: bool = False) -> None:
        self._stdout = stdout
        self.stdout = FakeReader(lines or [])
        self.stdin = FakeWriter()
        self.returncode = None if running else 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


async def test_browser_login_round_trip(monkeypatch, tmp_path) -> None:
    login_process = FakeProcess(
        running=True,
        lines=[
            b"Opening browser to sign in...\n",
            b"If it did not open, visit: https://claude.com/cai/oauth/authorize?state=test&code=true\n",
        ],
    )
    status_payload = (
        b'{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty",'
        b'"email":"person@example.com","orgName":"Example","subscriptionType":"pro"}'
    )
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
        calls.append((args, kwargs))
        if args[1:4] == ("auth", "login", "--claudeai"):
            return login_process
        return FakeProcess(stdout=status_payload)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    manager = ClaudeAuthManager(cli_path=tmp_path / "claude")

    started = await manager.start_login()
    assert started["authorization_url"].startswith("https://claude.com/cai/oauth/authorize?")
    assert calls[0][1]["env"]["BROWSER"] == "true"

    status = await manager.complete_login(started["login_id"], "one-time-code")
    assert login_process.stdin.value == b"one-time-code\n"
    assert login_process.stdin.closed is True
    assert status == {
        "logged_in": True,
        "auth_method": "claude.ai",
        "api_provider": "firstParty",
        "email": "person@example.com",
        "organization": "Example",
        "subscription_type": "pro",
        "login_pending": False,
    }
