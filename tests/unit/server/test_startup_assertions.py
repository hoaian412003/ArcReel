"""启动断言测试 — 父进程 env 不得含 provider 密钥。"""

from __future__ import annotations

import pytest

from lib.config.env_keys import PROVIDER_SECRET_KEYS
from server.app import (
    _log_profile_sync_outcome,
    assert_no_provider_secrets_in_environ,
)

# 复用生产代码（assert_no_provider_secrets_in_environ）所基于的同一份真相源，
# 避免测试与运行时密钥清单漂移。sorted() 让 parametrize 测试 ID 稳定。
_SECRET_KEYS_SORTED = sorted(PROVIDER_SECRET_KEYS)


def _clear_secret_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in PROVIDER_SECRET_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_clean_environ_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_secret_envs(monkeypatch)
    assert_no_provider_secrets_in_environ()  # no raise


@pytest.mark.parametrize("leaked_key", _SECRET_KEYS_SORTED)
def test_any_single_secret_triggers_raise(monkeypatch: pytest.MonkeyPatch, leaked_key: str) -> None:
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv(leaked_key, "leaked-value")
    with pytest.raises(RuntimeError, match="SECURITY"):
        assert_no_provider_secrets_in_environ()


def test_empty_string_value_not_treated_as_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """空字符串不算泄漏（os.environ.pop 后 SDK 子进程会跳过空值）。"""
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert_no_provider_secrets_in_environ()  # 空值不 raise


def test_claude_code_oauth_token_does_not_trigger_startup_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude 登录态会在会话环境中清空，但不属于启动期 provider 密钥。"""
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "user-login-token")
    assert_no_provider_secrets_in_environ()


# bool 是 int 子类，``isinstance(True, int) and True > 0`` 为真——这一组三连测试
# 防止 startup 日志判定回退到天真的 isinstance 写法，把 abort 信号误打成"同步完成"。
def test_log_profile_sync_outcome_aborted_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stats = {
        "created": 0,
        "repaired": 0,
        "skipped": 0,
        "errors": 0,
        "failed_projects": 0,
        "aborted": True,
    }
    with caplog.at_level("WARNING", logger="server.app"):
        _log_profile_sync_outcome(stats)
    assert any("物化已中止" in r.message for r in caplog.records)
    assert not any("同步完成" in r.message for r in caplog.records)


def test_log_profile_sync_outcome_success_counts_logs_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stats = {
        "created": 3,
        "repaired": 0,
        "skipped": 0,
        "errors": 0,
        "failed_projects": 0,
        "aborted": False,
    }
    with caplog.at_level("INFO", logger="server.app"):
        _log_profile_sync_outcome(stats)
    assert any("物化完成" in r.message for r in caplog.records)


def test_log_profile_sync_outcome_all_zero_is_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """无 abort、所有计数为 0 → 不打日志（避免每次空启动也刷一行）。"""
    stats = {
        "created": 0,
        "repaired": 0,
        "skipped": 0,
        "errors": 0,
        "failed_projects": 0,
        "aborted": False,
    }
    with caplog.at_level("INFO", logger="server.app"):
        _log_profile_sync_outcome(stats)
    assert caplog.records == []
