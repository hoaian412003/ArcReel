"""H3 Ref2VA 提示词写作指南预设：决定提示词编写时 ``h3_prompt`` 的细节与篇幅取向。

预设只调整写作风格（镜头密度、细节取向、篇幅），不改变六段结构与机械校验：占位写法、台词进 ``<d>``、
英文正文、分镜时间戳等硬约束固定在模版里，由 ``validate_h3_prompt`` 强制。

取值按「项目 > 全局设置 > 缺省」解析；任一层读不懂的值都视为未设置，落到下一层。

本模块是叶子模块（不依赖 lib 内任何其它模块），供 lib 各层与 server 侧共同引用。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast, get_args

#: H3 写作指南预设。前端 ``H3GuidePreset`` 与之一一对应，取值增减须两侧同步，并新增同名的指南片段
#: ``partials/shared/h3_guide/<取值>.md``。
H3GuidePreset = Literal["standard", "dialogue", "action"]

DEFAULT_H3_GUIDE_PRESET: H3GuidePreset = "standard"

VALID_H3_GUIDE_PRESETS: frozenset[str] = frozenset(get_args(H3GuidePreset))

#: project.json 顶层字段；缺省即继承全局设置。
PROJECT_FIELD = "h3_guide_preset"

#: 全局 setting 键；缺省即 ``standard``。
SETTING_KEY = "h3_guide_preset"

#: 自定义指南正文：替换所选预设的内置指南片段。project.json 顶层字段与全局 setting 键同名。
CUSTOM_PROJECT_FIELD = "h3_guide_custom"
CUSTOM_SETTING_KEY = "h3_guide_custom"

#: 自定义指南正文的字符上限；它整段进入提示词，过长只会挤占单元内容。
MAX_CUSTOM_GUIDE_CHARS = 4000


def parse_h3_guide_preset(raw: object) -> H3GuidePreset | None:
    """把任意存量值读成预设；缺失、空白或未知取值返回 None。"""
    if isinstance(raw, str):
        value = raw.strip()
        if value in VALID_H3_GUIDE_PRESETS:
            return cast(H3GuidePreset, value)
    return None


def resolve_h3_guide_preset(project: Mapping[str, object] | None, global_value: object = None) -> H3GuidePreset:
    """项目字段 > 全局设置 > 缺省预设。"""
    if project is not None:
        override = parse_h3_guide_preset(project.get(PROJECT_FIELD))
        if override is not None:
            return override
    return parse_h3_guide_preset(global_value) or DEFAULT_H3_GUIDE_PRESET


def parse_h3_guide_custom(raw: object) -> str | None:
    """把任意存量值读成自定义指南正文；缺失、空白、非字符串或超长返回 None。"""
    if isinstance(raw, str):
        value = raw.strip()
        if value and len(value) <= MAX_CUSTOM_GUIDE_CHARS:
            return value
    return None


def resolve_h3_guide_custom(project: Mapping[str, object] | None, global_value: object = None) -> str | None:
    """项目自定义指南 > 全局自定义指南；都没有时用所选预设的内置指南。"""
    if project is not None:
        override = parse_h3_guide_custom(project.get(CUSTOM_PROJECT_FIELD))
        if override is not None:
            return override
    return parse_h3_guide_custom(global_value)


__all__ = [
    "CUSTOM_PROJECT_FIELD",
    "CUSTOM_SETTING_KEY",
    "DEFAULT_H3_GUIDE_PRESET",
    "MAX_CUSTOM_GUIDE_CHARS",
    "PROJECT_FIELD",
    "SETTING_KEY",
    "VALID_H3_GUIDE_PRESETS",
    "H3GuidePreset",
    "parse_h3_guide_custom",
    "parse_h3_guide_preset",
    "resolve_h3_guide_custom",
    "resolve_h3_guide_preset",
]
