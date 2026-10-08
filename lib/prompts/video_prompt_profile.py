"""参考生视频提示词档：项目级设置，决定单元的视频提示词按哪套模版编写与渲染。

``default`` 走通用三段论渲染（单元正文 + 主体绑定 + 风格与负向约束）；``h3_ref2va`` 让提示词编写
产出 MiniMax H3 全参考六段结构，生成时把其中的 ``@[名称]`` 解析成参考图 / 参考音频标签后直接下发。

本模块是叶子模块（不依赖 lib 内任何其它模块），供 lib 各层与 server 侧共同引用。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast, get_args

#: 参考生视频提示词档。前端 ``VideoPromptProfile`` 与之一一对应，取值增减须两侧同步。
VideoPromptProfile = Literal["default", "h3_ref2va"]

DEFAULT_VIDEO_PROMPT_PROFILE: VideoPromptProfile = "default"

VALID_VIDEO_PROMPT_PROFILES: frozenset[str] = frozenset(get_args(VideoPromptProfile))

PROJECT_FIELD = "video_prompt_profile"

#: H3 六段提示词的书写语言：缺省英文（H3 指南的默认写法）；``zh`` 让六段（含台词）用中文书写。
H3_PROMPT_LANGUAGE_FIELD = "h3_prompt_language"
H3PromptLanguage = Literal["en", "zh"]
VALID_H3_PROMPT_LANGUAGES: frozenset[str] = frozenset(get_args(H3PromptLanguage))

#: 该提示词档只对参考生视频路线成立，且不覆盖广告 / 短片（其提示词编写走独立的构建器）。
H3_REF2VA_CONTENT_MODES: frozenset[str] = frozenset({"drama", "narration"})
H3_REF2VA_GENERATION_MODE = "reference_video"


def video_prompt_profile(project: Mapping[str, object] | None) -> VideoPromptProfile:
    """项目声明的提示词档；缺省与非法值一律按默认档。

    project.json 是明文文件，字段被手编成非法值时按默认档解读：降级只会少用一套定制模版，
    比按 H3 档解读一个读不懂的值安全。
    """
    if project is None:
        return DEFAULT_VIDEO_PROMPT_PROFILE
    raw = project.get(PROJECT_FIELD)
    if isinstance(raw, str) and raw in VALID_VIDEO_PROMPT_PROFILES:
        return cast(VideoPromptProfile, raw)
    return DEFAULT_VIDEO_PROMPT_PROFILE


def h3_prompt_language(project: Mapping[str, object] | None) -> H3PromptLanguage:
    """项目声明的 H3 六段书写语言；缺省与非法值一律按英文。"""
    raw = project.get(H3_PROMPT_LANGUAGE_FIELD) if project is not None else None
    return "zh" if raw == "zh" else "en"


def h3_ref2va_supported(project: Mapping[str, object]) -> bool:
    """项目的模式组合是否支持 H3 全参考提示词档：参考生视频路线的剧情 / 解说项目。"""
    return (
        project.get("generation_mode") == H3_REF2VA_GENERATION_MODE
        and project.get("content_mode") in H3_REF2VA_CONTENT_MODES
    )


def is_h3_ref2va(project: Mapping[str, object] | None) -> bool:
    """项目是否启用 H3 全参考提示词档，并且模式组合支持该档。"""
    if project is None or video_prompt_profile(project) != "h3_ref2va":
        return False
    return h3_ref2va_supported(project)


__all__ = [
    "DEFAULT_VIDEO_PROMPT_PROFILE",
    "H3_PROMPT_LANGUAGE_FIELD",
    "H3_REF2VA_CONTENT_MODES",
    "H3_REF2VA_GENERATION_MODE",
    "PROJECT_FIELD",
    "VALID_H3_PROMPT_LANGUAGES",
    "VALID_VIDEO_PROMPT_PROFILES",
    "VideoPromptProfile",
    "h3_prompt_language",
    "h3_ref2va_supported",
    "is_h3_ref2va",
    "video_prompt_profile",
]
