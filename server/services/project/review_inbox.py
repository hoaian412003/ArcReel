"""Project-wide human review inbox and one-pass Vietnamese summaries."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import TextGenerationRequest, TextTaskType
from lib.backends.text_generator import TextGenerator
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_state import WorkflowStatus
from server.services.project.direction_review import DirectionReviewService
from server.services.project.screenplay_review import ScreenplayReviewService
from server.services.project.script_review import ScriptReviewService
from server.services.project.workflow_planner import WorkflowPlanner

ReviewKind = Literal["screenplay", "script_plan", "direction"]

_ACTION_KIND: dict[str, ReviewKind] = {
    "review_screenplay": "screenplay",
    "confirm_script_plan": "script_plan",
    "review_direction": "direction",
}
_CACHE_FILENAME = ".review-summaries.json"
_locks: dict[tuple[str, int, ReviewKind], asyncio.Lock] = {}


@dataclass(frozen=True, slots=True)
class ReviewSource:
    episode: int
    title: str
    kind: ReviewKind
    fingerprint: str
    content: dict[str, Any]
    blockers: list[dict[str, Any]]

    @property
    def task_id(self) -> str:
        return f"{self.kind}:{self.episode}"


class ReviewInboxService:
    def __init__(self, projects: ProjectManager, workflow_planner: WorkflowPlanner | None = None) -> None:
        self.projects = projects
        self.workflow_planner = workflow_planner or WorkflowPlanner(projects)

    async def list_tasks(self, project_name: str) -> list[dict[str, Any]]:
        statuses = await self.workflow_planner.get_episode_statuses(project_name)
        cache = await asyncio.to_thread(self._read_cache, project_name)
        tasks: list[dict[str, Any]] = []
        for episode, title, status in statuses:
            kind = _ACTION_KIND.get(status.next_action.type.value)
            if kind is None:
                continue
            source = await self._source(project_name, episode, title, kind, status)
            cached = cache.get(source.task_id)
            summary = (
                cached.get("summary")
                if isinstance(cached, dict) and cached.get("fingerprint") == source.fingerprint
                else None
            )
            tasks.append(self._task_payload(source, summary=summary))
        return tasks

    async def summarize(
        self,
        project_name: str,
        episode: int,
        kind: ReviewKind,
        *,
        user_id: str,
    ) -> dict[str, Any]:
        statuses = await self.workflow_planner.get_episode_statuses(project_name)
        match = next((item for item in statuses if item[0] == episode), None)
        if match is None or _ACTION_KIND.get(match[2].next_action.type.value) != kind:
            raise ValueError("review_task_not_pending")
        source = await self._source(project_name, episode, match[1], kind, match[2])
        lock = _locks.setdefault((project_name, episode, kind), asyncio.Lock())
        async with lock:
            cache = await asyncio.to_thread(self._read_cache, project_name)
            cached = cache.get(source.task_id)
            if isinstance(cached, dict) and cached.get("fingerprint") == source.fingerprint:
                return self._task_payload(source, summary=str(cached.get("summary") or ""))

            generator = await TextGenerator.create(
                TextTaskType.REVIEW_SUMMARY,
                project_name,
                purpose=CallPurpose.REVIEW_SUMMARY,
                user_id=user_id,
            )
            body = json.dumps(source.content, ensure_ascii=False, separators=(",", ":"))
            result = await generator.generate(
                TextGenerationRequest(
                    system_prompt=(
                        "Bạn là trợ lý duyệt nội dung của một nền tảng sản xuất video. "
                        "Hãy tóm tắt chính xác bằng tiếng Việt, không bịa thêm và không đưa ra quyết định thay người dùng."
                    ),
                    prompt=(
                        f"Loại nội dung: {kind}. Tập {episode}: {source.title}.\n"
                        "Viết 3 phần ngắn: (1) Nội dung chính, (2) Điểm cần chú ý, "
                        "(3) Câu hỏi người duyệt nên tự kiểm tra. Tối đa 220 từ.\n\n"
                        f"DỮ LIỆU CẦN DUYỆT:\n{body[:80000]}"
                    ),
                    max_output_tokens=1200,
                ),
                project_name,
            )
            summary = result.text.strip()
            cache[source.task_id] = {
                "fingerprint": source.fingerprint,
                "summary": summary,
                "provider": result.provider,
                "model": result.model,
            }
            await asyncio.to_thread(self._write_cache, project_name, cache)
            return self._task_payload(source, summary=summary, provider=result.provider, model=result.model)

    async def _source(
        self,
        project_name: str,
        episode: int,
        title: str,
        kind: ReviewKind,
        status: WorkflowStatus,
    ) -> ReviewSource:
        if kind == "screenplay":
            state = await ScreenplayReviewService(self.projects).get_state(project_name, episode)
            content = {key: state.get(key) for key in ("title", "logline", "total_seconds", "scenes", "markdown")}
        elif kind == "script_plan":
            state = await ScriptReviewService(self.projects).get_state(project_name, episode)
            content = state.get("content") or {}
        else:
            state = await DirectionReviewService(self.projects).get_state(project_name, episode)
            content = state.get("report") or {}
        fingerprint = state.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("review_source_missing_fingerprint")
        blockers = [problem.model_dump(mode="json") for problem in status.blockers]
        return ReviewSource(episode, title, kind, fingerprint, content, blockers)

    def _cache_path(self, project_name: str):
        return self.projects.get_project_path(project_name) / _CACHE_FILENAME

    def _read_cache(self, project_name: str) -> dict[str, Any]:
        value = load_json_or_none(self._cache_path(project_name))
        return value if isinstance(value, dict) else {}

    def _write_cache(self, project_name: str, cache: dict[str, Any]) -> None:
        atomic_write_json(self._cache_path(project_name), cache)

    @staticmethod
    def _task_payload(
        source: ReviewSource,
        *,
        summary: str | None,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": source.task_id,
            "episode": source.episode,
            "title": source.title,
            "kind": source.kind,
            "fingerprint": source.fingerprint,
            "blockers": source.blockers,
            "summary": summary,
            "summary_status": "ready" if summary else "missing",
            "summary_provider": provider,
            "summary_model": model,
        }


__all__ = ["ReviewInboxService", "ReviewKind"]
