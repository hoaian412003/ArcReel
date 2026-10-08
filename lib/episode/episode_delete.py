"""删除一集：账本条目、集源文，以及（经确认）该集的剧本、script_plan 草稿与已登记的产物。

只处理这一集自己的东西：其他集、角色 / 场景 / 道具资产与它们的资产图不受影响。集号不重排，
后续上传仍可重用已删除的集号。下游产物（剧本 / script_plan / 产物清单里的媒体）不可恢复，
所以有产物的集须带 ``confirm_products=True`` 才执行，否则抛 :class:`EpisodeDeleteConfirmationRequired`
并列出将被删除的内容。账本提交后才删文件：提交失败不会留下半删状态，删文件失败只留下无账本引用的孤儿文件。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.episode.episode_ledger import discover_episode_file_aliases, has_downstream_products, parse_episode_num
from lib.episode.episode_paths import episode_drafts_dir, episode_script_relpath
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.project_manager import ProjectManager

logger = logging.getLogger(__name__)


class EpisodeDeleteError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EpisodeDeleteConfirmationRequired(EpisodeDeleteError):
    """该集已有下游产物，须确认后才删除；``products`` 是将被删除的内容概览。"""

    def __init__(self, episode: int, products: EpisodeProducts) -> None:
        super().__init__("confirmation_required", f"第 {episode} 集已有下游产物，删除后不可恢复，需确认")
        self.products = products


@dataclass(frozen=True)
class EpisodeProducts:
    script: bool
    drafts: bool
    artifacts: int


@dataclass
class EpisodeDeleteResult:
    episode: int
    removed_files: int = 0
    removed_artifacts: int = 0
    products: EpisodeProducts | None = None
    failed_paths: list[str] = field(default_factory=list)


def _episode_artifact_keys(project_dir: Path, episode: int) -> dict[ArtifactKey, str]:
    """产物清单里归属该集的条目：键 → 产物相对路径。"""
    snapshot = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    return {key: entry.artifact_path for key, entry in snapshot.items() if key.episode_number == episode}


def delete_episode(
    pm: ProjectManager,
    project_name: str,
    episode: int,
    *,
    confirm_products: bool = False,
) -> EpisodeDeleteResult:
    """删除第 ``episode`` 集。

    Raises:
        EpisodeDeleteError: 集号不在账本里（``episode_not_found``）。
        EpisodeDeleteConfirmationRequired: 有下游产物且未确认。
        FileNotFoundError: 项目不存在。
    """
    project_dir = pm.get_project_path(project_name)
    result = EpisodeDeleteResult(episode=episode)
    doomed: list[Path] = []
    drafts_dir = episode_drafts_dir(project_dir, episode)
    artifact_keys: dict[ArtifactKey, str] = {}

    def _commit(project: dict[str, Any]) -> None:
        episodes = project.get("episodes")
        episodes = episodes if isinstance(episodes, list) else []
        entry = next(
            (e for e in episodes if isinstance(e, dict) and parse_episode_num(e.get("episode")) == episode), None
        )
        if entry is None:
            raise EpisodeDeleteError("episode_not_found", f"第 {episode} 集不存在")
        artifact_keys.update(_episode_artifact_keys(project_dir, episode))
        script_rel = entry.get("script_file") if isinstance(entry.get("script_file"), str) else None
        script_rel = script_rel or episode_script_relpath(episode)
        products = EpisodeProducts(
            script=(project_dir / script_rel).is_file() or (project_dir / episode_script_relpath(episode)).is_file(),
            drafts=drafts_dir.is_dir(),
            artifacts=len(artifact_keys),
        )
        if (
            has_downstream_products(project_dir, episode, entry) or products.script or products.drafts or artifact_keys
        ) and not confirm_products:
            raise EpisodeDeleteConfirmationRequired(episode, products)
        result.products = products

        doomed.extend(discover_episode_file_aliases(project_dir).get(episode, []))
        for rel in {script_rel, episode_script_relpath(episode), *artifact_keys.values()}:
            try:
                path = safe_join(project_dir, rel)
            except PathTraversalError:
                logger.warning("删除第 %d 集时跳过越界路径: %r", episode, rel)
                continue
            if path.is_file() or path.is_symlink():
                doomed.append(path)
        project["episodes"] = [e for e in episodes if e is not entry]

    def _after_commit(_project_file: Path) -> None:
        adapter = ProjectArtifactManifestAdapter(project_dir)
        for key in artifact_keys:
            adapter.delete_entry(key)
            result.removed_artifacts += 1
        for path in doomed:
            try:
                path.unlink(missing_ok=True)
                result.removed_files += 1
            except OSError:
                logger.exception("删除第 %d 集文件失败: %s", episode, path)
                result.failed_paths.append(str(path.relative_to(project_dir)))
        if drafts_dir.is_dir():
            shutil.rmtree(drafts_dir, ignore_errors=True)

    pm.update_project(project_name, _commit, on_commit=_after_commit)
    return result


__all__ = [
    "EpisodeDeleteConfirmationRequired",
    "EpisodeDeleteError",
    "EpisodeDeleteResult",
    "EpisodeProducts",
    "delete_episode",
]
