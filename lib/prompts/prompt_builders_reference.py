"""参考生视频 Prompt 构建器。

设计原则与 prompt_builders_script.py 一致：
- 不重复 schema 已声明的枚举（type 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据；让模型按画面内容自行决定。
- 字段说明给指导和示例，不堆"必须 / 禁止"清单。
- 跨 backend 时长 / references 上限通过参数显式注入，不在文本里硬编码秒数。

两级 prompt 经内置整段模版渲染，引用语法规范是两者共用的同一个共享片段；资产外观、大纲、
档位与单元列表由本模块投影为槽位值。
"""

from __future__ import annotations

from lib.infra.text_metrics import reading_unit_noun
from lib.prompts.h3_guide_preset import DEFAULT_H3_GUIDE_PRESET
from lib.prompts.prompt_builders_script import (
    _neutralize_tags,
    _outline_slot,
    _overview_slot,
    _project_asset_appearances,
)
from lib.prompts.prompt_rules.asset_appearance import asset_reference_names
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.speech.dub_settings import character_dub_gender
from lib.speech.speech_rate import speech_rate_units_per_second


def _candidate_names(characters: dict | None, scenes: dict | None, props: dict | None) -> dict:
    return {
        "character_names": asset_reference_names("character", characters),
        "scene_names": asset_reference_names("scene", scenes),
        "prop_names": asset_reference_names("prop", props),
    }


def _join_durations(durations: list[int]) -> str:
    return ", ".join(str(d) for d in durations)


def build_reference_units_split_prompt(
    *,
    novel_text: str,
    project_overview: dict,
    characters: dict,
    scenes: dict,
    props: dict,
    supported_durations: list[int],
    reference_supported_durations: list[int] | None = None,
    text_supported_durations: list[int] | None = None,
    max_duration: int,
    max_reference_images: int | None,
    default_duration: int | None,
    episode: int,
    target_language: str = "中文",
    source_language: str | None = None,
    speech_rate_override: float | None = None,
    episode_target_duration: int | None = None,
    episode_outline: dict | None = None,
    next_episode_outline: dict | None = None,
    dub_target_language: str | None = None,
    narrator_gender: str = "male",
    instructions: str | None = None,
) -> str:
    """Step-1 video_unit 拆分 prompt：源文 → 扁平 unit 表（时长 + 原文锚 + 引用语法正文）。

    由 ``generate_script_plan`` 的参考生视频变体消费。script_plan 定的是**结构与内容契约**——
    unit 边界、时长（即计费单位）、台词落位、核心资产指认；提示词编写（景别 / 构图 / 运镜）
    留给 prompt_authoring。产出受 response_schema（``build_reference_units_script_plan_model``，unit 时长
    枚举硬约束）约束；unit_id / utterances 全部机器派生，不进 LLM 输出。

    Args:
        supported_durations: unit 允许的时长取值集合（秒），即两种引用状态下档位的并集，
            与 response_schema 的枚举同集合。
        reference_supported_durations: 带 ``@`` 引用的 unit 适用的档位。
        text_supported_durations: 不带 ``@`` 引用的 unit 适用的档位。两套档位相同（或任一为
            None）时不写入该联动约束——多数型号不声明「参考图↔时长」，多写一条无效约束只挤占
            注意力；两套不同时**两套都写**，不假定谁包含谁。
        max_duration: 单次视频生成的时长上限（秒），即档位最大值。
        max_reference_images: 单 unit 参考图上限；None 时不写入硬性数量约束。
        default_duration: 用户项目偏好的默认秒数；须为 supported_durations 成员或 None。
        source_language: 项目源文语言码（zh / en / vi 或 None），供台词口播时长下界取语速。
        speech_rate_override: 项目级语速覆盖（阅读单位 / 秒，由调用方经
            ``project_speech_rate_override`` 解析）；None 即无覆盖、回退语言默认。
        episode_target_duration: 项目级「单集目标时长」偏好（秒，由调用方经
            ``project_episode_target_duration`` 解析）。设了目标时打包效率按该目标组织，
            未设（None）时按单次生成上限组织，与不设该项的项目行为一致。
        episode_outline / next_episode_outline: 分集账本大纲（``episode_outline_context``
            的返回值），用于约束本集内容边界；为 None 时不插入该段。
        dub_target_language: 项目启用的配音目标语言（目前仅 ``vi``）；None 时不渲染配音分节，
            提示词与不支持配音的项目逐字相同。
        narrator_gender: 画外音配音性别（male / female），仅启用配音时使用。
        instructions: 附加指令正文；空 / None 时不渲染该分节。
    """
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")
    normalized_reference_durations = sorted({int(d) for d in reference_supported_durations or []})
    normalized_text_durations = sorted({int(d) for d in text_supported_durations or []})
    for name, tier in (
        ("reference_supported_durations", normalized_reference_durations),
        ("text_supported_durations", normalized_text_durations),
    ):
        if not set(tier) <= set(normalized_durations):
            raise ValueError(f"{name}={tier} 不是 supported_durations={normalized_durations} 的子集")

    # 「参考图↔时长」联动约束只在型号真的声明它、且两套档位不同时才写进 prompt：多数型号两者
    # 等价，多写一条无效约束只会挤占模型注意力。两套不同时两套都写全，不写成「并集 + 带图收窄」
    # ——两桶可以使用不同模型，带图那套也可能更宽，此时无引用 unit 才是被收窄的一方；只讲
    # 带图会让它照并集取到自己申请不到的档位。
    # 约束的落地判定在工具侧按机械派生的 references 逐 unit 做，prompt 这段是教学，不是唯一防线。
    duration_tiers = None
    if (
        normalized_reference_durations
        and normalized_text_durations
        and normalized_reference_durations != normalized_text_durations
    ):
        # 默认偏好只是第 3 优先级、在第 1 条硬约束内做优化，但两套档位不同时它可能只对其中一种
        # 引用状态合法（Veo 3.1 在 720p 下带图仅 8s，而项目默认可能是 4s）。此时点明它的适用范围，
        # 免得模型把「默认 4 秒」套到带引用的 unit 上、拆出执行期申请不到的时长。
        in_reference = default_duration is not None and int(default_duration) in normalized_reference_durations
        in_text = default_duration is not None and int(default_duration) in normalized_text_durations
        duration_tiers = {
            "with_references": _join_durations(normalized_reference_durations),
            "without_references": _join_durations(normalized_text_durations),
            "default_with_references_only": in_reference and not in_text,
            "default_without_references_only": in_text and not in_reference,
        }
    # 语速从 lib.speech.speech_rate 单一真相源取（项目级覆盖优先、否则按 source_language 的语言默认）、
    # 不写死；与工具侧的台词超载后校验同一套换算，prompt 给的下界和校验器判的上界因此是同一把尺。
    source_language = source_language if isinstance(source_language, str) else None
    speech_rate = speech_rate_units_per_second(source_language, speech_rate_override)

    dub = None
    if dub_target_language == "vi":
        dub = {
            "speech_rate": f"{speech_rate_units_per_second('vi'):g}",
            "narrator_gender": narrator_gender if narrator_gender in ("male", "female") else "male",
            "characters": [
                {"name": _neutralize_tags(str(name)), "gender": character_dub_gender(raw)}
                for name, raw in (characters or {}).items()
            ],
        }

    return builtin_templates.render(
        "text/reference_video_script_plan",
        target_language=target_language,
        project_overview=_overview_slot(project_overview),
        assets=_project_asset_appearances(characters, scenes, props),
        **_candidate_names(characters, scenes, props),
        novel_text=novel_text,
        episode_outline=_outline_slot(episode_outline),
        next_episode_outline=_outline_slot(next_episode_outline),
        episode=episode,
        durations=_join_durations(normalized_durations),
        duration_tiers=duration_tiers,
        default_duration=default_duration,
        speech_rate=f"{speech_rate:g}",
        speech_unit=reading_unit_noun(source_language),
        max_duration=max_duration,
        max_reference_images=max_reference_images,
        episode_target_duration=episode_target_duration,
        dub=dub,
        instructions=instructions,
    )


def build_reference_dub_lines_prompt(
    *,
    units: list[dict],
    characters: dict,
    narrator_gender: str = "male",
    synopsis: str | None = None,
) -> str:
    """为已有 unit 回填越南语配音台词的 prompt（TRANSLATE_DUB，单次最多一批 unit）。

    ``units`` 每项为 ``{"unit_id", "duration_seconds", "text", "utterances": [{"index", "speaker", "text"}]}``，
    ``utterances`` 是 unit 正文派生出的台词 / 画外音序列（``speaker`` 为 None 即画外音）。
    只翻译与配音，不改写正文；性别取角色登记值，未登记时由模型依称谓与语境判断。
    """
    narrator = narrator_gender if narrator_gender in ("male", "female") else "male"
    speech_rate = f"{speech_rate_units_per_second('vi'):g}"
    lines: list[str] = [
        "你是一位影视配音译制编剧。下面是若干视频单元（unit）里按顺序出现的台词与画外音，请为每一条发声写一行越南语配音台词。",
        "",
        "规则：",
        "- 每个 unit 的 `dub_lines` 与它的发声逐条一一对应：`index` 即发声序号（从 0 起），行数与发声条数相等；没有发声的 unit 给空数组。",
        "- `speaker`：台词填说话角色名（与输入一致）；画外音填 null。",
        f"- `gender`：台词按下方角色表的性别取值，表中未登记时依称谓、语气与上下文判断；画外音一律取 `{narrator}`。",
        "- `text_vi`：译自对应发声原文，忠实原意、口语自然；称谓与人称代词（anh / em / chị / tôi / tao / mày / cậu ...）"
        "须符合说话人与听者的性别、年龄与关系；不加原文没有的内容，不夹带中文或括号注释。",
        f"- 篇幅预算：越南语口播语速约 {speech_rate} 词/秒，一个 unit 里全部 `text_vi` 的总词数不得超过"
        f"「unit 时长（秒）× {speech_rate}」，宁短勿长，译文偏长时精简措辞。",
        "- `unit_id` 原样回填，不得增删 unit。",
    ]
    if synopsis and synopsis.strip():
        lines += ["", "故事梗概：", synopsis.strip()]
    lines += ["", "角色表："]
    if characters:
        for name, raw in characters.items():
            gender = character_dub_gender(raw) or "未登记"
            description = raw.get("description", "") if isinstance(raw, dict) else ""
            description = " ".join(str(description).split())[:200]
            lines.append(
                f"- {_neutralize_tags(str(name))}（{gender}）：{_neutralize_tags(description)}".rstrip("：").rstrip()
            )
    else:
        lines.append("（暂无）")
    lines += ["", "待配音的 unit："]
    for unit in units:
        duration = int(unit.get("duration_seconds") or 0)
        lines += [
            "",
            f"## {unit['unit_id']}（时长 {duration} 秒，词数预算约 {max(1, int(duration * speech_rate_units_per_second('vi')))}）",
        ]
        utterances = unit.get("utterances") or []
        if not utterances:
            lines.append("（无台词与画外音）")
        for item in utterances:
            speaker = item.get("speaker")
            label = f"{speaker}" if speaker else "画外音"
            lines.append(f"- [{item['index']}] {_neutralize_tags(str(label))}：{item['text']}")
    return "\n".join(lines)


def render_reference_units_for_prompt_authoring(units: list[dict]) -> str:
    """把 script_plan units 渲染为 prompt_authoring prompt 的输入文本。

    机械渲染、无 LLM 参与：按 script_plan 的落盘顺序逐 unit 输出序号 + 时长 + 正文。
    prompt_authoring 以此为唯一基底做视觉扩写（见 ADR 0041）；``unit_id`` 不进渲染——它由序号机械
    派生，prompt_authoring 不写 id 就没有 id 漂移可校验。
    """
    blocks: list[str] = []
    for index, unit in enumerate(units, start=1):
        duration = int(unit.get("duration_seconds") or 0)
        body = str(unit.get("text") or "")
        blocks.append(f"#### unit {index}（时长 {duration}s）\n{body}")
    return "\n\n".join(blocks)


def build_reference_video_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    script_plan_units: list[dict],
    max_refs: int | None,
    episode: int,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """构建参考生视频 prompt_authoring（提示词编写）的 LLM Prompt。

    prompt_authoring 只做一件事：把 script_plan 每个 unit 的正文按同一份书写语法扩写出视觉层，**保结构**——
    unit 数与顺序不变、台词逐字不变；时长不进输出（script_plan 定稿、机械沿用）。

    Args:
        project_overview: 项目概述（synopsis, genre, theme, world_setting）。
        style / style_description: 视觉风格标签与描述。
        characters / scenes / props: 三类已注册资产字典（用于候选列表）。
        script_plan_units: 结构化 script_plan units（``script_plan_reference_units.json`` 经校验后的 dict 列表），
            由 ``render_reference_units_for_prompt_authoring`` 机械渲染进 prompt。
        max_refs: 当前视频模型支持的最大参考图数；为 None 时不写入硬性数量约束。
        instructions: 附加指令正文；空 / None 时不渲染该分节。
    """
    return builtin_templates.render(
        "text/reference_video_prompt_authoring",
        target_language=target_language,
        project_overview=_overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        assets=_project_asset_appearances(characters, scenes, props),
        **_candidate_names(characters, scenes, props),
        units_content=render_reference_units_for_prompt_authoring(script_plan_units),
        episode=episode,
        max_refs=max_refs,
        instructions=instructions,
    )


def build_reference_video_h3_ref2va_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    script_plan_units: list[dict],
    max_refs: int | None,
    episode: int,
    audio_reference_names: list[str],
    h3_guide_preset: str = DEFAULT_H3_GUIDE_PRESET,
    h3_guide_custom: str | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """构建 H3 Ref2VA 提示词档下参考生视频 prompt_authoring 的 LLM Prompt。

    与 :func:`build_reference_video_prompt` 同一套输入，产出多一份六段英文载荷；
    ``audio_reference_names`` 是已挂参考音频、允许用 ``<Audio @[名称]>`` 引用的角色名，为空时模版不使用音频标签；
    ``h3_guide_preset`` 选定 ``detailed_description`` 的写作指南预设；``h3_guide_custom`` 非空时整段替换预设的内置指南。
    """
    return builtin_templates.render(
        "text/reference_video_prompt_authoring_h3_ref2va",
        target_language=target_language,
        project_overview=_overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        assets=_project_asset_appearances(characters, scenes, props),
        **_candidate_names(characters, scenes, props),
        audio_reference_names=audio_reference_names,
        h3_guide_preset=h3_guide_preset,
        h3_guide_custom=h3_guide_custom,
        units_content=render_reference_units_for_prompt_authoring(script_plan_units),
        episode=episode,
        max_refs=max_refs,
        instructions=instructions,
    )
