"""read_local_login_status：解析 `claude auth status` 输出并对异常降级。"""

from __future__ import annotations

import pytest

from lib.config import local_claude_login as mod


class _FakeProc:
    def __init__(self, out: bytes) -> None:
        self._out = out

    async def communicate(self):
        return self._out, b""


def _patch_proc(monkeypatch: pytest.MonkeyPatch, out: bytes) -> None:
    async def fake_exec(*_a, **_k):
        return _FakeProc(out)

    monkeypatch.setattr(mod, "find_claude_cli", lambda: "/bin/claude")
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)


@pytest.mark.asyncio
async def test_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proc(monkeypatch, b'{"loggedIn": true, "authMethod": "claude.ai", "email": "a@b.c"}')
    status = await mod.read_local_login_status()
    assert status.logged_in
    assert status.cli_found
    assert status.auth_method == "claude.ai"
    assert status.email == "a@b.c"


@pytest.mark.asyncio
async def test_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proc(monkeypatch, b'{"loggedIn": false, "authMethod": "none"}')
    status = await mod.read_local_login_status()
    assert status.cli_found
    assert not status.logged_in


@pytest.mark.asyncio
async def test_garbage_output_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proc(monkeypatch, b"not json")
    status = await mod.read_local_login_status()
    assert status.cli_found
    assert not status.logged_in


@pytest.mark.asyncio
async def test_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "find_claude_cli", lambda: None)
    status = await mod.read_local_login_status()
    assert not status.cli_found
    assert not status.logged_in
