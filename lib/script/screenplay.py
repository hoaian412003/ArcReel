"""编剧步骤的产物：面向审阅者的单集剧本（screenplay）。

编剧把本集源文改编为按场次组织的可读剧本，人工逐场审阅、修改后通过；后续的单元拆解（``break_down_units``）
以通过的剧本为依据划分单元、定时长、登记资产。剧本落在 ``drafts/episode_N/screenplay.json``：

- 内容：``title`` / ``logline`` / ``scenes[{heading, summary, estimated_seconds, text}]``；
- 审阅：``approval = {fingerprint, approved_at}``，指纹取内容部分（不含 ``approval``）的规范化摘要，
  任何内容修改都使指纹变化、审阅自动失效，不需要单独的失效标记；
- 来源：``source_digest`` 是写入时那份源文的摘要，据它判断剧本是否还对得上当前源文（分集被重划后源文会变）；
- 总时长不落盘：由各场 ``estimated_seconds`` 相加得出，落一份只会与正文漂移。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from lib.artifacts.formal_write import project_metadata_lock
from lib.episode.episode_paths import episode_drafts_dir
from lib.infra.content_digest import canonical_json_digest
from lib.infra.json_io import atomic_write_json

SCREENPLAY_FILENAME = "screenplay.json"
SCREENPLAY_VERSION = 1
MIN_SCENE_SECONDS = 1
MAX_SCENE_SECONDS = 600
MAX_SCENES = 200

ScreenplayReviewStatus = Literal["none", "pending_review", "approved"]

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ScreenplayError(ValueError):
    """剧本文件损坏、请求与现状冲突或编辑操作非法。``code`` 供路由 / 工具映射为结构化问题。"""

    def __init__(self, message: str, *, code: str = "screenplay_invalid") -> None:
        super().__init__(message)
        self.code = code


class ScreenplayScene(BaseModel):
    """一场戏。``text`` 是 Markdown 纯文本：动作 / 环境、对白、画外音。"""

    model_config = ConfigDict(extra="forbid")

    heading: _Text = Field(description="场景标题：内景/外景 · 地点 · 时间")
    summary: _Text = Field(description="这一场发生了什么、为什么重要，一到两句")
    estimated_seconds: int = Field(ge=MIN_SCENE_SECONDS, le=MAX_SCENE_SECONDS, description="该场预计时长（秒，正整数）")
    text: _Text = Field(description="剧本正文（Markdown）：动作 / 环境描写、对白、画外音")


class ScreenplayDraft(BaseModel):
    """编剧步骤的模型产出（``response_schema``）。总时长不在其中：由各场时长相加得出。"""

    model_config = ConfigDict(extra="forbid")

    title: _Text = Field(description="本集标题")
    logline: _Text = Field(description="本集看点：核心冲突、观众期待与情绪走向，一到两句")
    scenes: list[ScreenplayScene] = Field(min_length=1, max_length=MAX_SCENES, description="按叙事顺序排列的场次")


def screenplay_path(project_path: Path, episode: int) -> Path:
    return episode_drafts_dir(project_path, episode) / SCREENPLAY_FILENAME


def source_digest(source_text: str) -> str:
    """源文摘要：写入剧本时记下，之后据此判断剧本是否仍对应当前源文。"""
    return canonical_json_digest(source_text)


def matches_source(data: Mapping[str, Any], source_text: str) -> bool:
    """剧本对得上当前源文。写入时没有记录来源的剧本不判定（视为对得上）。"""
    recorded = data.get("source_digest")
    return not isinstance(recorded, str) or recorded == source_digest(source_text)


def _content(data: Mapping[str, Any]) -> dict[str, Any]:
    """参与指纹与展示的内容部分（不含审阅记录与文件元数据）。"""
    return {"title": data["title"], "logline": data["logline"], "scenes": data["scenes"]}


def screenplay_fingerprint(data: Mapping[str, Any]) -> str:
    return canonical_json_digest(_content(data))


def total_seconds(data: Mapping[str, Any]) -> int:
    """本集预计总时长：各场 ``estimated_seconds`` 之和。"""
    return sum(int(scene["estimated_seconds"]) for scene in data["scenes"])


def review_status(data: Mapping[str, Any] | None) -> ScreenplayReviewStatus:
    if data is None:
        return "none"
    approval = data.get("approval")
    if isinstance(approval, Mapping) and approval.get("fingerprint") == screenplay_fingerprint(data):
        return "approved"
    return "pending_review"


def _validated(raw: object, episode: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ScreenplayError(f"第 {episode} 集剧本文件结构损坏：顶层须为对象", code="screenplay_corrupt")
    try:
        draft = ScreenplayDraft.model_validate({key: raw.get(key) for key in ("title", "logline", "scenes")})
    except ValidationError as exc:
        raise ScreenplayError(f"第 {episode} 集剧本文件内容不合法：{exc}", code="screenplay_corrupt") from exc
    approval = raw.get("approval")
    if approval is not None and not (
        isinstance(approval, dict) and isinstance(approval.get("fingerprint"), str) and approval["fingerprint"]
    ):
        approval = None
    digest = raw.get("source_digest")
    return {
        "version": SCREENPLAY_VERSION,
        "episode": episode,
        **draft.model_dump(mode="json"),
        "approval": approval,
        "source_digest": digest if isinstance(digest, str) and digest else None,
    }


def load_screenplay(project_path: Path, episode: int) -> dict[str, Any] | None:
    """读取本集剧本；文件不存在返回 ``None``，存在但损坏抛 :class:`ScreenplayError`。"""
    path = screenplay_path(project_path, episode)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScreenplayError(f"第 {episode} 集剧本文件无法读取：{exc}", code="screenplay_corrupt") from exc
    return _validated(raw, episode)


def _store(project_path: Path, episode: int, data: dict[str, Any]) -> dict[str, Any]:
    path = screenplay_path(project_path, episode)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)
    return data


def write_screenplay(
    project_path: Path, episode: int, draft: ScreenplayDraft, *, source_text: str | None = None
) -> dict[str, Any]:
    """写入编剧产出（整份替换，审阅记录清空）。``source_text`` 给出时记下它的摘要，供之后判断剧本是否过期。"""
    data = {
        "version": SCREENPLAY_VERSION,
        "episode": episode,
        **draft.model_dump(mode="json"),
        "approval": None,
        "source_digest": source_digest(source_text) if source_text is not None else None,
    }
    with project_metadata_lock(project_path):
        return _store(project_path, episode, data)


def _require_current(project_path: Path, episode: int, expected_fingerprint: str | None) -> dict[str, Any]:
    data = load_screenplay(project_path, episode)
    if data is None:
        raise ScreenplayError(f"第 {episode} 集还没有剧本，请先运行编剧步骤", code="screenplay_missing")
    if expected_fingerprint is not None and expected_fingerprint != screenplay_fingerprint(data):
        raise ScreenplayError(
            f"第 {episode} 集剧本在你读取之后被改动，请刷新后重试", code="screenplay_revision_conflict"
        )
    return data


def approve_screenplay(project_path: Path, episode: int, *, expected_fingerprint: str | None = None) -> dict[str, Any]:
    """记录对当前内容的审阅通过。``expected_fingerprint`` 给出时须与当前内容一致，防止通过一份没看过的版本。"""
    with project_metadata_lock(project_path):
        data = _require_current(project_path, episode, expected_fingerprint)
        data["approval"] = {
            "fingerprint": screenplay_fingerprint(data),
            "approved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        return _store(project_path, episode, data)


_SCENE_FIELDS = frozenset({"heading", "summary", "estimated_seconds", "text"})


def edit_scenes(
    project_path: Path,
    episode: int,
    operations: Sequence[Mapping[str, Any]],
    *,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    """按场次修改剧本；任何成功的修改都使审阅失效（指纹随内容变化）。

    ``operations`` 逐条顺序应用，任一条非法则整批不落盘：

    - ``{"op": "update", "index": i, "fields": {...}}``：改第 i 场（从 0 起）的部分字段；
    - ``{"op": "insert", "index": i, "scene": {...}}``：在第 i 位前插入整场（``i == 场数`` 即追加）；
    - ``{"op": "delete", "index": i}``：删除第 i 场（不能删光）；
    - ``{"op": "set_meta", "fields": {"title"?, "logline"?}}``：改标题 / 看点。
    """
    with project_metadata_lock(project_path):
        data = _require_current(project_path, episode, expected_fingerprint)
        scenes: list[dict[str, Any]] = [dict(scene) for scene in data["scenes"]]
        title, logline = data["title"], data["logline"]
        for position, operation in enumerate(operations):
            label = f"第 {position + 1} 条操作"
            op = operation.get("op")
            if op == "set_meta":
                fields = operation.get("fields")
                if not isinstance(fields, Mapping) or not fields or set(fields) - {"title", "logline"}:
                    raise ScreenplayError(f"{label}：set_meta 只接受 title / logline", code="screenplay_bad_edit")
                title = fields.get("title", title)
                logline = fields.get("logline", logline)
                continue
            index = operation.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise ScreenplayError(f"{label}：index 须为整数", code="screenplay_bad_edit")
            if op == "update":
                fields = operation.get("fields")
                if not isinstance(fields, Mapping) or not fields or set(fields) - _SCENE_FIELDS:
                    raise ScreenplayError(f"{label}：fields 只接受 {sorted(_SCENE_FIELDS)}", code="screenplay_bad_edit")
                if not 0 <= index < len(scenes):
                    raise ScreenplayError(f"{label}：场次 {index} 不存在", code="screenplay_bad_edit")
                scenes[index] = {**scenes[index], **fields}
            elif op == "insert":
                scene = operation.get("scene")
                if not isinstance(scene, Mapping):
                    raise ScreenplayError(f"{label}：insert 须给出完整的 scene", code="screenplay_bad_edit")
                if not 0 <= index <= len(scenes):
                    raise ScreenplayError(f"{label}：插入位置 {index} 越界", code="screenplay_bad_edit")
                scenes.insert(index, dict(scene))
            elif op == "delete":
                if not 0 <= index < len(scenes):
                    raise ScreenplayError(f"{label}：场次 {index} 不存在", code="screenplay_bad_edit")
                del scenes[index]
            else:
                raise ScreenplayError(f"{label}：未知操作 {op!r}", code="screenplay_bad_edit")
        try:
            draft = ScreenplayDraft.model_validate({"title": title, "logline": logline, "scenes": scenes})
        except ValidationError as exc:
            raise ScreenplayError(f"修改后的剧本不合法：{exc}", code="screenplay_bad_edit") from exc
        # 内容没有变化时保留既有审阅：指纹相同，审阅仍然有效。
        updated = {
            "version": SCREENPLAY_VERSION,
            "episode": episode,
            **draft.model_dump(mode="json"),
            "approval": data.get("approval"),
            "source_digest": data.get("source_digest"),
        }
        return _store(project_path, episode, updated)


def render_markdown(data: Mapping[str, Any]) -> str:
    """面向审阅者的可读版本：标题、看点、逐场正文与时长，末尾为总时长。"""
    lines = [f"# {data['title']}", "", f"**本集看点**：{data['logline']}", ""]
    for number, scene in enumerate(data["scenes"], start=1):
        lines += [
            "---",
            "",
            f"## 第 {number} 场　{scene['heading']}",
            f"**剧情**：{scene['summary']}",
            f"**预计时长**：约 {scene['estimated_seconds']} 秒",
            "",
            str(scene["text"]),
            "",
        ]
    lines += ["---", "", f"**本集预计总时长**：约 {total_seconds(data)} 秒"]
    return "\n".join(lines)


def screenplay_summary(data: Mapping[str, Any]) -> dict[str, Any]:
    """读侧摘要：场数、逐场时长、总时长、审阅状态与内容指纹（供前端 / 工具展示与并发校验）。"""
    return {
        "episode": data["episode"],
        "title": data["title"],
        "logline": data["logline"],
        "scene_count": len(data["scenes"]),
        "scene_seconds": [int(scene["estimated_seconds"]) for scene in data["scenes"]],
        "total_seconds": total_seconds(data),
        "status": review_status(data),
        "fingerprint": screenplay_fingerprint(data),
    }
