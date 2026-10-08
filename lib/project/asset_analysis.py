"""资产分析服务：读尚未分析的源文单元 → 调项目配置的文本模型 → 增量写入角色 / 场景 / 道具并记完成事实。

源文单元优先取分集源文 ``source/episode_N.txt``（手动上传的分集），没有分集文件时取 scope=all 读到的源文。
每个单元按内容指纹记录「已分析」（``workflow.analyzed_units``），新上传或被替换的集只重新分析自己；
已登记的资产不重复提取、不被覆盖。全部批次成功后与完成事实在同一把项目锁内一并提交
（:func:`lib.project.asset_inventory.complete_asset_inventory`），中途失败不写任何东西。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import TextGenerationRequest, TextTaskType
from lib.backends.text_generator import TextGenerator
from lib.project.asset_inventory import (
    ANALYZED_UNITS_FIELD,
    AssetInventoryCompletion,
    AssetInventoryError,
    AssetInventoryRevisionConflict,
    complete_asset_inventory,
)
from lib.project.asset_types import ASSET_SPECS, resolve_asset_key, validate_asset_name
from lib.project.project_manager import ProjectManager, resolve_source_kind
from lib.project.source_revision import SourceScope, compute_source_revision
from lib.project.source_units import SourceUnit, collect_source_units
from lib.prompts.prompt_templates.builtin import builtin_templates

logger = logging.getLogger(__name__)

#: 单次模型调用的源文字符上限；超出按单元拆成多批，同一批内单元不拆。
DEFAULT_BATCH_CHARS = 60000

#: 分析期间源文被并发改动（完成事实的 revision 复核冲突）时的总尝试次数。
_MAX_REVISION_ATTEMPTS = 3

_DRAFT_CONFIG = ConfigDict(extra="forbid")


class AssetAnalysisError(RuntimeError):
    """资产分析失败（源文不可读、模型输出不合 schema、revision 反复冲突等）。"""


class DerivativeDraft(BaseModel):
    model_config = _DRAFT_CONFIG

    name: str = Field(min_length=1)
    description: str


class CharacterDraft(BaseModel):
    model_config = _DRAFT_CONFIG

    name: str = Field(min_length=1)
    description: str
    voice_style: str = ""
    derivatives: list[DerivativeDraft] = Field(default_factory=list)


class AssetDraft(BaseModel):
    model_config = _DRAFT_CONFIG

    name: str = Field(min_length=1)
    description: str


class AssetAnalysisDraft(BaseModel):
    """模型输出：三类资产的新增条目（列表形态，供各家结构化输出稳定约束）。"""

    model_config = _DRAFT_CONFIG

    characters: list[CharacterDraft] = Field(default_factory=list)
    scenes: list[AssetDraft] = Field(default_factory=list)
    props: list[AssetDraft] = Field(default_factory=list)


@dataclass
class AssetAnalysisResult:
    analyzed_units: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    added: dict[str, list[str]] = field(default_factory=dict)
    skipped_units: list[str] = field(default_factory=list)


def _pending_units(project: Mapping[str, Any], units: list[SourceUnit]) -> list[SourceUnit]:
    workflow = project.get("workflow")
    recorded = workflow.get(ANALYZED_UNITS_FIELD) if isinstance(workflow, Mapping) else None
    recorded = recorded if isinstance(recorded, Mapping) else {}
    return [unit for unit in units if recorded.get(unit.key) != unit.fingerprint]


def _batches(units: list[SourceUnit], batch_chars: int) -> list[list[SourceUnit]]:
    batches: list[list[SourceUnit]] = []
    current: list[SourceUnit] = []
    size = 0
    for unit in units:
        if current and size + len(unit.text) > batch_chars:
            batches.append(current)
            current, size = [], 0
        current.append(unit)
        size += len(unit.text)
    if current:
        batches.append(current)
    return batches


def _existing_names(project: Mapping[str, Any], accumulated: Mapping[str, Mapping[str, Any]]) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for kind, spec in ASSET_SPECS.items():
        if kind == "product":
            continue
        bucket = project.get(spec.bucket_key)
        current = list(bucket) if isinstance(bucket, Mapping) else []
        current.extend(name for name in accumulated.get(spec.bucket_key, {}) if name not in current)
        names[spec.bucket_key] = [str(name) for name in current]
    return names


def _merge_draft(
    draft: AssetAnalysisDraft,
    project: Mapping[str, Any],
    entries: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """把一批模型输出并入待提交 entries：名字非法或已存在（项目内 / 先前批次）的条目静默丢弃。"""

    def _known(bucket_key: str, name: str) -> bool:
        project_bucket = project.get(bucket_key)
        if isinstance(project_bucket, Mapping) and resolve_asset_key(project_bucket, name) is not None:
            return True
        return resolve_asset_key(entries.setdefault(bucket_key, {}), name) is not None

    def _clean_name(raw: str) -> str | None:
        try:
            return validate_asset_name(raw)
        except ValueError:
            logger.warning("资产分析丢弃非法资产名: %r", raw)
            return None

    for character in draft.characters:
        name = _clean_name(character.name)
        if name is None or _known("characters", name):
            continue
        derivatives: dict[str, dict[str, str]] = {}
        for derivative in character.derivatives:
            derivative_name = _clean_name(derivative.name)
            if derivative_name is not None and derivative_name not in derivatives:
                derivatives[derivative_name] = {"description": derivative.description}
        entry: dict[str, Any] = {"description": character.description, "voice_style": character.voice_style}
        if derivatives:
            entry["derivatives"] = derivatives
        entries["characters"][name] = entry
    for bucket_key, drafts in (("scenes", draft.scenes), ("props", draft.props)):
        for asset in drafts:
            name = _clean_name(asset.name)
            if name is None or _known(bucket_key, name):
                continue
            entries[bucket_key][name] = {"description": asset.description}


def _build_prompt(
    project: Mapping[str, Any],
    units: list[SourceUnit],
    existing: Mapping[str, list[str]],
) -> str:
    overview = project.get("overview")
    overview = overview if isinstance(overview, Mapping) else {}
    raw_language = project.get("source_language")
    target_language = raw_language if isinstance(raw_language, str) and raw_language.strip() else "中文"
    source_content = "\n\n".join(f"## {unit.title}\n{unit.text}" for unit in units)
    return builtin_templates.render(
        "text/asset_analysis",
        source_kind=resolve_source_kind(dict(project)),
        target_language=target_language,
        synopsis=overview.get("synopsis") or None,
        genre=overview.get("genre") or None,
        existing_characters=existing.get("characters", []),
        existing_scenes=existing.get("scenes", []),
        existing_props=existing.get("props", []),
        source_content=source_content,
    )


class AssetAnalyzer:
    """资产分析器。``generator`` 为 None 时仅可构造，调用 analyze() 会报错。"""

    def __init__(
        self,
        pm: ProjectManager,
        project_name: str,
        generator: TextGenerator | None = None,
        *,
        batch_chars: int = DEFAULT_BATCH_CHARS,
    ):
        self.pm = pm
        self.project_name = project_name
        self.generator = generator
        self.batch_chars = batch_chars

    @classmethod
    async def create(cls, pm: ProjectManager, project_name: str) -> AssetAnalyzer:
        """异步工厂：按项目配置创建文本后端（与项目概述同一条 OVERVIEW 任务配置链）。"""
        generator = await TextGenerator.create(TextTaskType.OVERVIEW, project_name, purpose=CallPurpose.ASSET_ANALYSIS)
        return cls(pm, project_name, generator)

    async def analyze(self) -> AssetAnalysisResult:
        for attempt in range(1, _MAX_REVISION_ATTEMPTS + 1):
            try:
                return await self._analyze_once()
            except AssetInventoryRevisionConflict:
                # 分析期间源文又被改动（如连续上传多集）：重读后只补分析新增 / 变化的单元
                if attempt == _MAX_REVISION_ATTEMPTS:
                    raise AssetAnalysisError("分析期间源文被反复修改，请稍后重试") from None
                logger.info("资产分析期间源文变动，重新读取后继续（第 %d 次）", attempt)
        raise AssetAnalysisError("资产分析未执行")  # pragma: no cover - 循环必然返回或抛出

    async def _analyze_once(self) -> AssetAnalysisResult:
        if self.generator is None:
            raise AssetAnalysisError("未配置文本模型，无法分析资产")
        project_dir = self.pm.get_project_path(self.project_name)
        project = self.pm.load_project(self.project_name)
        revision = compute_source_revision(project_dir, project, SourceScope(kind="all"))
        if revision.blockers:
            reasons = "；".join(f"{blocker.path}: {blocker.reason}" for blocker in revision.blockers)
            raise AssetAnalysisError(f"源文不可用于分析：{reasons}")
        if revision.revision is None:
            raise AssetAnalysisError("源文版本不可用")
        units = collect_source_units(project_dir, revision.documents)
        if not units:
            raise AssetAnalysisError("没有可分析的源文")
        pending = _pending_units(project, units)

        entries: dict[str, dict[str, dict[str, Any]]] = {"characters": {}, "scenes": {}, "props": {}}
        for batch in _batches(pending, self.batch_chars):
            existing = _existing_names(project, entries)
            draft = await self._request_draft(_build_prompt(project, batch, existing))
            _merge_draft(draft, project, entries)

        completion = await self._commit(revision.revision, entries, pending)
        return self._result(completion, entries, pending, units)

    async def _request_draft(self, prompt: str) -> AssetAnalysisDraft:
        assert self.generator is not None
        result = await self.generator.generate(
            TextGenerationRequest(prompt=prompt, response_schema=AssetAnalysisDraft),
            project_name=self.project_name,
        )
        try:
            return AssetAnalysisDraft.model_validate_json(result.text)
        except ValidationError as exc:
            raise AssetAnalysisError(f"模型输出不符合资产分析 schema：{exc}") from exc

    async def _commit(
        self,
        expected_revision: str,
        entries: dict[str, dict[str, dict[str, Any]]],
        pending: list[SourceUnit],
    ) -> AssetInventoryCompletion:
        from lib.infra.async_thread import run_sync_transaction

        def _complete() -> AssetInventoryCompletion:
            return complete_asset_inventory(
                self.pm,
                self.project_name,
                SourceScope(kind="all"),
                expected_revision,
                entries,
                analyzed_units={unit.key: unit.fingerprint for unit in pending},
            )

        try:
            return await run_sync_transaction(_complete)
        except AssetInventoryRevisionConflict:
            raise
        except AssetInventoryError as exc:
            raise AssetAnalysisError(str(exc)) from exc

    @staticmethod
    def _result(
        completion: AssetInventoryCompletion,
        entries: dict[str, dict[str, dict[str, Any]]],
        pending: list[SourceUnit],
        units: list[SourceUnit],
    ) -> AssetAnalysisResult:
        pending_keys = {unit.key for unit in pending}
        return AssetAnalysisResult(
            analyzed_units=[unit.key for unit in pending],
            counts=dict(completion.counts),
            added={bucket: sorted(names) for bucket, names in entries.items()},
            skipped_units=[unit.key for unit in units if unit.key not in pending_keys],
        )


__all__ = [
    "AssetAnalysisDraft",
    "AssetAnalysisError",
    "AssetAnalysisResult",
    "AssetAnalyzer",
]
