"""分集源文上传：把一集原文写成 ``source/episode_N.txt`` 并登记账本条目。

drama 项目不经分集规划：用户在分集界面逐集上传（或替换）源文，本模块在同一把项目锁内写集文件与账本条目。
条目只带集号、标题与 ``script_file``，没有 ``source_range``——集文件本身就是这一集的最终记录
（见 :mod:`lib.episode.episode_ledger` 的手动预拆分语义）。替换已有下游产物的集时标 ``stale``、产物不删除。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lib.episode.episode_ledger import (
    discover_episode_file_aliases,
    has_downstream_products,
    normalize_source_text,
    parse_episode_num,
)
from lib.episode.episode_paths import episode_script_relpath, episode_source_path
from lib.project.project_manager import ProjectManager
from lib.script import script_review

logger = logging.getLogger(__name__)


class EpisodeUploadError(ValueError):
    """上传请求本身不合法（项目模式不支持、集号非法、正文为空）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EpisodeUploadResult:
    episode: int
    title: str
    created: bool
    stale: bool
    characters: int


def _sort_episodes(episodes: list[Any]) -> None:
    if all(isinstance(e, dict) and parse_episode_num(e.get("episode")) is not None for e in episodes):
        episodes.sort(key=lambda e: parse_episode_num(e["episode"]) or 0)


@dataclass(frozen=True)
class EpisodeSourceItem:
    episode: int
    text: str
    title: str | None = None


def upload_episode_sources(
    pm: ProjectManager,
    project_name: str,
    items: Sequence[EpisodeSourceItem],
) -> list[EpisodeUploadResult]:
    """在同一把项目锁、同一次账本提交里写入（新增或替换）多集源文。

    任一集不合法则整批不写；集号重复也整批拒绝。批内文件与账本要么全部生效要么全部回滚。

    Raises:
        EpisodeUploadError: 非 drama 项目 / 集号非正或重复 / 正文为空。
        FileNotFoundError: 项目不存在。
    """
    bodies: dict[int, str] = {}
    for item in items:
        if isinstance(item.episode, bool) or item.episode < 1:
            raise EpisodeUploadError("invalid_episode", "集号必须是正整数")
        if item.episode in bodies:
            raise EpisodeUploadError("invalid_episode", f"集号 {item.episode} 重复")
        body = normalize_source_text(item.text)
        if not body.strip():
            raise EpisodeUploadError("empty_source", "源文不能为空")
        bodies[item.episode] = body
    project_path = pm.get_project_path(project_name)
    results: dict[int, EpisodeUploadResult] = {}
    targets = {num: episode_source_path(project_path, num) for num in bodies}
    # 同集号的 padding 别名（episode_01.txt）与新文件指向同一集，替换时一并清掉
    all_aliases = discover_episode_file_aliases(project_path)
    aliases = {num: [p for p in all_aliases.get(num, []) if p != targets[num]] for num in bodies}

    def _commit(project: dict[str, Any]) -> None:
        if project.get("content_mode") != "drama":
            raise EpisodeUploadError("unsupported_mode", "仅剧情演绎（drama）项目支持上传分集源文")
        episodes = project.get("episodes")
        episodes = episodes if isinstance(episodes, list) else []
        for item in items:
            num = item.episode
            body = bodies[num]
            entry = next(
                (e for e in episodes if isinstance(e, dict) and parse_episode_num(e.get("episode")) == num),
                None,
            )
            created = entry is None
            if entry is None:
                entry = {"episode": num, "title": "", "script_file": episode_script_relpath(num)}
                episodes.append(entry)
            if item.title is not None and item.title.strip():
                entry["title"] = item.title.strip()
            # 手动上传的集没有原文范围：上一轮规划留下的坐标指向旧源文，不再成立
            entry.pop("source_range", None)
            stale = False
            if created:
                entry["ledger_status"] = "planned"
            elif has_downstream_products(project_path, num, entry):
                stale = True
                entry["ledger_status"] = "stale"
                script_plan_path = script_review.script_plan_path(project_path, project, num)
                entry[script_review.STALE_SCRIPT_PLAN_REVISION_FIELD] = (
                    script_review.content_fingerprint(script_plan_path) if script_plan_path is not None else None
                )
            targets[num].parent.mkdir(parents=True, exist_ok=True)
            targets[num].write_text(body, encoding="utf-8")
            for alias in aliases[num]:
                alias.unlink(missing_ok=True)
            results[num] = EpisodeUploadResult(
                episode=num,
                title=str(entry.get("title") or ""),
                created=created,
                stale=stale,
                characters=len(body),
            )
        _sort_episodes(episodes)
        project["episodes"] = episodes

    formal_paths = (*targets.values(), *(alias for group in aliases.values() for alias in group))
    pm.update_project(project_name, _commit, formal_paths=formal_paths)
    return [results[item.episode] for item in items]


def upload_episode_source(
    pm: ProjectManager,
    project_name: str,
    episode: int,
    text: str,
    *,
    title: str | None = None,
) -> EpisodeUploadResult:
    """写入（新增或替换）第 ``episode`` 集源文并登记账本；规则见 :func:`upload_episode_sources`。"""
    return upload_episode_sources(pm, project_name, [EpisodeSourceItem(episode, text, title)])[0]


__all__ = [
    "EpisodeSourceItem",
    "EpisodeUploadError",
    "EpisodeUploadResult",
    "upload_episode_source",
    "upload_episode_sources",
]
