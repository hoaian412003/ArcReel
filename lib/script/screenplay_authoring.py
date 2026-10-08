"""编剧提交：把编剧 Agent 提交的「场 + 有类型的镜头节拍」编译成存盘的剧本，并做机械质量检查。

Agent 自己写剧本，提交时用有类型的节拍（动作 / 对白 / 画外音）而不是自由 Markdown：存盘格式
（``ScreenplayScene.text`` 的 Markdown：``**人物** (神态): 台词``、``（画外音）…``）由本模块确定性渲染，
因此对白格式不会随模型漂移；每场预计时长由台词字数按口播语速加动作时长算出，不取自模型。

检查分两档：``error`` 拒绝落盘（结构不成立或必然出错的内容），``warning`` 照常落盘并回报，
由编剧按质量要求决定是否修改后重新提交。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from lib.infra.text_metrics import count_reading_units
from lib.script.screenplay import MAX_SCENE_SECONDS, MAX_SCENES, MIN_SCENE_SECONDS, ScreenplayDraft, ScreenplayScene
from lib.speech.speech_rate import speech_rate_units_per_second

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

#: 台词里每一句之间的停顿（秒）；估算时长时与念白时间相加。
LINE_PAUSE_SECONDS = 0.5

#: 单句台词的建议上限（阅读单位）：中文按字、越南语 / 英语按词。超出为 warning。
SOFT_LINE_UNITS = {"zh": 22}
DEFAULT_SOFT_LINE_UNITS = 15
#: 单句台词的硬上限：超出必然是把叙述写成了台词，拒绝。
HARD_LINE_UNITS = {"zh": 60}
DEFAULT_HARD_LINE_UNITS = 35

#: 目标时长的容差；偏离超过它给 warning。
DURATION_TOLERANCE = 0.15
#: 开场场次超过这个时长且前几拍没有人物对白，视为冷开场不够抓人。
COLD_OPEN_MAX_SECONDS = 30
COLD_OPEN_BEATS = 3
#: 单场对白条数建议上限。
SOFT_DIALOGUE_LINES_PER_SCENE = 8

_CJK = re.compile(r"[一-鿿]")

BeatKind = Literal["action", "dialogue", "voice_over"]


class Beat(BaseModel):
    """一个镜头节拍：动作 / 环境、一句对白，或一句画外音。"""

    model_config = ConfigDict(extra="forbid")

    kind: BeatKind = Field(description="action 动作或环境；dialogue 一句对白；voice_over 一句画外音")
    text: _Text = Field(description="动作描写，或这一句台词 / 画外音的原话（不含人物名与括注）")
    speaker: str | None = Field(default=None, description="dialogue 必填：登记的人物名；衍生写作「本体/衍生」")
    tone: str | None = Field(default=None, description="dialogue 可选：神态或语气，几个字")
    seconds: int | None = Field(
        default=None, ge=1, le=60, description="action 必填：看清这个动作需要的秒数；对白与画外音不填（按字数算）"
    )

    @model_validator(mode="after")
    def _check_kind(self) -> Beat:
        if self.kind == "dialogue" and not (self.speaker and self.speaker.strip()):
            raise ValueError("dialogue 节拍必须给 speaker")
        if self.kind != "dialogue" and (self.speaker or self.tone):
            raise ValueError(f"{self.kind} 节拍不能带 speaker / tone")
        if self.kind == "action" and self.seconds is None:
            raise ValueError("action 节拍必须给 seconds")
        if self.kind != "action" and self.seconds is not None:
            raise ValueError(f"{self.kind} 节拍的时长按字数算，不要给 seconds")
        return self


class SubmittedScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: _Text = Field(description="场景标题：内景/外景 · 地点 · 时间")
    summary: _Text = Field(description="这一场发生了什么、为什么重要，一到两句")
    beats: list[Beat] = Field(min_length=1, description="按发生顺序排列的节拍")


class ScreenplaySubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: _Text = Field(description="本集标题，简短有记忆点")
    logline: _Text = Field(description="本集看点：核心冲突、观众期待与情绪走向，一到两句")
    scenes: list[SubmittedScene] = Field(
        min_length=1, max_length=MAX_SCENES, description="按叙事顺序排列的场次；常见 3–8 场，不为凑场次而切碎"
    )


@dataclass(frozen=True)
class Gate:
    """一条检查结果：``severity`` 为 error 时拒绝落盘。"""

    severity: Literal["error", "warning"]
    code: str
    message: str


@dataclass(frozen=True)
class CompiledScreenplay:
    draft: ScreenplayDraft
    gates: list[Gate]
    total_seconds: int

    @property
    def errors(self) -> list[Gate]:
        return [gate for gate in self.gates if gate.severity == "error"]

    @property
    def warnings(self) -> list[Gate]:
        return [gate for gate in self.gates if gate.severity == "warning"]


def _render_beat(beat: Beat) -> str:
    if beat.kind == "dialogue":
        tone = f" ({beat.tone.strip()})" if beat.tone and beat.tone.strip() else ""
        return f"**{(beat.speaker or '').strip()}**{tone}: {beat.text}"
    if beat.kind == "voice_over":
        return f"（画外音）{beat.text}"
    return beat.text


def _scene_seconds(scene: SubmittedScene, *, language: str | None, rate: float) -> int:
    spoken = 0.0
    action = 0
    for beat in scene.beats:
        if beat.kind == "action":
            action += beat.seconds or 0
        else:
            spoken += count_reading_units(beat.text, language) / rate + LINE_PAUSE_SECONDS
    return max(MIN_SCENE_SECONDS, min(MAX_SCENE_SECONDS, round(spoken + action)))


def _limits(table: Mapping[str, int], default: int, language: str | None) -> int:
    return table.get((language or "").strip().lower(), default)


def compile_submission(
    submission: ScreenplaySubmission,
    *,
    language: str | None,
    speech_rate_override: float | None = None,
    character_names: Sequence[str] = (),
    target_seconds: int | None = None,
) -> CompiledScreenplay:
    """渲染存盘剧本并检查。``error`` 非空时不应落盘。"""
    rate = speech_rate_units_per_second(language, speech_rate_override)
    soft_line = _limits(SOFT_LINE_UNITS, DEFAULT_SOFT_LINE_UNITS, language)
    hard_line = _limits(HARD_LINE_UNITS, DEFAULT_HARD_LINE_UNITS, language)
    known = set(character_names)
    gates: list[Gate] = []

    scenes: list[ScreenplayScene] = []
    long_lines: list[str] = []
    unknown: set[str] = set()
    spoken_beats = 0
    for index, scene in enumerate(submission.scenes, start=1):
        for beat in scene.beats:
            if beat.kind == "action":
                continue
            spoken_beats += 1
            units = count_reading_units(beat.text, language)
            if units > hard_line:
                gates.append(
                    Gate(
                        "error",
                        "line_too_long",
                        f"第 {index} 场有一句{units}个单位的台词，超出硬上限 {hard_line}：{beat.text[:30]}…",
                    )
                )
            elif units > soft_line:
                long_lines.append(f"第 {index} 场（{units}）")
            if beat.kind == "dialogue" and known:
                base = (beat.speaker or "").split("/")[0].strip()
                if base not in known:
                    unknown.add(base)
        scenes.append(
            ScreenplayScene(
                heading=scene.heading,
                summary=scene.summary,
                estimated_seconds=_scene_seconds(scene, language=language, rate=rate),
                text="\n\n".join(_render_beat(beat) for beat in scene.beats),
            )
        )
    total = sum(scene.estimated_seconds for scene in scenes)

    if spoken_beats == 0:
        gates.append(Gate("error", "no_spoken_beats", "全集没有任何对白或画外音：短剧需要台词推进剧情"))
    if (language or "").strip().lower() not in ("zh", "") and _CJK.search(
        " ".join([submission.title, submission.logline, *(b.text for s in submission.scenes for b in s.beats)])
    ):
        gates.append(
            Gate("error", "wrong_language", "出现中文字符：所有内容必须使用项目源语言书写（人名保持原文写法）")
        )

    if long_lines:
        gates.append(
            Gate(
                "warning",
                "long_lines",
                f"有台词超过 {soft_line} 个单位：{'、'.join(long_lines[:8])}，改成口语短句或拆成两句",
            )
        )
    if unknown:
        gates.append(
            Gate(
                "warning",
                "unregistered_speakers",
                f"说话人不在登记角色里：{'、'.join(sorted(unknown))}（泛指群演可保留，具名角色请用登记名）",
            )
        )
    if target_seconds:
        deviation = (total - target_seconds) / target_seconds
        if abs(deviation) > DURATION_TOLERANCE:
            gates.append(
                Gate(
                    "warning",
                    "duration_off_target",
                    f"预计总时长 {total} 秒，偏离目标 {target_seconds} 秒 {round(deviation * 100)}%（容差 ±{round(DURATION_TOLERANCE * 100)}%）："
                    + ("补足有戏的情节，不要注水" if deviation < 0 else "压缩铺垫与重复信息，不要删必要情节"),
                )
            )
    first = scenes[0]
    opening = submission.scenes[0].beats[:COLD_OPEN_BEATS]
    if first.estimated_seconds > COLD_OPEN_MAX_SECONDS and not any(beat.kind == "dialogue" for beat in opening):
        gates.append(
            Gate(
                "warning",
                "cold_open",
                f"开场一场 {first.estimated_seconds} 秒，前 {COLD_OPEN_BEATS} 拍内没有人物对白：开场要立刻出现人物与矛盾",
            )
        )
    flat = [i for i, s in enumerate(submission.scenes, 1) if all(b.kind != "action" for b in s.beats)]
    if flat:
        gates.append(
            Gate(
                "warning",
                "no_action_scene",
                f"第 {', '.join(map(str, flat))} 场没有任何动作节拍：每场要有可被镜头拍到的动作",
            )
        )
    crowded = [
        i
        for i, s in enumerate(submission.scenes, 1)
        if sum(1 for b in s.beats if b.kind == "dialogue") > SOFT_DIALOGUE_LINES_PER_SCENE
    ]
    if crowded:
        gates.append(
            Gate(
                "warning",
                "dialogue_heavy",
                f"第 {', '.join(map(str, crowded))} 场对白超过 {SOFT_DIALOGUE_LINES_PER_SCENE} 句：精简或拆场",
            )
        )
    last = submission.scenes[-1].beats[-1]
    if last.kind == "voice_over":
        gates.append(
            Gate(
                "warning",
                "weak_ending",
                "集尾停在画外音上：结尾应停在具体的动作、一句话或出现的人 / 物上，给观众继续看的理由",
            )
        )

    draft = ScreenplayDraft(title=submission.title, logline=submission.logline, scenes=scenes)
    return CompiledScreenplay(draft=draft, gates=gates, total_seconds=total)


__all__ = [
    "Beat",
    "CompiledScreenplay",
    "Gate",
    "ScreenplaySubmission",
    "SubmittedScene",
    "compile_submission",
]
