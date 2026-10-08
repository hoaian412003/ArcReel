"""剧本生成 Prompt 构建器（drama / narration 两种 content_mode）。

设计原则：
- 不重复 schema 已声明的枚举（shot_type / camera_motion 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据，避免把人的镜头审美灌给 LLM；
  让模型按画面内容自行决定。
- 不写无法被 LLM 自检的字数硬限制（"≤200 字"）；用示例隐性表达节奏。
- 字段说明用少量正例与带解说的反例传达要求，不堆"必须 / 禁止"清单。
- 提示词编写与旁白切分经内置整段模版渲染；资产外观与只读内容保持代码投影。
"""

from lib.infra.text_metrics import reading_unit_noun
from lib.prompts.prompt_rules.asset_appearance import asset_reference_names, iter_asset_appearances
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.speech.speech_rate import speech_rate_units_per_second


def format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """生成时长约束描述。连续整数集 ≥5 用区间表达，否则枚举。"""
    if not supported_durations:
        raise ValueError("supported_durations 不能为空：调用方必须提供 model 的合法时长列表")

    sorted_d = sorted(set(supported_durations))
    is_continuous = len(sorted_d) >= 5 and all(sorted_d[i] == sorted_d[i - 1] + 1 for i in range(1, len(sorted_d)))
    if is_continuous:
        body = f"{sorted_d[0]} 到 {sorted_d[-1]} 秒间整数任选"
    else:
        durations_str = ", ".join(str(d) for d in sorted_d)
        body = f"从 [{durations_str}] 秒中选择"

    if default_duration is not None:
        if default_duration not in sorted_d:
            raise ValueError(
                f"default_duration={default_duration} 不在 supported_durations={sorted_d} 内，"
                "调用方必须保证默认值合法（否则 prompt 会自相矛盾）"
            )
        return f"时长：{body}，默认 {default_duration} 秒"
    return f"时长：{body}，按内容节奏自行决定"


def _format_aspect_ratio_desc(aspect_ratio: str) -> str:
    if aspect_ratio == "9:16":
        return "竖屏构图"
    if aspect_ratio == "16:9":
        return "横屏构图"
    return f"{aspect_ratio} 构图"


def _overview_slot(project_overview: dict) -> dict[str, object]:
    """项目概述投影为键齐全的槽位值，缺键传 None，模版按空渲染。"""
    return {key: project_overview.get(key) for key in ("synopsis", "genre", "theme", "world_setting")}


def _stripped_text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _outline_slot(outline: object) -> dict | None:
    """分集大纲的键齐全投影；空白与非字符串字段按缺失处理，没有任何有效内容时为 ``None``，模版不渲染该块。

    大纲是本集内容边界的既定契约，拆分时先知道本集要讲到哪里、下集从哪接，才不会把跨集
    情节吞进来或提前抖包袱。
    """
    if not isinstance(outline, dict):
        return None
    beats = outline.get("story_beats")
    projected = {
        "title": _stripped_text(outline.get("title")),
        "story_beats": [beat for beat in beats if isinstance(beat, str) and beat.strip()]
        if isinstance(beats, list)
        else [],
        "hook": _stripped_text(outline.get("hook")),
        "next_episode_teaser": _stripped_text(outline.get("next_episode_teaser")),
    }
    return projected if any(projected.values()) else None


def _screenplay_slot(screenplay: object) -> dict | None:
    """已通过剧本的键齐全投影；没有剧本（``None`` / 非对象 / 无场次）时为 ``None``，模版不渲染剧本分节。

    只取模版用到的字段：落盘文件里的审阅记录等元数据不进提示词。
    """
    if not isinstance(screenplay, dict):
        return None
    scenes = screenplay.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return None
    return {
        "title": screenplay.get("title") or "",
        "logline": screenplay.get("logline") or "",
        "scenes": [
            {key: scene.get(key) for key in ("heading", "summary", "estimated_seconds", "text")}
            for scene in scenes
            if isinstance(scene, dict)
        ],
    }


# ---------------------------------------------------------------------------
# 两段式分层文案（见 ADR 0041）：script_plan（normalize）= 内容、prompt_authoring（drama）= 视觉。
#
# 内容抽取前移到 script_plan：分镜边界、出场资产、逐字口播 utterances、原文锚 source_text、
# 视觉改编描述 scene_description 一次定稿，并按 source_kind 切「改编 / 提取」口径。
# prompt_authoring 只补视觉层（image_prompt / video_prompt），按 scene_id 透传内容、不再识别口播、
# 不分 source_kind——故 prompt_authoring 文案无 novel/screenplay 分支。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _neutralize_tags(value: str) -> str:
    """中和动态文本里的尖括号：novel_text / 资产名出现 </segments> 等标签序列时，避免打散
    标签化 prompt 的块结构。属 prompt 鲁棒性——prompt_authoring 输出仍由 response_schema 强制，无安全边界。
    """
    return value.replace("<", "＜").replace(">", "＞")


def _clean(value: object) -> str:
    return _neutralize_tags(value.strip()) if isinstance(value, str) else ""


def _format_direction_lines(direction: object) -> list[str]:
    """把条目的导演说明渲染为「导演：」行组（镜头与意图、表演、衔接）；条目没有导演说明时为空。

    动态文本过 ``_neutralize_tags``；字段缺失按空处理，渲染本身 fail-soft，结构校验在导演步骤写入时已做。
    """
    if not isinstance(direction, dict):
        return []
    raw_camera = direction.get("camera")
    camera = raw_camera if isinstance(raw_camera, dict) else {}
    shot = " / ".join(part for part in (_clean(camera.get(key)) for key in ("angle", "shot_size", "movement")) if part)
    intent = _clean(camera.get("intent"))
    head = f"导演：镜头 {shot}" if shot else "导演："
    if intent:
        head += f"（意图：{intent}）"
    lines = [head]
    raw_performance = direction.get("performance")
    notes = [
        f"{_clean(item.get('character'))}——{_clean(item.get('note'))}"
        for item in (raw_performance if isinstance(raw_performance, list) else [])
        if isinstance(item, dict) and _clean(item.get("character")) and _clean(item.get("note"))
    ]
    if notes:
        lines.append("  表演：" + "；".join(notes))
    continuity_in, continuity_out = _clean(direction.get("continuity_in")), _clean(direction.get("continuity_out"))
    if continuity_in or continuity_out:
        lines.append(f"  衔接：开场 {continuity_in or '—'} ｜ 结束 {continuity_out or '—'}")
    return lines


def _format_settled_image(image_prompt: object) -> str:
    """第二轮（视频）的输入：第一轮已定的画面，一行写清画面描述与构图。没有定稿画面时为空串。"""
    if isinstance(image_prompt, str):
        return _neutralize_tags(image_prompt.strip())
    if not isinstance(image_prompt, dict):
        return ""
    scene = _clean(image_prompt.get("scene"))
    raw_composition = image_prompt.get("composition")
    composition = raw_composition if isinstance(raw_composition, dict) else {}
    parts = [_clean(composition.get(key)) for key in ("shot_type", "lighting", "ambiance")]
    detail = "，".join(part for part in parts if part)
    return f"{scene}（{detail}）" if detail else scene


def _format_narration_script_plan_segments(script_plan_segments: list[dict]) -> str:
    """把 script_plan 结构化分镜渲染为 prompt_authoring 的只读上下文：segment_id + 内容字段 + 逐字原文。

    这些字段在 script_plan 已定、prompt_authoring 透传不重出；此处仅作为「为该分镜写好视觉层」的依据呈现。
    """
    if not script_plan_segments:
        return "（无分镜）"
    lines: list[str] = []
    for seg in script_plan_segments:
        sid = _neutralize_tags(str(seg.get("segment_id", "?")))
        dur = seg.get("duration_seconds", "?")
        brk = "，场景切换" if seg.get("segment_break") else ""
        chars = _neutralize_tags("、".join(seg.get("characters_in_segment") or []) or "无")
        scene_names = _neutralize_tags("、".join(seg.get("scenes") or []) or "无")
        prop_names = _neutralize_tags("、".join(seg.get("props") or []) or "无")
        # 多行 novel_text 续行缩进进原文块，避免 flush-left 溢出 <segments>；尖括号一并中和防注入
        novel_block = _neutralize_tags(seg.get("novel_text") or "").replace("\n", "\n  ")
        entry = f"- {sid}（时长 {dur}s{brk}）｜出场角色：{chars}｜场景：{scene_names}｜道具：{prop_names}\n  原文：{novel_block}"
        direction_lines = _format_direction_lines(seg.get("direction"))
        if direction_lines:
            entry += "\n" + "\n".join(f"  {line}" for line in direction_lines)
        if settled := _format_settled_image(seg.get("image_prompt")):
            entry += f"\n  已定画面：{settled}"
        lines.append(entry)
    return "\n".join(lines)


def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    script_plan_segments: list[dict],
    episode: int,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
    write_image: bool = True,
) -> str:
    """构建旁白/解说模式 prompt_authoring（视觉层）prompt。

    提示词编写分两轮：``write_image`` 为 True 时只写 image_prompt（第一轮），为 False 时看着分镜表里
    「已定画面」只写 video_prompt（第二轮）。

    script_plan 已定的 novel_text / 时长 / segment_break / 出场角色 / 场景 / 道具按 segment_id
    透传，prompt_authoring 只产 image_prompt 与 video_prompt。``<segments>`` 块为只读上下文，
    LLM 不重出这些字段——novel_text 由此不再经 prompt_authoring 的 LLM 扩写漂移。
    """
    return builtin_templates.render(
        "text/narration_prompt_authoring",
        project_overview=_overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=_format_aspect_ratio_desc(aspect_ratio),
        assets=_project_asset_appearances(characters, scenes, props),
        segments_content=_format_narration_script_plan_segments(script_plan_segments),
        episode=episode,
        target_language=target_language,
        write_image=write_image,
        instructions=instructions,
    )


def render_drama_content_for_prompt_authoring(content_scenes: list) -> str:
    """把 script_plan 已定稿的分镜内容渲染为 prompt_authoring 视觉生成的输入块（每分镜一段）。

    口播 / 原文锚仅供 LLM 理解戏剧节奏——「不要复制进视觉字段」由 ``build_drama_prompt`` 在
    ``<shots>`` 块前一次性声明，分镜条目内不逐条重复；它们由后端按 scene_id 透传
    （见 ``ScriptGenerator._merge_visual_layer``），prompt_authoring 只产出 image_prompt / video_prompt。

    渲染结果嵌入 prompt_authoring prompt 的 ``<shots>`` 块：资产名 / utterances 字段先判 ``isinstance(_, list)``——
    降级 / 手改 script_plan 可能写成非列表值（字符串会被逐字符迭代、数字会抛 TypeError），非列表按空处理；
    列表内再按 ``isinstance(_, str)`` 过滤非字符串脏数据。所有动态文本过 ``_neutralize_tags`` 中和尖括号——
    逐字 source_text / utterances / scene_description 含 ``<...>`` 时不致打散标签块结构（与 narration 的
    ``_format_narration_script_plan_segments`` 同口径）。本函数 fail-soft：结构性 fail-loud 在上游 _load_drama_script_plan_content。
    """
    if not content_scenes:
        return "（无分镜内容）"
    blocks: list[str] = []
    for scene in content_scenes:
        if not isinstance(scene, dict):
            continue
        sid = _neutralize_tags(str(scene.get("scene_id") or "?"))
        duration = scene.get("duration_seconds")
        header = f"### {sid}" + (f"（时长 {duration} 秒）" if duration else "")
        lines = [header]
        raw_chars = scene.get("characters_in_scene")
        raw_scenes_ref = scene.get("scenes")
        raw_props_ref = scene.get("props")
        chars = [c for c in raw_chars if isinstance(c, str)] if isinstance(raw_chars, list) else []
        scenes_ref = [s for s in raw_scenes_ref if isinstance(s, str)] if isinstance(raw_scenes_ref, list) else []
        props_ref = [p for p in raw_props_ref if isinstance(p, str)] if isinstance(raw_props_ref, list) else []
        lines.append(
            f"出场资产：角色 [{_neutralize_tags(', '.join(chars) or '无')}]、"
            f"场景 [{_neutralize_tags(', '.join(scenes_ref) or '无')}]、道具 [{_neutralize_tags(', '.join(props_ref) or '无')}]"
        )
        # 视觉改编描述由内容确认转换透传进正式脚本；存量正式脚本可能不带它，缺席时不渲染这一行。
        raw_scene_desc = scene.get("scene_description")
        if raw_scene_desc:
            scene_desc = _neutralize_tags(str(raw_scene_desc)).replace("\n", "\n  ")
            lines.append(f"视觉改编：{scene_desc}")
        raw_utterances = scene.get("utterances")
        utterances = raw_utterances if isinstance(raw_utterances, list) else []
        if utterances:
            lines.append("口播：")
            for u in utterances:
                if not isinstance(u, dict):
                    continue
                text = _neutralize_tags(str(u.get("text") or "")).replace("\n", "\n    ")
                if u.get("kind") == "dialogue":
                    speaker = _neutralize_tags(str(u.get("speaker") or ""))
                    lines.append(f"  - [台词] {speaker}：{text}")
                else:
                    lines.append(f"  - [画外音] {text}")
        source_text = scene.get("source_text")
        if source_text:
            source_block = _neutralize_tags(str(source_text)).replace("\n", "\n  ")
            lines.append(f"原文锚：{source_block}")
        lines.extend(_format_direction_lines(scene.get("direction")))
        if settled := _format_settled_image(scene.get("image_prompt")):
            lines.append(f"已定画面：{settled}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _project_asset_appearances(characters: dict | None, scenes: dict | None, props: dict | None) -> dict:
    """资产外观与引用名的键齐全投影；动态尖括号中和后作为模版数据。"""
    return {
        key: [
            {"name": _neutralize_tags(name), "appearance": _neutralize_tags(appearance) or None}
            for name, appearance in iter_asset_appearances(asset_type, bucket)
        ]
        for key, asset_type, bucket in (
            ("characters", "character", characters),
            ("scenes", "scene", scenes),
            ("props", "prop", props),
        )
    }


def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    scenes_content: str,
    episode: int,
    aspect_ratio: str = "16:9",
    target_language: str = "中文",
    characters: dict | None = None,
    scenes: dict | None = None,
    props: dict | None = None,
    instructions: str | None = None,
    write_image: bool = True,
) -> str:
    """构建剧情演绎 prompt_authoring（视觉层）prompt。

    提示词编写分两轮：``write_image`` 为 True 时只写 image_prompt（第一轮），为 False 时看着分镜内容里
    「已定画面」只写 video_prompt（第二轮）。

    内容抽取前移到 script_plan（见 ADR 0041）：分镜边界、出场资产、逐字口播 utterances、原文锚
    source_text、视觉改编描述均已在 script_plan 定稿，``scenes_content`` 是其渲染输入
    （``render_drama_content_for_prompt_authoring``）。prompt_authoring 仅产出视觉层（image_prompt / video_prompt），
    LLM 输出按 scene_id 与 script_plan 内容对齐、由后端合并；不再按 source_kind 分支、不再识别口播、
    不再标注资产或时长——这些都是 script_plan 的职责。

    ``characters`` / ``scenes`` / ``props`` 注入出场资产的外观描述（project.json 各 bucket），
    供视觉字段写服装 / 材质 / 陈设细节时取材；三者都为 None 时不渲染资产块。
    """
    return builtin_templates.render(
        "text/drama_prompt_authoring",
        project_overview=_overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=_format_aspect_ratio_desc(aspect_ratio),
        assets=(
            _project_asset_appearances(characters, scenes, props)
            if characters is not None or scenes is not None or props is not None
            else None
        ),
        scenes_content=scenes_content,
        episode=episode,
        target_language=target_language,
        write_image=write_image,
        instructions=instructions,
    )


def build_normalize_prompt(
    novel_text: str,
    project_overview: dict,
    style: str,
    characters: dict,
    scenes: dict,
    props: dict,
    default_duration: int | None,
    supported_durations: list[int],
    episode: int,
    source_kind: str = "novel",
    target_language: str = "中文",
    source_language: str | None = None,
    speech_rate_override: float | None = None,
    episode_target_duration: int | None = None,
    episode_outline: dict | None = None,
    next_episode_outline: dict | None = None,
    screenplay: dict | None = None,
    instructions: str | None = None,
) -> str:
    """脚本规划的规范化 prompt：源文 → 结构化分镜内容（utterances + source_text + 视觉改编描述）。

    由 ``generate_script_plan`` 的剧情变体消费，措辞在内置模版 ``text/drama_script_plan``。输出受
    response_schema（``DramaNormalizedScript``）约束为结构化 JSON。``source_kind`` 非 ``"screenplay"``
    的取值一律按 ``"novel"`` 渲染。

    ``source_language`` 决定口播下界句的语速与阅读单位量词（``lib.speech.speech_rate`` 单一真相源），非字符串
    回退默认语速；``speech_rate_override`` 是项目级语速覆盖，``None`` 即回退语言默认。
    ``episode_target_duration`` / ``episode_outline`` / ``next_episode_outline`` / ``instructions`` 为
    ``None`` 或空时不渲染对应分节。
    """
    # 空集合或 default 不在集合内都会产出自相矛盾的提示词，生成前失败更便于诊断。
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")

    # source_language 来自 project.json，可能是非字符串脏数据，下游 .strip() 会崩。
    source_language = source_language if isinstance(source_language, str) else None
    return builtin_templates.render(
        "text/drama_script_plan",
        source_kind="screenplay" if source_kind == "screenplay" else "novel",
        target_language=target_language,
        project_overview=_overview_slot(project_overview),
        style=style,
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        novel_text=novel_text,
        episode=episode,
        durations=", ".join(str(d) for d in normalized_durations),
        max_duration=normalized_durations[-1],
        default_duration=default_duration,
        speech_rate=f"{speech_rate_units_per_second(source_language, speech_rate_override):g}",
        speech_unit=reading_unit_noun(source_language),
        episode_target_duration=episode_target_duration,
        episode_outline=_outline_slot(episode_outline),
        next_episode_outline=_outline_slot(next_episode_outline),
        screenplay=_screenplay_slot(screenplay),
        instructions=instructions or None,
    )


def build_narration_split_prompt(
    *,
    novel_text: str,
    project_overview: dict,
    characters: dict,
    scenes: dict,
    props: dict,
    default_duration: int | None,
    supported_durations: list[int],
    episode: int,
    target_language: str = "中文",
    episode_target_duration: int | None = None,
    screenplay: dict | None = None,
    instructions: str | None = None,
) -> str:
    """脚本规划的旁白/解说分镜拆分 prompt：源文 → 结构化分镜表（逐字 novel_text + 时长 + 资产登记）。

    由 ``generate_script_plan`` 的旁白变体消费。输出受 response_schema（``NarrationScriptPlanDraft``）
    约束为结构化 JSON——``novel_text`` 逐字保留原文（配音与透传真相源），视觉层由后续 prompt_authoring 按
    ``segment_id`` 对齐补齐。分镜时长的成员校验（∈ ``supported_durations``）由工具后校验兜底，因静态
    ``NarrationScriptPlanSegment.duration_seconds`` 是 ``ge=1, le=60`` 开区间、不在 schema 层枚举硬约束
    （复用既有分镜 schema）。

    ``default_duration`` 为单分镜默认秒数偏好；与 ``build_normalize_prompt`` 不同，此处对漂移到
    ``supported_durations`` 之外的 default 按 None 处理（软偏好、可被内容需要覆盖），不 fail-loud——
    与 split-narration-segments 子智能体的「default 非成员按 null」口径一致。

    ``episode_target_duration`` 是项目级「单集目标时长」偏好（秒），驱动模型决定本集拆多少个分镜；
    ``None`` 即未设目标、不注入该段。与 ``default_duration`` 是两个尺度，同为软偏好。
    """
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        default_duration = None

    return builtin_templates.render(
        "text/narration_script_plan",
        project_overview=_overview_slot(project_overview),
        novel_text=novel_text,
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        durations=", ".join(str(d) for d in normalized_durations),
        max_duration=normalized_durations[-1],
        default_duration=default_duration,
        episode_target_duration=episode_target_duration,
        episode=episode,
        target_language=target_language,
        screenplay=_screenplay_slot(screenplay),
        instructions=instructions,
    )


def render_director_entries(entries: list[dict], kind: str, *, target_ids: list[str] | None = None) -> str:
    """把全集条目渲染为导演步骤的输入块：id、时长、出场角色、画面 / 原文与口播，已有导演说明时一并列出。

    ``kind`` 是骨架种类（``segments`` 旁白 / ``scenes`` 剧情）。``target_ids`` 非空时，本次要重做的条目带「（本次重做）」标记，
    其余条目的现有导演说明作前后文参照。动态文本过 ``_neutralize_tags``。
    """
    if not entries:
        return "（无分镜内容）"
    id_field, chars_field = (
        ("segment_id", "characters_in_segment") if kind == "segments" else ("scene_id", "characters_in_scene")
    )
    targeted = set(target_ids or ())
    blocks: list[str] = []
    for index, entry in enumerate(entries, start=1):
        entry_id = _neutralize_tags(str(entry.get(id_field) or "?"))
        duration = entry.get("duration_seconds")
        mark = "（本次重做）" if str(entry.get(id_field)) in targeted else ""
        header = f"### {index}. {entry_id}" + (f"（时长 {duration} 秒）" if duration else "") + mark
        raw_chars = entry.get(chars_field)
        chars = [name for name in (raw_chars if isinstance(raw_chars, list) else []) if isinstance(name, str)]
        lines = [header, f"出场角色：{_neutralize_tags('、'.join(chars) or '无')}"]
        if kind == "segments":
            lines.append("原文：" + _neutralize_tags(str(entry.get("novel_text") or "")).replace("\n", "\n  "))
        else:
            description = entry.get("scene_description")
            if description:
                lines.append("画面：" + _neutralize_tags(str(description)).replace("\n", "\n  "))
            raw_utterances = entry.get("utterances")
            for utterance in raw_utterances if isinstance(raw_utterances, list) else []:
                if not isinstance(utterance, dict):
                    continue
                text = _neutralize_tags(str(utterance.get("text") or "")).replace("\n", "\n    ")
                if utterance.get("kind") == "dialogue":
                    lines.append(f"  - [台词] {_neutralize_tags(str(utterance.get('speaker') or ''))}：{text}")
                else:
                    lines.append(f"  - [画外音] {text}")
        if entry.get("segment_break") is True:
            lines.append("场景切换点")
        lines.extend(_format_direction_lines(entry.get("direction")))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_director_prompt(
    *,
    project_overview: dict,
    characters: dict | None,
    scenes: dict | None,
    props: dict | None,
    entries: list[dict],
    kind: str,
    episode: int,
    target_ids: list[str] | None = None,
    revision_note: str | None = None,
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """构建导演步骤 prompt：全集条目 + 资产外观 → 集级导演规划与逐条目导演说明（``DirectorEpisodeOutput``）。

    ``target_ids`` 非空表示只修订这些条目（其余条目的现有说明作参照）；``revision_note`` 是审阅者的修改意见。
    """
    return builtin_templates.render(
        "text/director_episode",
        target_language=target_language,
        project_overview=_overview_slot(project_overview),
        assets=_project_asset_appearances(characters, scenes, props),
        entries_content=render_director_entries(entries, kind, target_ids=target_ids),
        episode=episode,
        total_entries=len(entries),
        target_entry_ids=list(target_ids) if target_ids else None,
        revision_note=_stripped_text(revision_note),
        instructions=instructions or None,
    )


def build_overview_prompt(source_content: str, source_kind: str = "novel", target_language: str = "中文") -> str:
    """构建项目概述（overview）生成 prompt，措辞在内置模版 ``text/source_overview``。

    ``source_kind`` 非 ``"screenplay"`` 的取值（含缺省与非法值）一律按 ``"novel"`` 渲染。overview 产出的
    字段会注入后续所有生成 prompt，输出语言须与其余 builder 同口径（target_language 由调用方按
    project.json 的 source_language 解析）。
    """
    return builtin_templates.render(
        "text/source_overview",
        source_kind="screenplay" if source_kind == "screenplay" else "novel",
        target_language=target_language,
        source_content=source_content,
    )
