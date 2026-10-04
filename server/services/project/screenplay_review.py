"""编剧剧本（screenplay）的服务层：读取、按场次修改、人工通过。

纯逻辑（模型 / 指纹 / 状态 / 渲染）在 ``lib.script.screenplay``；本层叠加项目与集号校验，并把阻塞的文件读写
放到线程里。router 与 Agent 工具共用本服务，两条路径的语义一致。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lib.project.project_manager import ProjectManager, find_episode
from lib.script import screenplay


class ScreenplayReviewError(Exception):
    """剧本审阅的领域错误。``code`` 供 router 映射 HTTP 状态与 i18n key。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message


def _domain_error(exc: screenplay.ScreenplayError) -> ScreenplayReviewError:
    """``screenplay_`` 前缀留给上层（工具 problem 码 / i18n key）统一添加，领域错误码不带。"""
    return ScreenplayReviewError(exc.code.removeprefix("screenplay_"), str(exc))


def _view(data: Mapping[str, Any]) -> dict[str, Any]:
    """读侧视图：摘要 + 逐场内容 + 可读 Markdown。总时长在摘要里，由各场时长相加得出。"""
    return {
        **screenplay.screenplay_summary(data),
        "scenes": [dict(scene) for scene in data["scenes"]],
        "markdown": screenplay.render_markdown(data),
        "approved_at": (data.get("approval") or {}).get("approved_at") if data.get("approval") else None,
    }


class ScreenplayReviewService:
    def __init__(self, pm: ProjectManager) -> None:
        self.pm = pm

    def _project_path(self, project_name: str, episode: int) -> Path:
        project = self.pm.load_project(project_name)
        if find_episode(project, episode) is None:
            raise ScreenplayReviewError("episode_not_found")
        if project.get("content_mode") == "ad":
            raise ScreenplayReviewError("not_applicable", "广告/短片项目没有剧本规划，不走编剧步骤")
        return self.pm.get_project_path(project_name)

    async def get_state(self, project_name: str, episode: int) -> dict[str, Any]:
        """该集剧本与审阅状态；尚未编写时 ``status`` 为 ``none``、``scenes`` 为空。"""
        return await asyncio.to_thread(self._get_state_sync, project_name, episode)

    def _get_state_sync(self, project_name: str, episode: int) -> dict[str, Any]:
        project_path = self._project_path(project_name, episode)
        try:
            data = screenplay.load_screenplay(project_path, episode)
        except screenplay.ScreenplayError as exc:
            raise _domain_error(exc) from exc
        if data is None:
            return {"episode": episode, "status": "none", "scenes": [], "fingerprint": None, "total_seconds": 0}
        return _view(data)

    async def edit(
        self,
        project_name: str,
        episode: int,
        operations: Sequence[Mapping[str, Any]],
        *,
        expected_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """按场次修改剧本并返回最新状态；成功修改使既有审阅失效。"""
        return await asyncio.to_thread(self._edit_sync, project_name, episode, operations, expected_fingerprint)

    def _edit_sync(
        self,
        project_name: str,
        episode: int,
        operations: Sequence[Mapping[str, Any]],
        expected_fingerprint: str | None,
    ) -> dict[str, Any]:
        project_path = self._project_path(project_name, episode)
        try:
            data = screenplay.edit_scenes(project_path, episode, operations, expected_fingerprint=expected_fingerprint)
        except screenplay.ScreenplayError as exc:
            raise _domain_error(exc) from exc
        return _view(data)

    async def approve(
        self, project_name: str, episode: int, *, expected_fingerprint: str | None = None
    ) -> dict[str, Any]:
        """记录人工审阅通过并返回最新状态。``expected_fingerprint`` 给出时须与当前内容一致。"""
        return await asyncio.to_thread(self._approve_sync, project_name, episode, expected_fingerprint)

    def _approve_sync(self, project_name: str, episode: int, expected_fingerprint: str | None) -> dict[str, Any]:
        project_path = self._project_path(project_name, episode)
        try:
            data = screenplay.approve_screenplay(project_path, episode, expected_fingerprint=expected_fingerprint)
        except screenplay.ScreenplayError as exc:
            raise _domain_error(exc) from exc
        return _view(data)
