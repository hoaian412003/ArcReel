"""H3 Ref2VA 提示词编写模版的运行期替换：全局设置与项目字段各可存一份完整模版正文。

替换的是模版正文；槽位、轴与锁定片段沿用内置模版，保存前经 ``PromptTemplates.check_body`` 与示例渲染校验。
取值按「项目 > 全局设置 > 内置模版」解析；读不懂的值视为未设置。

本模块是叶子模块（不依赖 lib 内任何其它模块），供 lib 各层与 server 侧共同引用。
"""

from __future__ import annotations

from collections.abc import Mapping

#: 可被替换的内置模版 id。
TEMPLATE_ID = "text/reference_video_prompt_authoring_h3_ref2va"

#: project.json 顶层字段与全局 setting 键同名。
PROJECT_FIELD = "h3_authoring_template"
SETTING_KEY = "h3_authoring_template"

#: 替换正文的字符上限；内置正文约 1 万字符，留出充足余量。
MAX_TEMPLATE_CHARS = 40000


def parse_h3_authoring_template(raw: object) -> str | None:
    """把任意存量值读成替换正文；缺失、空白、非字符串或超长返回 None。"""
    if isinstance(raw, str) and raw.strip() and len(raw) <= MAX_TEMPLATE_CHARS:
        return raw
    return None


def resolve_h3_authoring_template(project: Mapping[str, object] | None, global_value: object = None) -> str | None:
    """项目替换正文 > 全局替换正文；都没有时用内置模版。"""
    if project is not None:
        override = parse_h3_authoring_template(project.get(PROJECT_FIELD))
        if override is not None:
            return override
    return parse_h3_authoring_template(global_value)


__all__ = [
    "MAX_TEMPLATE_CHARS",
    "PROJECT_FIELD",
    "SETTING_KEY",
    "TEMPLATE_ID",
    "parse_h3_authoring_template",
    "resolve_h3_authoring_template",
]
