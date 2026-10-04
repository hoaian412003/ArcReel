"""导演审阅的服务层：状态与报告读取、人工决定落盘。

纯逻辑（适用性 / 指纹 / 状态派生）在 ``lib.script.direction_review``，报告由
``lib.script.director_report`` 确定性派生；本层叠加 ProjectManager 读取正式脚本与决定记录的持久化。
router 与 Agent 工具共用本服务，两条路径的决定语义一致。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from lib.project.project_manager import ProjectManager, find_episode
from lib.script import direction_review, script_review
from lib.script.director_report import build_director_report
from lib.script.script_skeleton import SKELETONS

logger = logging.getLogger(__name__)


class DirectionReviewError(Exception):
    """导演审阅的领域错误。``code`` 供 router 映射 HTTP 状态与 i18n key。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message


def read_formal_script(pm: ProjectManager, project_name: str, project: dict[str, Any], episode: int) -> dict | None:
    """读该集正式脚本（只读，不触发迁移回写）；缺失或读不出时 ``None``。"""
    project_path = pm.get_project_path(project_name)
    try:
        filename = script_review.formal_script_filename(project_path, project, episode)
        return pm.load_script_readonly(project_name, filename)
    except (FileNotFoundError, script_review.ForeignFormalScriptError):
        return None
    except (OSError, ValueError) as exc:
        logger.warning("导演审阅读取第 %s 集正式脚本失败: %s", episode, exc)
        return None


class DirectionReviewService:
    def __init__(self, pm: ProjectManager) -> None:
        self.pm = pm

    async def get_state(self, project_name: str, episode: int) -> dict[str, Any]:
        """该集导演审阅状态 + 确定性报告。

        返回 ``{"episode", "status", "fingerprint", "decided_at", "note", "report"}``；``report`` 在脚本里
        有任何导演数据时给出，否则为 ``None``。项目不适用导演步骤时 ``status`` 为 ``not_applicable``。
        """
        return await asyncio.to_thread(self._get_state_sync, project_name, episode)

    def _get_state_sync(self, project_name: str, episode: int) -> dict[str, Any]:
        project = self.pm.load_project(project_name)
        if find_episode(project, episode) is None:
            raise DirectionReviewError("episode_not_found")
        kind = direction_review.entry_kind(project)
        script = read_formal_script(self.pm, project_name, project, episode) if kind is not None else None
        status = direction_review.review_status(project, script, episode)
        stored = direction_review.stored_review(project, episode) if status in {"approved", "changes_requested"} else {}
        report = build_director_report(script, kind) if script is not None and kind is not None else None
        return {
            "episode": episode,
            "status": status,
            "fingerprint": direction_review.direction_fingerprint(script, kind)
            if script is not None and kind is not None
            else None,
            "decided_at": stored.get("decided_at"),
            "note": stored.get("note"),
            "report": report,
        }

    async def decide(
        self,
        project_name: str,
        episode: int,
        decision: str,
        *,
        note: str | None = None,
        entry_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """记录人工决定并返回最新状态。

        ``approve`` 只在已有导演数据且状态为待审阅 / 已要求修改时接受；``changes`` / ``redo`` 记录为要求修改，
        不在服务端调用 Agent——导演步骤随后由工作流下一步 ``direct_episode`` 接手。该集提示词已全部编写时不再
        接受修改类决定（改导演会与已编写的提示词脱节）。
        """
        await asyncio.to_thread(self._decide_sync, project_name, episode, decision, note, entry_ids)
        return await self.get_state(project_name, episode)

    def _decide_sync(
        self,
        project_name: str,
        episode: int,
        decision: str,
        note: str | None,
        entry_ids: list[str] | None,
    ) -> None:
        if decision not in direction_review.DECISIONS:
            raise DirectionReviewError("invalid_decision")
        project = self.pm.load_project(project_name)
        if find_episode(project, episode) is None:
            raise DirectionReviewError("episode_not_found")
        kind = direction_review.entry_kind(project)
        if kind is None:
            raise DirectionReviewError("not_applicable")
        script = read_formal_script(self.pm, project_name, project, episode)
        if script is None or not direction_review.direction_complete(script, kind):
            raise DirectionReviewError("no_direction")
        status = direction_review.review_status(project, script, episode)
        clean_note = note.strip() if isinstance(note, str) and note.strip() else None
        clean_ids = list(dict.fromkeys(entry_ids)) if entry_ids else None
        if decision == direction_review.DECISION_APPROVE:
            if status not in {"pending_review", "changes_requested"}:
                raise DirectionReviewError("not_reviewable")
            clean_note, clean_ids = None, None
        else:
            if not direction_review.has_pending_authoring(script, kind):
                raise DirectionReviewError("authored")
            if decision == direction_review.DECISION_REDO:
                clean_ids = None
            elif clean_note is None and clean_ids is None:
                raise DirectionReviewError("invalid_decision", "changes requires a note or entry_ids")
            if clean_ids:
                known = {str(item[SKELETONS[kind].id_field]) for item in direction_review.script_entries(script, kind)}
                unknown = [entry_id for entry_id in clean_ids if entry_id not in known]
                if unknown:
                    raise DirectionReviewError("unknown_entries", f"unknown entry_ids: {unknown}")
        fingerprint = direction_review.direction_fingerprint(script, kind)
        assert fingerprint is not None
        decided_at = datetime.now(UTC).isoformat()

        def _record(project_dict: dict[str, Any]) -> None:
            if not direction_review.apply_decision(
                project_dict,
                episode,
                fingerprint=fingerprint,
                decision=decision,
                decided_at=decided_at,
                note=clean_note,
                entry_ids=clean_ids,
            ):
                raise DirectionReviewError("episode_not_found")

        self.pm.update_project(project_name, _record)


__all__ = ["DirectionReviewError", "DirectionReviewService", "read_formal_script"]
