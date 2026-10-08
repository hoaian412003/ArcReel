"""资产分析：按集增量提取、已登记资产不覆盖、完成事实与指纹同提交（mock 文本后端，不触真实 LLM）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.backends.text_backends.base import TextGenerationRequest, TextGenerationResult
from lib.episode.episode_upload import upload_episode_source
from lib.project.asset_analysis import AssetAnalysisError, AssetAnalyzer
from lib.project.project_manager import ProjectManager


class _FakeTextGenerator:
    def __init__(self, responses: list[dict[str, Any] | BaseException | str]):
        self._responses = list(responses)
        self.requests: list[TextGenerationRequest] = []

    async def generate(self, request: TextGenerationRequest, project_name: str | None = None) -> TextGenerationResult:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        return TextGenerationResult(text=text, provider="fake", model="fake-model")


def _project(tmp_path: Path, **extra: Any) -> tuple[ProjectManager, str]:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    project = {
        "schema_version": 3,
        "title": "测试项目",
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_language": "中文",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": [],
        **extra,
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return ProjectManager.for_project_dir(project_dir), "demo"


def _load(pm: ProjectManager, name: str) -> dict:
    return json.loads((pm.get_project_path(name) / "project.json").read_text(encoding="utf-8"))


_EP1_DRAFT = {
    "characters": [
        {
            "name": "李恒",
            "description": "青衫少年，腰悬木剑",
            "voice_style": "清朗",
            "derivatives": [{"name": "战斗装", "description": "换上黑色重甲，其余外观保持不变"}],
        }
    ],
    "scenes": [{"name": "山村", "description": "晨雾笼罩的山村"}],
    "props": [{"name": "古玉", "description": "半透明白玉"}],
}


async def test_first_analysis_writes_assets_the_inventory_marker_and_unit_fingerprints(tmp_path: Path) -> None:
    pm, name = _project(tmp_path)
    upload_episode_source(pm, name, 1, "李恒在山村得到古玉。")
    generator = _FakeTextGenerator([_EP1_DRAFT])

    result = await AssetAnalyzer(pm, name, generator).analyze()

    project = _load(pm, name)
    assert project["characters"]["李恒"]["description"] == "青衫少年，腰悬木剑"
    assert project["characters"]["李恒"]["derivatives"]["战斗装"]["description"].startswith("换上黑色重甲")
    assert "山村" in project["scenes"]
    assert "古玉" in project["props"]
    assert project["workflow"]["asset_inventory"]["scope"] == {"kind": "all", "files": []}
    assert list(project["workflow"]["analyzed_units"]) == ["1"]
    assert result.analyzed_units == ["1"]
    assert result.added["characters"] == ["李恒"]


async def test_second_upload_only_analyses_the_new_episode_and_keeps_existing_assets(tmp_path: Path) -> None:
    pm, name = _project(tmp_path)
    upload_episode_source(pm, name, 1, "李恒在山村得到古玉。")
    await AssetAnalyzer(pm, name, _FakeTextGenerator([_EP1_DRAFT])).analyze()
    edited = _load(pm, name)
    edited["characters"]["李恒"]["description"] = "人工改过的描述"
    (pm.get_project_path(name) / "project.json").write_text(json.dumps(edited, ensure_ascii=False), encoding="utf-8")
    upload_episode_source(pm, name, 2, "李恒与少女在城门相遇。")
    generator = _FakeTextGenerator(
        [
            {
                "characters": [
                    {"name": "李恒", "description": "模型想覆盖的描述"},
                    {"name": "少女", "description": "红衣少女"},
                ],
                "scenes": [],
                "props": [],
            }
        ]
    )

    result = await AssetAnalyzer(pm, name, generator).analyze()

    project = _load(pm, name)
    assert project["characters"]["李恒"]["description"] == "人工改过的描述"
    assert project["characters"]["少女"]["description"] == "红衣少女"
    assert result.analyzed_units == ["2"]
    assert result.skipped_units == ["1"]
    prompt = generator.requests[0].prompt
    assert "城门相遇" in prompt
    assert "古玉" not in prompt.split("# 源文")[1]
    assert "李恒" in prompt.split("# 源文")[0]


async def test_a_failed_model_call_writes_nothing_and_leaves_the_episode_pending(tmp_path: Path) -> None:
    pm, name = _project(tmp_path)
    upload_episode_source(pm, name, 1, "李恒在山村得到古玉。")

    with pytest.raises(RuntimeError, match="provider down"):
        await AssetAnalyzer(pm, name, _FakeTextGenerator([RuntimeError("provider down")])).analyze()

    project = _load(pm, name)
    assert project["characters"] == {}
    assert "workflow" not in project


async def test_output_that_breaks_the_schema_is_reported_without_committing(tmp_path: Path) -> None:
    pm, name = _project(tmp_path)
    upload_episode_source(pm, name, 1, "李恒在山村得到古玉。")

    with pytest.raises(AssetAnalysisError, match="schema"):
        await AssetAnalyzer(pm, name, _FakeTextGenerator(['{"characters": "oops"}'])).analyze()

    assert "workflow" not in _load(pm, name)


async def test_source_changing_mid_analysis_is_retried_against_the_new_revision(tmp_path: Path) -> None:
    pm, name = _project(tmp_path)
    upload_episode_source(pm, name, 1, "李恒在山村得到古玉。")

    class _UploadingGenerator(_FakeTextGenerator):
        async def generate(self, request: TextGenerationRequest, project_name: str | None = None):
            if len(self.requests) == 0:
                upload_episode_source(pm, name, 2, "第二集在分析期间上传。")
            return await super().generate(request, project_name)

    generator = _UploadingGenerator([_EP1_DRAFT, {"characters": [], "scenes": [], "props": []}])

    result = await AssetAnalyzer(pm, name, generator).analyze()

    assert sorted(_load(pm, name)["workflow"]["analyzed_units"]) == ["1", "2"]
    assert len(generator.requests) == 2
    assert result.analyzed_units == ["1", "2"]


async def test_analysis_with_no_source_is_refused(tmp_path: Path) -> None:
    pm, name = _project(tmp_path)

    with pytest.raises(AssetAnalysisError):
        await AssetAnalyzer(pm, name, _FakeTextGenerator([])).analyze()
