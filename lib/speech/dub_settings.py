"""项目级配音设置的字段名与读时解析（project.json 顶层字段）。

只承载「是否配音、配成什么语言、画外音用什么性别」这几个项目事实，不依赖任何 script 子包，
供提示词投影、产物 basis、校验器与路由共用。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

#: project.json 顶层字段：配音目标语言（缺省不配音；目前仅支持 ``vi``）。
DUB_TARGET_LANGUAGE_FIELD = "dub_target_language"
#: project.json 顶层字段：画外音（旁白）配音的性别，缺省 male。
NARRATOR_GENDER_FIELD = "narrator_gender"
#: project.json 顶层字段：按性别指定的配音音色 id（缺省回落到音色目录里该性别的第一个音色）。
DUB_VOICE_FIELDS: dict[str, str] = {"male": "dub_voice_male", "female": "dub_voice_female"}
#: character 资产上的配音性别字段。
CHARACTER_GENDER_FIELD = "gender"

SUPPORTED_DUB_LANGUAGES: tuple[str, ...] = ("vi",)
VALID_DUB_GENDERS: tuple[str, ...] = ("male", "female")
DubGenderValue = Literal["male", "female"]
DEFAULT_NARRATOR_GENDER: DubGenderValue = "male"


def project_dub_target_language(project: Mapping[str, Any] | None) -> str | None:
    """项目启用的配音目标语言；未设置 / 脏值一律 ``None``（不配音）。"""
    if not isinstance(project, Mapping):
        return None
    raw = project.get(DUB_TARGET_LANGUAGE_FIELD)
    return raw if isinstance(raw, str) and raw in SUPPORTED_DUB_LANGUAGES else None


def project_narrator_gender(project: Mapping[str, Any] | None) -> DubGenderValue:
    """画外音配音性别；未设置 / 脏值回退 male。"""
    raw = project.get(NARRATOR_GENDER_FIELD) if isinstance(project, Mapping) else None
    return "female" if raw == "female" else "male"


def character_dub_gender(character: object) -> DubGenderValue | None:
    """角色资产登记的配音性别；未登记 / 脏值为 ``None``。"""
    raw = character.get(CHARACTER_GENDER_FIELD) if isinstance(character, Mapping) else None
    if raw == "male":
        return "male"
    if raw == "female":
        return "female"
    return None


def project_dub_voice(project: Mapping[str, Any] | None, gender: str) -> str | None:
    """项目为某性别指定的配音音色 id；未指定 / 脏值为 ``None``。"""
    field = DUB_VOICE_FIELDS.get(gender)
    raw = project.get(field) if field and isinstance(project, Mapping) else None
    return raw.strip() if isinstance(raw, str) and raw.strip() else None
