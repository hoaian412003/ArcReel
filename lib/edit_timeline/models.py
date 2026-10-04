"""剪辑时间线的数据模型：剪辑片段、修订、时间线。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 转场词表：每一项都对应一个 ffmpeg xfade 效果（剪映映射随剪映草稿导出补充）。
TransitionName = Literal["cut", "fade", "dissolve", "wipe"]

TRANSITIONS: tuple[str, ...] = ("cut", "fade", "dissolve", "wipe")

#: 转场时长（秒）。
TRANSITION_SECONDS = 0.5


class Clip(BaseModel):
    """主轨上的一格：某视频单元在入点与出点之间的一段画面。

    ``out_s`` 为 ``None`` 表示到素材末尾；``trimmed_against_version`` 记录入出点针对哪个视频版本裁切，
    ``None`` 表示未裁切（整段使用）。
    """

    model_config = ConfigDict(extra="forbid")

    clip_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    in_s: float = Field(default=0.0, ge=0)
    out_s: float | None = Field(default=None, gt=0)
    trimmed_against_version: int | None = None
    transition_to_next: TransitionName = "cut"

    @model_validator(mode="after")
    def _check_range(self) -> Clip:
        if self.out_s is not None and self.out_s <= self.in_s:
            raise ValueError("out_s must be greater than in_s")
        return self


class Revision(BaseModel):
    """不可变修订：一次剪辑之后的完整片段序列。"""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    parent_revision: int | None = None
    author: str
    summary: str
    created_at: str
    clips: list[Clip]
    #: 成片是否混入越南语配音（原声压低、配音叠加）。旧文件没有这个字段，缺省关闭。
    voice_over: bool = False

    @model_validator(mode="after")
    def _unique_clip_ids(self) -> Revision:
        ids = [clip.clip_id for clip in self.clips]
        if len(ids) != len(set(ids)):
            raise ValueError("clip_id must be unique within a revision")
        return self


class Timeline(BaseModel):
    """一条剪辑时间线：稳定 ID、可改显示名与追加式修订列表。"""

    model_config = ConfigDict(extra="forbid")

    timeline_id: str
    episode: int = Field(ge=1)
    name: str = Field(min_length=1)
    revisions: list[Revision] = Field(min_length=1)

    @property
    def latest(self) -> Revision:
        return self.revisions[-1]
