"""
script_models.py - 剧本数据模型

使用 Pydantic 定义剧本的数据结构，用于：
1. Gemini API 的 response_schema（Structured Outputs）
2. 输出验证
"""

import logging
from typing import Annotated, Any, ClassVar, Literal, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    create_model,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from lib.script.script_skeleton import resolve_declared_kind

# 所有剧本模型默认禁止额外字段:Agent 的 `patch_episode_script` 通过 `_set_nested` 允许在
# dict 上凭空创建叶子(为了让 Agent 补 LLM 漏写的 optional 字段);若 Pydantic 走默认
# `extra="ignore"`,任何 typo / hallucinated 字段都会被静默丢,但 dict 已被 atomic_write_json
# 持久化,JSON 文件里垃圾字段长存,「不更坏」error-set diff 永远抓不到(before/after Pydantic
# 都 ignore → 两边 errors 集合相同 → new_errors=∅ → 放行)。`extra="forbid"` 让 Pydantic
# 在 typo 写入后明确把它列为新 ValidationError,「不更坏」就能挡下。
# ScriptGenerator 路径(LLM 输出走 model_validate + model_dump)也会被这层保护:LLM 在
# Structured Outputs 下不太会产出额外字段,产出即 hallucination,拒比静默丢更安全。
_STRICT_CONFIG = ConfigDict(extra="forbid")

# ============ 枚举类型定义 ============

ShotType = Literal[
    "Extreme Close-up",
    "Close-up",
    "Medium Close-up",
    "Medium Shot",
    "Medium Long Shot",
    "Long Shot",
    "Extreme Long Shot",
    "Over-the-shoulder",
    "Point-of-view",
]

# 取值须为供应商官方运镜词表承认的写法（对齐 MiniMax Hailuo [command] 指令表与阿里万相
# 基础/高级运镜表的运动类条目），下游按原文插值进视频 prompt，不做二次翻译。
CameraMotion = Literal[
    "Static",
    "Pan Left",
    "Pan Right",
    "Tilt Up",
    "Tilt Down",
    "Zoom In",
    "Zoom Out",
    "Push In",
    "Pull Out",
    "Truck Left",
    "Truck Right",
    "Pedestal Up",
    "Pedestal Down",
    "Orbit",
    "Tracking Shot",
    "Shake",
]

TransitionType = Literal[
    "cut",
    "fade",
    "dissolve",
]

logger = logging.getLogger(__name__)


def _canon_enum_key(value: str) -> str:
    """枚举漂移归一键：下划线/连字符折叠为空格、多空格合一、casefold。"""
    return " ".join(value.replace("_", " ").replace("-", " ").split()).casefold()


# schema 的 enum 只有在供应商执行约束解码时才是硬约束；代理网关/OpenAI 兼容通道丢弃
# wire 级结构化参数时，模型会把枚举写成大写/小写蛇形（MEDIUM_SHOT / medium_shot）
# 甚至词表外值（如 wide_shot / dolly_in）。机械归一（大小写/
# 分隔符）把风格漂移拉回词表；词表外值不做语义近义映射（语义映射永远穷举不全），
# 一律降级为中性默认值并 warn——这两个字段下游只作生成 prompt 的文本插值，
# 单镜头词汇漂移不值得让整集剧本生成失败。
_DEFAULT_SHOT_TYPE: ShotType = "Medium Shot"
_DEFAULT_CAMERA_MOTION: CameraMotion = "Static"

_SHOT_TYPE_BY_KEY: dict[str, str] = {_canon_enum_key(v): v for v in get_args(ShotType)}
_CAMERA_MOTION_BY_KEY: dict[str, str] = {_canon_enum_key(v): v for v in get_args(CameraMotion)}


def _normalize_shot_type(value: object) -> object:
    if not isinstance(value, str):
        return value
    hit = _SHOT_TYPE_BY_KEY.get(_canon_enum_key(value))
    if hit is not None:
        return hit
    logger.warning("shot_type 枚举漂移无法归一，降级为 %s: %r", _DEFAULT_SHOT_TYPE, value)
    return _DEFAULT_SHOT_TYPE


def _normalize_camera_motion(value: object) -> object:
    if not isinstance(value, str):
        return value
    hit = _CAMERA_MOTION_BY_KEY.get(_canon_enum_key(value))
    if hit is not None:
        return hit
    logger.warning("camera_motion 枚举漂移无法归一，降级为 %s: %r", _DEFAULT_CAMERA_MOTION, value)
    return _DEFAULT_CAMERA_MOTION


def _none_to_empty_list(value: object) -> object:
    """漂移容错：非约束解码通道可能把可选列表字段写成 null 而非省略。"""
    return [] if value is None else value


class Dialogue(BaseModel):
    """对话条目"""

    model_config = _STRICT_CONFIG

    speaker: str = Field(description="说话人名称")
    line: str = Field(description="对话内容")


class Composition(BaseModel):
    """构图信息"""

    model_config = _STRICT_CONFIG

    shot_type: Annotated[ShotType, BeforeValidator(_normalize_shot_type)] = Field(description="镜头类型")
    lighting: str = Field(description="光线描述")
    ambiance: str = Field(description="整体氛围")


class ImagePrompt(BaseModel):
    """分镜图生成 Prompt"""

    model_config = _STRICT_CONFIG

    scene: str = Field(description="画面静态描述；动态内容由 video_prompt.action 承载")
    composition: Composition = Field(description="构图信息")


class _VideoPromptCore(BaseModel):
    """video_prompt 的画面层公共字段（动作 / 运镜 / 环境音）；dialogue 由具体变体决定是否携带。"""

    model_config = _STRICT_CONFIG

    action: str = Field(description="该分镜时长内的动作描述；镜头运动由 camera_motion 承载")
    camera_motion: Annotated[CameraMotion, BeforeValidator(_normalize_camera_motion)] = Field(description="镜头运动")
    ambiance_audio: str = Field(description="环境音效（画内音）")


class VideoPrompt(_VideoPromptCore):
    """narration / ad 视频生成 Prompt：含角色对话 dialogue。

    drama 的有序发声由 ``DramaScene.utterances`` 承载，video_prompt 使用无-dialogue 的
    ``DramaVideoPrompt``（见 ADR 0040）。
    """

    dialogue: Annotated[list[Dialogue], BeforeValidator(_none_to_empty_list)] = Field(
        default_factory=list, description="对话列表，仅当原文有引号对话时填写"
    )


class DramaVideoPrompt(_VideoPromptCore):
    """drama 视频生成 Prompt：无 dialogue，口播由分镜级 ``DramaScene.utterances`` 统一承载。

    ``extra="forbid"`` 下任何残留的 ``dialogue`` 键会被 ``DramaScene`` 读时迁移先行剥离。
    """


def _require_non_blank_prompt(value: str) -> str:
    """文本形态提示词不得为空白：空串等价于「没有提示词」，渲染层同样以 ValueError 拒绝。"""
    if not value.strip():
        raise ValueError("prompt text must not be empty")
    return value


#: 提示词的文本形态：该字段本身就是送给模型的提示词主体，渲染层只做必要的注入、不套结构模板。
#: 以 ``SkipJsonSchema`` 排除在 response_schema 之外——结构化输出仍只允许 LLM 产出结构形态，
#: 文本形态是创作者在时间线里显式切换的编写方式，不是模型可选的输出形状。
PromptText = SkipJsonSchema[Annotated[str, AfterValidator(_require_non_blank_prompt)]]

#: 提示词的「待生成」态：脚本规划机械转为正式脚本时视觉层不写，条目以 ``None`` 落盘等待
#: 提示词编写或人工补写。``SkipJsonSchema`` 把 ``null`` 排除在 response_schema 之外——待生成
#: 只由转换路径写入，不是模型可选的输出形状；空串仍由 ``PromptText`` 拒绝。
PendingPrompt = SkipJsonSchema[None]

#: 条目级待编写标记的字段名，见各条目模型的 ``pending_authoring``。
PENDING_AUTHORING_FIELD = "pending_authoring"


class GeneratedAssets(BaseModel):
    """生成资源状态（初始化为空）"""

    model_config = _STRICT_CONFIG

    storyboard_image: str | None = Field(default=None, description="分镜图路径")
    storyboard_last_image: str | None = Field(default=None, description="分镜图最后一帧路径")
    grid_id: str | None = Field(default=None, description="关联的网格图生成 ID")
    grid_cell_index: int | None = Field(default=None, description="在网格图中的单元格索引")
    video_clip: str | None = Field(default=None, description="视频片段路径")
    # video_thumbnail 由 reference_video_tasks / generation_tasks 在视频生成后通过
    # lib.infra.thumbnail.extract_video_thumbnail 抽帧落盘,写到 ga["video_thumbnail"];
    # 漏声明的话 extra="forbid" 会让「不更坏」检测到 extra_forbidden 差集,拒整集写盘。
    video_thumbnail: str | None = Field(default=None, description="视频缩略图路径")
    video_uri: str | None = Field(default=None, description="视频 URI")
    # narration_audio 由 TTS 任务（generation_tasks.execute_tts_task）在合成后写回，
    # 显式声明使其通过 extra="forbid" + 「不更坏」守卫；仅旁白/解说 segment 写入，drama/refvideo 恒 None。
    narration_audio: str | None = Field(default=None, description="旁白配音路径")
    status: Literal["pending", "storyboard_ready", "completed"] = Field(default="pending", description="生成状态")
    # video_clip 写回时（apply_unit_video_assets 单一写点）机械戳生成时间；用于跟角色
    # `voice_updated_at` 比较，判定该片段是否生成于当前参考音频设置之前。
    # 缺省视为早于任何设置，落在「生成于设置之前」语义内。对 LLM 隐藏。
    video_generated_at: SkipJsonSchema[str | None] = Field(default=None, description="视频生成完成时间（ISO8601 UTC）")
    # 仅用于无损接收迁移前的历史记录；运行时不读取、比较或新增该键。
    source_signature: SkipJsonSchema[str | None] = Field(default=None, description="历史产物来源签名")


def get_generated_assets(item: dict) -> dict:
    """归一化访问 item 的 ``generated_assets`` 容器。

    该字段来自磁盘上的剧本 JSON，不可信任：外部编辑可能把它损坏成非 dict（如
    list/字符串）。归一化为空 dict 而非抛错，让调用方走各自「该资产未生成」的
    既有分支（单条跳过 / 可读拒绝），不会在批量入队时因为一条脏数据抛未捕获
    ``AttributeError`` 中断整批。
    """
    assets = item.get("generated_assets")
    return assets if isinstance(assets, dict) else {}


# ============ 导演规划（集级 DirectorPlan + 逐条目 EntryDirection） ============
#
# 集级 ``DirectorPlan`` 与逐 unit 的表演说明被参考生视频 script_plan 与分镜路线（旁白 / 剧情）共用；
# 分镜路线的逐条目导演说明见 ``EntryDirection``，由导演步骤在内容确认之后、提示词编写之前写入正式脚本。


class DirectorArcBeat(BaseModel):
    """集级情绪弧线的一拍：一种情绪或转折，以及承载它的 unit。"""

    model_config = _STRICT_CONFIG

    beat: str = Field(description="这一拍的情绪或转折，一个短语")
    units: list[int] = Field(description="承载这一拍的 unit / 分镜序号（从 1 起，对应条目数组顺序）")


class DirectorPlan(BaseModel):
    """集级导演规划：本集要完成什么、情绪弧线、基调与贯穿意象。"""

    model_config = _STRICT_CONFIG

    episode_intent: str = Field(description="本集要让观众知道、感受或期待什么，一到两句")
    arc: list[DirectorArcBeat] = Field(description="按顺序排列的情绪弧线")
    tone: str = Field(description="整集基调及其变化，一句")
    motifs: list[str] = Field(default_factory=list, description="贯穿多个 unit / 分镜的意象、声音或道具，1-4 个")


class DirectorPerformance(BaseModel):
    """某个角色在本 unit 里的表演说明。"""

    model_config = _STRICT_CONFIG

    character: str = Field(description="角色登记名")
    note: str = Field(description="节奏、神情、动作与音量，不写外貌、服装与镜头")


class DirectorCamera(BaseModel):
    """导演为一个条目定的镜头：机位角度、景别、运动与一句话意图。

    四个字段都是自由文本（非空），不设固定词表：导演按画面需要自行措辞，意图写清「为什么这样拍」。
    """

    model_config = _STRICT_CONFIG

    angle: str = Field(min_length=1, description="机位角度，如低角度、平视、过肩")
    shot_size: str = Field(min_length=1, description="景别，如特写、中景、全景")
    movement: str = Field(min_length=1, description="镜头运动，如固定、推进、跟拍")
    intent: str = Field(min_length=1, description="这样拍的意图：为什么选这个角度 / 景别 / 运动，观众该有什么感受")


class EntryDirection(BaseModel):
    """分镜路线（旁白 / 剧情）单个条目的导演说明：镜头、逐角色表演与前后衔接。"""

    model_config = _STRICT_CONFIG

    camera: DirectorCamera = Field(description="本条目的镜头与意图")
    performance: list[DirectorPerformance] = Field(
        default_factory=list, description="逐角色的表演说明；出场的每个角色都要有一条"
    )
    continuity_in: str = Field(min_length=1, description="开场状态：地点、时间、人物位置与手中物件")
    continuity_out: str = Field(min_length=1, description="结束状态，须与下一个条目的 continuity_in 衔接")


class DirectorEntryOutput(BaseModel):
    """导演步骤对单个条目的产出：条目 id（segment_id / scene_id）+ 导演说明。"""

    model_config = _STRICT_CONFIG

    id: str = Field(min_length=1, description="条目 id：旁白 / 剧情分别取 segment_id / scene_id，逐字等于输入")
    direction: EntryDirection


class DirectorEpisodeOutput(BaseModel):
    """导演步骤的结构化产出（文本模型的 response_schema）：集级规划 + 逐条目导演说明。"""

    model_config = _STRICT_CONFIG

    director_plan: DirectorPlan = Field(description="集级导演规划，先于逐条目说明写出")
    entries: list[DirectorEntryOutput] = Field(min_length=1, description="按剧本顺序排列的逐条目导演说明")


# ============ 旁白/解说（Narration） ============


class NarrationSegment(BaseModel):
    """旁白/解说的分镜

    注意：不设独立 `episode` 字段。集号已经编码在 `segment_id`（格式 E{集}S{序号}）中，
    与 `DramaScene.scene_id` / `ReferenceVideoUnit.unit_id` 保持一致。避免 AI 在每个
    segment 上重复生成集号造成幻觉污染（详见 `NarrationEpisodeScript` docstring）。
    """

    model_config = _STRICT_CONFIG

    # 已废弃但存量 JSON 里可能残留的字段:在 extra="forbid" 拒绝之前显式 pop 掉。
    # clues_in_segment 是 v0→v1 migration 删除的字段(lib/project/project_migrations/
    # v0_to_v1_clues_to_scenes_props.py),archive 流程通过 project_archive.py 已 pop,
    # 但若直接 NarrationSegment.model_validate(legacy_dict) 调用(_guard_no_worse lenient
    # 包装外)需要这里兜底,与 DramaScene.LEGACY_DROPPED_FIELDS 同模式。
    LEGACY_DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset({"clues_in_segment"})

    @model_validator(mode="before")
    @classmethod
    def _strip_legacy_fields(cls, data: object) -> object:
        if isinstance(data, dict):
            for k in cls.LEGACY_DROPPED_FIELDS:
                data.pop(k, None)
        return data

    segment_id: str = Field(description="分镜 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    duration_seconds: int = Field(ge=1, le=60, description="分镜时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    novel_text: str = Field(description="小说原文（必须原样保留，用于后期配音）")
    characters_in_segment: list[str] = Field(description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    image_prompt: ImagePrompt | PromptText | PendingPrompt = Field(default=None, description="分镜图生成提示词")
    video_prompt: VideoPrompt | PromptText | PendingPrompt = Field(default=None, description="视频生成提示词")
    # transition_to_next 由 _add_metadata default + 用户 PATCH 路径(projects.py UpdateSegmentRequest)管理;
    # LLM 无 prompt 引导,隐藏避免乱填污染剪映/compose-video 合成
    transition_to_next: SkipJsonSchema[TransitionType] = Field(default="cut", description="转场类型")
    # 以下字段对 LLM 隐藏（SkipJsonSchema）：note 是人工备注、generated_assets 是 post-LLM 运行时状态。
    # 仍保留在 Pydantic 模型里以便存储 / 校验，但不出现在 response_schema 中，避免 LLM 填污染数据。
    note: SkipJsonSchema[str | None] = Field(default=None, description="用户备注（不参与生成）")
    # 尾帧快照的项目内相对路径（``end_frames/scene_{id}.png``）。用户意图而非运行时产出，
    # 故不进 generated_assets；只由尾帧设置/清除端点写入（通用 PATCH 白名单不含此字段，
    # 避免绕过快照复制写出悬空引用或越界路径），整集剧本重生成不保留（同 note 口径）。
    end_frame_image: SkipJsonSchema[str | None] = Field(default=None, description="尾帧快照路径（项目内相对路径）")
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="生成资源状态"
    )
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="该单元需要人工重新规划")
    # 待编写：视觉层尚未补出。不带视觉层的新增条目置位，提示词编写写回或手写齐视觉层时清除；
    # 对 LLM 隐藏，不在任何 PATCH 白名单内。落盘只在置位时出现。
    pending_authoring: SkipJsonSchema[bool] = Field(default=False, description="该条目待编写")
    # 导演说明：导演步骤写入正式脚本，提示词编写只读消费；对 LLM 的视觉层 schema 隐藏，落盘只在存在时出现。
    direction: SkipJsonSchema[EntryDirection | None] = Field(default=None, description="导演说明")


class NovelInfo(BaseModel):
    """小说来源信息

    title/chapter 都带 default,以便 SkipJsonSchema[NovelInfo] 的 default_factory=NovelInfo 构造。
    真实值由 ``ScriptGenerator._add_metadata`` setdefault 注入(项目 title + ``f"第N集"``);
    LLM 不再被引导填写,避免虚构章节名污染 compose-video 的输出 mp4 文件命名。
    """

    model_config = _STRICT_CONFIG

    title: str = Field(default="", description="小说标题")
    chapter: str = Field(default="", description="章节名称")


class NarrationEpisodeScript(BaseModel):
    """旁白/解说剧本

    注意：`episode` 字段不在 schema 中。CLI 参数 `--episode N` 是集号的唯一真相源，
    由 `ScriptGenerator._add_metadata` 写入。不让 AI 生成该字段，避免幻觉写错集号
    进而污染 project.json（曾导致 episode_10.json 内部 episode=1 覆盖第 1 集条目）。

    顶层**不**走 ``extra="forbid"``:``episode`` / ``metadata`` 等字段由运行时注入
    (``_add_metadata`` / ``_write_script_unlocked``)而非 schema 内字段,顶层 forbid
    会让现有写盘流程崩;``generation_mode`` 不在其列——生成模式的真相源是 project.json,剧本
    不留戳,生成写盘前由 ``ScriptGenerator._add_metadata`` 剥离,存量在制品里的残留字段按
    未知字段忽略。typo 防护靠子模型(VideoPrompt / ImagePrompt /
    NarrationSegment 等)的 ``extra="forbid"`` 在嵌套字段路径上挡。
    """

    title: str = Field(description="剧集标题")
    # content_mode 由 _add_metadata setdefault 注入项目级真值;Literal 单值让 LLM 写无意义
    content_mode: SkipJsonSchema[Literal["narration"]] = Field(default="narration", description="创作类型")
    # novel 由 _add_metadata 注入 {项目 title, f"第N集"};compose-video 用 chapter 作输出文件名,LLM 自由发挥反而不可预测
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="小说来源信息")
    # hook / next_episode_teaser 由 _add_metadata 从分集账本注入（账本是钩子设计的
    # 单一真相源，LLM 不参与填写）；账本无规划数据时为 null。
    hook: SkipJsonSchema[str | None] = Field(default=None, description="集尾钩子（来自分集账本）")
    next_episode_teaser: SkipJsonSchema[str | None] = Field(default=None, description="下集预告语（来自分集账本）")
    segments: list[NarrationSegment] = Field(description="分镜列表")
    # 集级导演规划：导演步骤写入，对 LLM 隐藏，落盘只在存在时出现。
    director_plan: SkipJsonSchema[DirectorPlan | None] = Field(default=None, description="集级导演规划")


# ============ 旁白/解说 script_plan 结构化中间态 / prompt_authoring 视觉层 ============
#
# 两段式职责切分：script_plan（分镜拆分）产出内容层（逐字 novel_text + 分镜边界 + 时长），
# prompt_authoring（generate-script）只产出视觉层（image_prompt / video_prompt），按 segment_id
# 合并回 script_plan 已确认结构。novel_text 永不经 prompt_authoring 的 LLM 重出 → 消除扩写漂移。


class NarrationScriptPlanSegment(BaseModel):
    """旁白/解说 script_plan（分镜拆分）产出的结构化分镜：内容层。

    只承载 script_plan 已定的内容字段：分镜边界（segment_id / segment_break）、逐字 novel_text、
    时长。视觉层（image_prompt / video_prompt）由 prompt_authoring 生成后按 segment_id 合并进来。
    characters_in_segment / scenes / props 由 script_plan 登记（内容层是资产引用的单一真相源）：
    prompt_authoring 视觉层 schema 不含资产字段、只读消费、不补登记不改写，故三者必填——无资产须显式写 []，
    缺字段即 fail-loud，杜绝把漏登记静默吞成空数组。合并后落到同一 NarrationSegment。
    """

    model_config = _STRICT_CONFIG

    segment_id: str = Field(pattern=r"^E[1-9]\d*S\d{2}$", description="分镜 ID，格式 E{集}S##")
    novel_text: str = Field(min_length=1, description="小说原文（逐字保留，用于配音与透传）")
    duration_seconds: int = Field(ge=1, le=60, description="分镜时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    characters_in_segment: list[str] = Field(description="出场角色名称列表；无则显式写 []")
    scenes: list[str] = Field(description="出场场景名称列表；无则显式写 []")
    props: list[str] = Field(description="出场道具名称列表；无则显式写 []")
    # 仅在提示词给出已通过剧本时由模型填写，供产出与剧本的对齐判定（lib.script.screenplay_alignment）使用；
    # exclude=True：model_dump 不含它，持久化的 script_plan 与正式脚本形状不变。
    scene_no: int | None = Field(
        default=None,
        ge=1,
        exclude=True,
        description="所属剧本场次的序号（从 1 起）；仅在提示词提供了已确认剧本时填写，否则省略",
    )


class NarrationScriptPlanDraft(BaseModel):
    """旁白/解说 script_plan 结构化中间态（``drafts/episode_N/script_plan_segments.json`` 的 schema）。

    顶层容忍附加字段（如 ``episode`` 头）：分镜拆分由子智能体经 Write 产出、非结构化输出
    强约束，读时按本模型校验。
    """

    model_config = ConfigDict(extra="ignore")

    segments: list[NarrationScriptPlanSegment] = Field(description="分镜列表")


class NarrationVisualSegment(BaseModel):
    """prompt_authoring（generate-script）按 segment_id 产出的视觉层。

    LLM 只产视觉字段（image_prompt / video_prompt）+ 对齐锚 segment_id；novel_text、时长、
    segment_break、characters_in_segment / scenes / props 等非视觉字段由 script_plan 已定、经后端
    按 segment_id 合并——不进 LLM 输出，从工程上杜绝其经 Structured Outputs 漂移。
    ``extra="forbid"`` 兜底：非结构化输出后端若混入 novel_text 等字段，校验即拒、不静默覆盖。
    """

    model_config = _STRICT_CONFIG

    segment_id: str = Field(min_length=1, description="对齐锚：必须取自 script_plan 分镜表，逐一对应、不增不减")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")


class NarrationVisualEpisodeScript(BaseModel):
    """prompt_authoring 视觉层的 LLM ``response_schema``：剧集标题 + 各分镜视觉层。

    顶层不走 ``extra="forbid"``（与 NarrationEpisodeScript 同口径）；逐分镜视觉层由
    NarrationVisualSegment 的 ``extra="forbid"`` 在嵌套路径上挡 typo / 漂移。
    """

    title: str = Field(description="剧集标题")
    segments: list[NarrationVisualSegment] = Field(description="各分镜的视觉层，按 segment_id 一一对齐 script_plan")


# ============ 剧情演绎（Drama） ============


UtteranceKind = Literal["dialogue", "voiceover"]


class Utterance(BaseModel):
    """drama 分镜级有序发声条目：插入顺序即幕内时序（台词与画外音的先后）。

    判别式联合 ``{kind, speaker, text}``，``kind`` 决定下游路由与 ``kind ⇄ speaker`` 约束：
    - ``dialogue``：角色发声（对白、内心独白、角色画外解说），必带非空 ``speaker``，
      进视频 YAML 交供应商出口型音轨；
    - ``voiceover``：无说话人的旁白解说，``speaker`` 必为 ``None``，不作视频提示词（留给字幕 / TTS）。

    取显式 ``kind`` 而非「speaker 有无隐式判别」：与 ``ReferenceResource.type`` 既有判别式风格一致、
    LLM 结构化输出更稳（见 ADR 0040）。
    """

    model_config = _STRICT_CONFIG

    kind: UtteranceKind = Field(description="发声类型：dialogue=带角色归属的角色发声、voiceover=无角色归属的叙述旁白")
    speaker: str | None = Field(default=None, description="说话角色名；dialogue 必填非空、voiceover 必须为 null")
    text: str = Field(description="发声内容原文，逐字保留")

    @model_validator(mode="before")
    @classmethod
    def _normalize_speaker(cls, data: object) -> object:
        # 空串 / 纯空白 speaker 归一为 None：voiceover 的「无说话人」既可写 null 也可写 ""，统一到
        # None 后由下方 kind ⇄ speaker 校验裁决（dialogue 的空 speaker 因此被判非法）。
        if isinstance(data, dict):
            speaker = data.get("speaker")
            if isinstance(speaker, str) and not speaker.strip():
                data = {**data, "speaker": None}
        return data

    @model_validator(mode="after")
    def _check_kind_speaker(self) -> "Utterance":
        if self.kind == "dialogue":
            if not self.speaker:
                raise ValueError("dialogue utterance 必须带非空 speaker")
        elif self.speaker is not None:
            raise ValueError("voiceover utterance 不得带 speaker")
        return self


class DramaScene(BaseModel):
    """剧情演绎的分镜。"""

    model_config = _STRICT_CONFIG

    # 已废弃但存量 JSON 里可能残留的字段:在 extra="forbid" 拒绝之前显式 pop 掉,
    # 与「未知字段(typo / hallucination)一律拒」并存——前者是已知 deprecated,
    # 后者才是 forbid 想挡的真问题。新增 deprecate 字段时把名字加到这个集合。
    # - scene_type:已删除的场景类型字段
    # - clues_in_scene:v0→v1 migration 删的线索字段(同 NarrationSegment.clues_in_segment)
    LEGACY_DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset({"scene_type", "clues_in_scene"})

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: object) -> object:
        """读时迁移：剥离已废弃字段，并把旧口播双字段（``video_prompt.dialogue`` + ``voiceover``）
        合成为有序 ``utterances``。

        判据「无 utterances 键」= 存量数据：合成时 dialogue 段在前、voiceover 段在后（旧数据无交错
        信息，确定性 best-effort、不假装还原），并剥离 ``voiceover`` 与 ``video_prompt.dialogue`` 使
        ``DramaVideoPrompt`` 的 ``extra="forbid"`` 不报错。缺说话人的旧台词归为无说话人 voiceover
        （保内容、不编造 speaker）。新数据（``utterances`` 已在）走快路径、不改写。不就地改调用方 dict。
        """
        if not isinstance(data, dict):
            return data
        legacy_present = any(k in data for k in cls.LEGACY_DROPPED_FIELDS)
        needs_spoken_migration = "utterances" not in data
        if not legacy_present and not needs_spoken_migration:
            return data
        data = dict(data)
        for k in cls.LEGACY_DROPPED_FIELDS:
            data.pop(k, None)
        if needs_spoken_migration:
            data["utterances"] = cls._synthesize_utterances(data)
            data.pop("voiceover", None)
            video_prompt = data.get("video_prompt")
            if isinstance(video_prompt, dict) and "dialogue" in video_prompt:
                data["video_prompt"] = {k: v for k, v in video_prompt.items() if k != "dialogue"}
        return data

    @staticmethod
    def _synthesize_utterances(scene: dict[str, object]) -> list[dict[str, object]]:
        """从旧 ``video_prompt.dialogue`` + 场景 ``voiceover`` 合成有序 utterances（dialogue 段在前）。"""
        utterances: list[dict[str, object]] = []
        video_prompt = scene.get("video_prompt")
        if isinstance(video_prompt, dict):
            dialogue = video_prompt.get("dialogue")
            if isinstance(dialogue, list):
                for entry in dialogue:
                    if not isinstance(entry, dict):
                        continue
                    text = str(entry.get("line") or "").strip()
                    if not text:
                        continue
                    speaker = str(entry.get("speaker") or "").strip()
                    if speaker:
                        utterances.append({"kind": "dialogue", "speaker": speaker, "text": text})
                    else:
                        # 旧台词缺说话人 → 归为无说话人 voiceover（best-effort，保内容、不编造 speaker）
                        utterances.append({"kind": "voiceover", "speaker": None, "text": text})
        voiceover = scene.get("voiceover")
        if isinstance(voiceover, list):
            utterances.extend(
                {"kind": "voiceover", "speaker": None, "text": line.strip()}
                for line in voiceover
                if isinstance(line, str) and line.strip()
            )
        return utterances

    scene_id: str = Field(min_length=1, description="分镜 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    duration_seconds: int = Field(default=8, ge=1, le=60, description="分镜时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    characters_in_scene: list[str] = Field(description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    image_prompt: ImagePrompt | PromptText | PendingPrompt = Field(default=None, description="分镜图生成提示词")
    # drama 的 video_prompt 只承载画面动作、运镜与环境音，口播由下方 utterances 承载。
    video_prompt: DramaVideoPrompt | PromptText | PendingPrompt = Field(default=None, description="视频生成提示词")
    # utterances 统一承载分镜级角色台词与画外音；条目顺序即幕内发声顺序（见 ADR 0040）。
    utterances: list[Utterance] = Field(
        default_factory=list,
        description="分镜级有序发声序列：角色台词（dialogue）与画外音（voiceover）按时序排列",
    )
    # 逐字原文摘录（追溯锚，类比旁白/解说 novel_text，但纯作追溯、不被朗读、不出音、best-effort）。
    # 由 script_plan（脚本规划）填入，prompt_authoring（视觉）透传不改；存量数据缺失时默认空串（不更坏守卫放行）。
    source_text: str = Field(default="", description="逐字原文摘录（追溯锚，不朗读、不出音，best-effort）")
    # 视觉改编描述：内容确认转换时由脚本规划透传，作为提示词编写的视觉基底；对 LLM 隐藏。
    # 存量正式脚本无此字段时为空串。
    scene_description: SkipJsonSchema[str] = Field(default="", description="视觉改编描述")
    # 见 NarrationSegment.transition_to_next 说明
    transition_to_next: SkipJsonSchema[TransitionType] = Field(default="cut", description="转场类型")
    # 见 NarrationSegment 同名字段说明。
    note: SkipJsonSchema[str | None] = Field(default=None, description="用户备注（不参与生成）")
    end_frame_image: SkipJsonSchema[str | None] = Field(default=None, description="尾帧快照路径（项目内相对路径）")
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="生成资源状态"
    )
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="该单元需要人工重新规划")
    # 待编写：视觉层尚未补出。不带视觉层的新增条目置位，提示词编写写回或手写齐视觉层时清除；
    # 对 LLM 隐藏，不在任何 PATCH 白名单内。落盘只在置位时出现。
    pending_authoring: SkipJsonSchema[bool] = Field(default=False, description="该条目待编写")
    # 见 NarrationSegment.direction 说明。
    direction: SkipJsonSchema[EntryDirection | None] = Field(default=None, description="导演说明")


class DramaEpisodeScript(BaseModel):
    """剧情演绎剧本

    注意：`episode` 字段不在 schema 中，集号由 CLI 真相源通过 `_add_metadata` 写入。
    详见 `NarrationEpisodeScript` docstring。顶层不走 ``extra="forbid"`` 同理。
    """

    title: str = Field(description="剧集标题")
    # 见 NarrationEpisodeScript.content_mode 说明
    content_mode: SkipJsonSchema[Literal["drama"]] = Field(default="drama", description="创作类型")
    # 见 NarrationEpisodeScript.novel 说明
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="小说来源信息")
    # 见 NarrationEpisodeScript 同名字段说明。
    hook: SkipJsonSchema[str | None] = Field(default=None, description="集尾钩子（来自分集账本）")
    next_episode_teaser: SkipJsonSchema[str | None] = Field(default=None, description="下集预告语（来自分集账本）")
    scenes: list[DramaScene] = Field(description="分镜列表")
    # 见 NarrationEpisodeScript.director_plan 说明。
    director_plan: SkipJsonSchema[DirectorPlan | None] = Field(default=None, description="集级导演规划")


# ============ 剧情演绎两段式：script_plan 内容 / prompt_authoring 视觉（见 ADR 0041） ============
#
# 内容抽取前移到 script_plan：分镜边界、characters/scenes/props、utterances（逐字口播）、source_text
# （逐字原文锚）、scene_description（视觉改编自由文本）一次定稿。prompt_authoring 只生成视觉层
# （image_prompt / video_prompt），LLM 输出 schema 仅含 scene_id（对齐锚）+ 视觉字段——
# 非视觉字段不进 LLM 输出，从工程上杜绝其经 Structured Outputs 漂移，由后端按 scene_id
# 写回正式脚本条目（ScriptGenerator._merge_visual_layer）。


class DramaSceneContent(BaseModel):
    """script_plan（normalize）产出的分镜内容层：除视觉层（image_prompt / video_prompt）外的全部字段。

    作为 prompt_authoring 视觉生成、以及后续 web 审阅 / 编辑的结构化中间态契约（落盘于
    ``drafts/episode_N/script_plan_normalized_script.json``，外层为 ``DramaNormalizedScript``）。
    三个文本字段职责严格区分、不可混填：

    - ``scene_description``：**视觉改编自由文本**——只承载画面可见内容（角色动作、神态、环境、光影），
      供 prompt_authoring 生成 image_prompt / video_prompt 作画面基底；**不内嵌任何口播**，允许相对原文创作改编
      （丢失 / 漂移可容忍，非保真字段）。
    - ``utterances``：**逐字口播**——分镜内"说出来的话"的有序序列（台词 dialogue 带 speaker、画外音
      voiceover 无 speaker），下游字幕 / TTS 的单一真相源，prompt_authoring 透传不改、不重识别。
    - ``source_text``：**逐字原文追溯锚**——本分镜所源自的原文片段摘录，供人工对照、失真定位、单分镜
      重生成；不被朗读、不出音，与 utterances 分属两事（utterances 是发声、source_text 是溯源）。
    """

    model_config = _STRICT_CONFIG

    scene_id: str = Field(min_length=1, description="分镜 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    duration_seconds: int = Field(default=8, ge=1, le=60, description="分镜时长（秒）")
    segment_break: bool = Field(default=False, description="是否为场景切换点")
    characters_in_scene: list[str] = Field(description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    scene_description: str = Field(
        description="分镜视觉改编描述（自由文本，仅承载视觉内容，供 prompt_authoring 生成视觉层）"
    )
    utterances: list[Utterance] = Field(
        default_factory=list,
        description="分镜级有序发声序列：角色台词（dialogue）与画外音（voiceover）按时序排列，逐字保留",
    )
    source_text: str = Field(default="", description="逐字原文摘录（追溯锚，不朗读、不出音，best-effort）")
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="该分镜需要人工重新规划")
    # 仅在提示词给出已通过剧本时由模型填写，供产出与剧本的对齐判定（lib.script.screenplay_alignment）使用；
    # exclude=True：model_dump 不含它，持久化的 script_plan 与正式脚本形状不变。
    scene_no: int | None = Field(
        default=None,
        ge=1,
        exclude=True,
        description="所属剧本场次的序号（从 1 起）；仅在提示词提供了已确认剧本时填写，否则省略",
    )


class DramaNormalizedScript(BaseModel):
    """script_plan 规范化剧本：分镜内容列表。作为 prompt_authoring 视觉生成与后续 web 审阅 / 编辑的唯一基底。

    顶层不走 ``extra="forbid"``（同 ``DramaEpisodeScript``）：避免落盘时附带的运行时字段触发拒绝。
    """

    title: str = Field(description="剧集标题")
    scenes: list[DramaSceneContent] = Field(description="分镜内容列表")


class DramaSceneVisual(BaseModel):
    """prompt_authoring（generate-script）产出的分镜视觉层：仅 scene_id（对齐锚）+ 视觉字段。

    ``scene_id`` 必须等于 script_plan 已定分镜的 scene_id，后端按它（非列表顺序）合并回内容层。
    """

    model_config = _STRICT_CONFIG

    scene_id: str = Field(min_length=1, description="对齐锚：必须等于 script_plan 已定分镜的 scene_id")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: DramaVideoPrompt = Field(description="视频生成提示词（无 dialogue，口播在 script_plan utterances）")


class DramaVisualScript(BaseModel):
    """prompt_authoring 视觉层剧本：各分镜视觉字段（按 scene_id 与 script_plan 内容对齐）。

    顶层不走 ``extra="forbid"`` 同 ``DramaNormalizedScript``。``title`` 可选，最终标题取自 script_plan 内容。
    """

    title: str = Field(default="", description="剧集标题（可选，最终以 script_plan 内容为准）")
    scenes: list[DramaSceneVisual] = Field(description="各分镜视觉层（按 scene_id 对齐 script_plan 内容）")


# ============ 广告/短片（Ad） ============


class AdShot(BaseModel):
    """广告/短片的分镜——平铺 shots[] 的最小单元。

    ``section`` 是带货框架段落标签（hook/pain_point/product_reveal/selling_point/
    demo/trust/price_promo/cta 八值引导，不硬枚举，留给 prompt 资产约束）；
    ``voiceover_text`` 是一等口播文案，字幕导出与后续 TTS 的单一来源。商品按名字
    引用 ``products_in_shot``（对应 project.json 的 products bucket），氛围分镜该列表为空。
    """

    model_config = _STRICT_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _fill_pending_prompts(cls, data: object) -> object:
        """待编写分镜可以不带提示词字段：补成 null，其余分镜仍须两侧齐备。"""
        if isinstance(data, dict) and data.get("pending_authoring") is True:
            data = {"image_prompt": None, "video_prompt": None, **data}
        return data

    shot_id: str = Field(description="分镜 ID，格式 E{集}S{序号} 或 E{集}S{序号}_{子序号}")
    section: str = Field(
        description="带货框架段落标签（如 hook/pain_point/product_reveal/selling_point/demo/trust/price_promo/cta）"
    )
    duration_seconds: int = Field(ge=1, le=60, description="分镜时长（秒）")
    voiceover_text: str = Field(description="口播文案（必须完整可照稿配音，可为空字符串）")
    characters_in_shot: list[str] = Field(default_factory=list, description="出场角色名称列表")
    scenes: list[str] = Field(default_factory=list, description="出场场景名称列表")
    props: list[str] = Field(default_factory=list, description="出场道具名称列表")
    products_in_shot: list[str] = Field(default_factory=list, description="出场商品名称列表，非空即商品分镜")
    # 整份生成的 response_schema 仍要求两侧提示词；只有待编写分镜（手动新增）可以缺省为 null，
    # 由提示词编写补出（见 _fill_pending_prompts 与结构校验）。
    image_prompt: ImagePrompt | PromptText | PendingPrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt | PromptText | PendingPrompt = Field(description="视频生成提示词")
    # 见 NarrationSegment.transition_to_next 说明
    transition_to_next: SkipJsonSchema[TransitionType] = Field(default="cut", description="转场类型")
    # 见 NarrationSegment 同名字段说明。
    note: SkipJsonSchema[str | None] = Field(default=None, description="用户备注（不参与生成）")
    end_frame_image: SkipJsonSchema[str | None] = Field(default=None, description="尾帧快照路径（项目内相对路径）")
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="生成资源状态"
    )
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="该单元需要人工重新规划")
    # 待编写：视觉层尚未补出。不带视觉层的新增条目置位，提示词编写写回或手写齐视觉层时清除；
    # 对 LLM 隐藏，不在任何 PATCH 白名单内。落盘只在置位时出现。
    pending_authoring: SkipJsonSchema[bool] = Field(default=False, description="该条目待编写")


class AdShotVisual(BaseModel):
    """提示词编写为已有广告分镜补出的视觉层：仅 shot_id（对齐锚）+ 视觉字段。

    口播、时长、段落与出场资产已在正式剧本里，按 shot_id 原样保留，不进 LLM 输出。
    """

    model_config = _STRICT_CONFIG

    shot_id: str = Field(min_length=1, description="对齐锚：必须逐字等于待编写分镜的 shot_id")
    image_prompt: ImagePrompt = Field(description="分镜图生成提示词")
    video_prompt: VideoPrompt = Field(description="视频生成提示词")


class AdVisualScript(BaseModel):
    """广告分镜提示词编写的 LLM ``response_schema``：各待编写分镜的视觉层。"""

    shots: list[AdShotVisual] = Field(description="各待编写分镜的视觉层，按 shot_id 一一对齐")


class AdEpisodeScript(BaseModel):
    """广告/短片剧本（恒单集，剧本即第 1 集脚本文件）。

    注意：`episode` 字段不在 schema 中，集号由 CLI 真相源通过 `_add_metadata` 写入。
    详见 `NarrationEpisodeScript` docstring。顶层不走 ``extra="forbid"`` 同理。
    """

    title: str = Field(description="短片标题")
    # 见 NarrationEpisodeScript.content_mode 说明
    content_mode: SkipJsonSchema[Literal["ad"]] = Field(default="ad", description="创作类型")
    # 见 NarrationEpisodeScript.novel 说明
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="小说来源信息")
    shots: list[AdShot] = Field(description="分镜列表")


# ============ 参考生视频（Reference-to-Video） ============

#: 视频单元编排时长（``ReferenceVideoUnit.duration_seconds``）的结构范围（秒）。
#: 静态模型只拦非正整数与量级明显失真的值；生成预检再按执行模型能力投影到申请档位并把
#: 偏移作为 warning 呈现。上界不由正文内的镜头描述数推导：镜头描述不承载时长，unit 才是一次生成调用。
#: 存量迁移的问题壳是唯一例外，可在 ``needs_replan`` 下保存零秒。
REFERENCE_UNIT_DURATION_RANGE: tuple[int, int] = (1, 300)

#: ad 剧本总时长 vs 项目 target_duration 的偏差观察阈值（比例）。供应商时长枚举
#: （如 [4,6,8]）的量化误差让总和难精确命中目标，阈值放宽只捕明显跑偏；超阈值
#: 仅 warn（生成端 logger、校验端 warnings 列表），不阻塞保存、不推前端。
#: ``ScriptGenerator`` 与 ``DataValidator`` 共用此真相源。
AD_TARGET_DURATION_DRIFT_THRESHOLD = 0.20


def ad_shot_duration_seconds(shot: object) -> int:
    """ad 单个分镜时长（秒）的脏数据归一口径：非 dict 条目、非正整数时长
    （bool 按 int 子类排除）一律按 0 计、不抛。

    分镜图生视频的总时长偏差观察经 ``ad_script_total_duration`` 共用此口径。
    """
    if not isinstance(shot, dict):
        return 0
    value = shot.get("duration_seconds")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def ad_script_total_duration(shots: object) -> int:
    """ad 剧本 shots 总时长（秒）。

    与 target_duration 偏差观察的求和口径单一真相源（``ScriptGenerator`` 探针与
    ``DataValidator`` 共用）：脏数据按 0 计、不抛（见 ``ad_shot_duration_seconds``）——
    求和服务于"仅 warn"的轻量观察与 metadata 统计，对降级保存的原始 dict 也要稳健。
    """
    if not isinstance(shots, list):
        return 0
    return sum(ad_shot_duration_seconds(shot) for shot in shots)


#: 缺 duration_seconds 时按骨架种类取的兜底时长（秒）——剧本条目时长的单一真相源。
#: segments/scenes 沿用历史默认；shots（ad）与 video_units（参考生视频）无单分镜默认时长
#: 偏好（按 target/预算逐条规划），缺失按 0 计，避免杜撰值污染与目标总时长的对照。
#: 四种骨架全登记；第五种骨架加入即在 ``item_duration`` 查表 KeyError。
_ITEM_FALLBACK_DURATIONS: dict[str, int] = {"segments": 4, "scenes": 8, "shots": 0, "video_units": 0}


def item_duration(kind: str, item: object) -> int:
    """单条剧本条目时长（秒）的脏数据归一口径——沿 ``ad_shot_duration_seconds`` 先例推广到四骨架。

    非 dict 条目无时长语义按 0 计；dict 内 ``duration_seconds`` 缺失，或为脏值
    （None / 布尔 / 非正整数 / 浮点 / 字符串）一律回退按 ``kind`` 查 ``_ITEM_FALLBACK_DURATIONS``
    的兜底时长。只认真正的正整数（bool 按 int 子类排除），与校验器「``duration <= 0`` 判无效」一致。
    """
    if not isinstance(item, dict):
        return 0
    value = item.get("duration_seconds")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return _ITEM_FALLBACK_DURATIONS[kind]


def script_duration_total(kind: str, items: object) -> int:
    """按骨架种类求剧本条目总时长（秒）——脏数据稳健、不抛（见 ``item_duration``）。

    ``items`` 非 list（含 null 这类降级保存的脏值）按空处理返回 0。读时计算与写盘重算
    共用此单一真相源，避免三处各自维护同一兜底表与守卫。
    """
    if not isinstance(items, list):
        return 0
    return sum(item_duration(kind, item) for item in items)


class ReferenceResource(BaseModel):
    """参考图引用——只存名称 + 类型，具体路径从 project.json 对应 bucket 读时解析。

    执行期派生物，不落盘：视频单元只持久化正文，参考图由正文的 ``@[名称]`` 在渲染与请求
    投影时解析（见 :func:`lib.script.reference_video.text_parser.derive_references_from_text`）。
    """

    model_config = _STRICT_CONFIG

    type: Literal["product", "character", "scene", "prop"] = Field(description="引用的资源类型")
    name: str = Field(description="商品/角色/场景/道具名称，必须在 project.json 对应 bucket 中已注册")


class H3Prompt(BaseModel):
    """MiniMax H3 全参考提示词的六段结构，由提示词编写或人工写入，属于 ``text`` 的派生载荷。

    各段正文里的资产以 ``@[名称]`` 占位，生成时按实际随请求发出的参考图 / 参考音频顺序解析成
    ``<Picture N>`` / ``<Audio N>`` 标签。``source_text_hash`` 记录写入时的 ``text`` 指纹，
    ``text`` 改动后指纹不再匹配，该载荷即视为过期。
    """

    model_config = _STRICT_CONFIG

    subject_definitions: str = Field(min_length=1, description="subject_definitions 段")
    summary: str = Field(min_length=1, description="summary 段")
    retention_analysis: str = Field(min_length=1, description="retention_analysis 段")
    detailed_description: str = Field(min_length=1, description="detailed_description 段")
    overall_soundscape: str = Field(min_length=1, description="overall_soundscape 段")
    non_diegetic_music: str = Field(min_length=1, description="non_diegetic_music 段")
    source_text_hash: SkipJsonSchema[str] = Field(default="", description="写入时的 text 指纹，空串表示未绑定")


DubGender = Literal["male", "female"]


class DubLine(BaseModel):
    """参考生视频 unit 的一条越南语配音台词，与 unit 正文里派生出的第 ``index`` 条发声一一对应。

    ``source_text`` 记录它所对应那条发声的原文（台词 / 画外音记号内的文本），原文被改动后与现行
    发声不再相符，据此判定该配音行过期。``start_seconds`` 为人工覆盖的相对 unit 起点的开始时间，
    缺省由语速估算或合成时长顺序排布。
    """

    model_config = _STRICT_CONFIG

    index: int = Field(ge=0, description="对应 unit 正文派生出的第几条发声（从 0 起）")
    speaker: str | None = Field(default=None, description="说话角色名；画外音为 null")
    gender: DubGender = Field(description="配音音色性别：male / female")
    source_text: str = Field(default="", description="所对应发声的原文")
    text_vi: str = Field(min_length=1, description="越南语配音台词")
    start_seconds: float | None = Field(default=None, ge=0, description="人工覆盖的开始时间（相对 unit 起点，秒）")
    # 已合成配音音频的登记：路径相对项目目录；``audio_text`` / ``audio_voice`` 记录合成时用的台词与音色，
    # 与现行 ``text_vi`` / 音色不符即音频过期。由配音合成任务写入，对 LLM 隐藏。
    audio_path: SkipJsonSchema[str | None] = Field(default=None, description="已合成配音音频的项目内相对路径")
    audio_duration_seconds: SkipJsonSchema[float | None] = Field(default=None, ge=0, description="音频真实时长（秒）")
    audio_voice: SkipJsonSchema[str | None] = Field(default=None, description="合成时使用的音色 id")
    audio_text: SkipJsonSchema[str | None] = Field(default=None, description="合成时使用的越南语台词")
    # 语音识别从成片里检测到的这条台词的开始时间（相对 unit 起点，秒）；人工覆盖 ``start_seconds`` 优先。
    detected_start_seconds: SkipJsonSchema[float | None] = Field(
        default=None, ge=0, description="从成片检测到的开始时间（秒）"
    )

    @model_serializer(mode="wrap")
    def _omit_unset_audio(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """尚未合成音频 / 尚未检测开始时间的配音行不落盘对应的 ``audio_*`` 与 ``detected_start_seconds`` 键，规划草稿与存量剧本的形状保持不变。"""
        data = handler(self)
        for key in ("audio_path", "audio_duration_seconds", "audio_voice", "audio_text", "detected_start_seconds"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class DubLineDraft(BaseModel):
    """script_plan 的 LLM 产出配音行：只含发声序号、说话人、性别与越南语译文，``source_text`` 由服务端补。"""

    model_config = _STRICT_CONFIG

    index: int = Field(ge=0, description="对应 text 中按出现顺序派生的第几条发声（台词与画外音，从 0 起）")
    speaker: str | None = Field(default=None, description="说话角色名；画外音为 null")
    gender: DubGender = Field(description="配音音色性别：male / female")
    text_vi: str = Field(min_length=1, description="该条发声的越南语配音台词，口语自然、简洁")


# ============ 参考生视频导演规划 ============
#
# 存量 script_plan 的导演说明曾随单元拆分同一次调用产出：集级 ``DirectorPlan`` + 逐 unit 的说明（类型、
# brief、beats、表演、按住项、末帧、衔接）。现在单元拆分只定内容，导演说明由内容确认之后的导演步骤写进
# 正式脚本（见下方「参考生视频导演步骤」）。这里的字段全部带缺省，存量 script_plan 与草稿照常通过校验，
# 内容要求由 ``lib.script.reference_video.draft_validation`` 的导演规划校验判定。

ReferenceUnitType = Literal[
    "establishing",
    "character_intro",
    "exposition",
    "dialogue_drama",
    "romance",
    "heartbreak",
    "family_warmth",
    "conflict",
    "humor",
    "power_move",
    "face_slap",
    "combat",
    "chase",
    "tension_build",
    "climax_impact",
    "reveal",
    "time_shift",
    "montage",
    "cliffhanger",
    "other",
]

#: 单元类型 → 含义，渲染进 script_plan 提示词，是各取值的唯一说明来源；键与 ``ReferenceUnitType`` 一致。
REFERENCE_UNIT_TYPE_MEANINGS: dict[str, str] = {
    "establishing": "交代地点、时间与氛围，故事尚未推进",
    "character_intro": "角色的第一印象：让观众认识他是谁、是什么样的人",
    "exposition": "交代观众必须知道的信息或背景",
    "dialogue_drama": "以对话为主，人物关系或筹码在对话中发生变化",
    "romance": "心动、靠近、暧昧或甜蜜的情感时刻",
    "heartbreak": "失去、拒绝、离别或悲伤",
    "family_warmth": "家人或挚友之间的温情",
    "conflict": "争执、对峙或较劲，不含肢体打斗",
    "humor": "以制造笑点为目的的喜剧段落",
    "power_move": "主角掌控局面，展示实力、身份或手腕",
    "face_slap": "反转打脸：看低主角的人被事实证明是错的",
    "combat": "肢体打斗",
    "chase": "追逐或逃离",
    "tension_build": "危险或压力不断升高，但结果被按住、留到后面的单元",
    "climax_impact": "大事件落地：事故、重击、真相揭晓的那一刻",
    "reveal": "隐藏的真相、身份或反转被揭开",
    "time_shift": "闪回、时间跳跃、重生、梦境等时间切换",
    "montage": "压缩时间的蒙太奇或重复动作串",
    "cliffhanger": "以悬念收尾，把问题留给下一集",
    "other": "以上都不合适时使用",
}

_REFERENCE_UNIT_TYPES: frozenset[str] = frozenset(get_args(ReferenceUnitType))
assert set(REFERENCE_UNIT_TYPE_MEANINGS) == _REFERENCE_UNIT_TYPES, "单元类型含义表与 ReferenceUnitType 不一致"


def _coerce_unit_type(value: object) -> object:
    """单元类型归一：大小写 / 首尾空白不敏感，不认识的字符串落 ``other``；``None`` 与非字符串原样透传。"""
    if not isinstance(value, str):
        return value
    canon = value.strip().lower()
    return canon if canon in _REFERENCE_UNIT_TYPES else "other"


class DirectorBeat(BaseModel):
    """unit 内的一个可观察动作节点。"""

    model_config = _STRICT_CONFIG

    at_pct: int = Field(ge=0, le=100, description="该动作在 unit 时长中的起点位置（百分比，0-100）")
    action: str = Field(description="镜头看得见或麦克风听得见的动作，不写内心活动")


class ReferenceUnitDirection(BaseModel):
    """unit 级导演说明的字段集，script_plan 的扁平产出单元与落盘单元共用。"""

    unit_type: Annotated[ReferenceUnitType | None, BeforeValidator(_coerce_unit_type)] = Field(
        default=None, description="单元类型"
    )
    brief: str = Field(default="", description="给人看的导演说明：发生什么、为什么、观众该有什么感受，二到四句")
    beats: list[DirectorBeat] = Field(default_factory=list, description="2-5 个可观察动作节点，at_pct 递增")
    performance: list[DirectorPerformance] = Field(default_factory=list, description="逐角色的表演说明")
    must_not_show: str = Field(default="", description="本 unit 必须按住、不能出现的内容")
    end_frame: str = Field(default="", description="本 unit 最后一帧的画面")
    continuity_in: str = Field(default="", description="开场状态：地点、时间、天气、人物位置与手中物件")
    continuity_out: str = Field(default="", description="结束状态，须与下一个 unit 的 continuity_in 衔接")


#: unit 级导演说明的字段名，落盘与草稿搬运时按它逐字段取用；与 ``ReferenceUnitDirection`` 的字段一致。
REFERENCE_UNIT_DIRECTION_FIELDS: tuple[str, ...] = tuple(ReferenceUnitDirection.model_fields)


# ============ 参考生视频导演步骤（内容确认之后、提示词编写之前） ============
#
# 导演步骤为每个视频单元定「这一段怎么演、怎么拍」：镜头切点与运镜、逐角色表演与声音、台词落在哪一镜、
# 同期声与配乐。提示词编写（H3 等）只把它整理成目标格式，不再自己发明表演与镜头。运镜词表取自 MiniMax
# H3 官方提示词写作指南（camera motion = type + amplitude + speed）。

ReferenceCameraType = Literal[
    "static_shot",
    "push_in",
    "pull_out",
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "truck_left",
    "truck_right",
    "tilt_up",
    "tilt_down",
    "pedestal_up",
    "pedestal_down",
    "arc_shot",
    "tracking_shot",
    "shake_slightly",
    "shake_strongly",
    "pov",
    "roll_clockwise",
    "roll_counterclockwise",
]


class ReferenceCameraMove(BaseModel):
    """一镜的主要运镜：类型 + 幅度 + 速度。中等幅度与正常速度是缺省，写提示词时省略。"""

    model_config = _STRICT_CONFIG

    type: ReferenceCameraType = Field(description="运镜类型，只取词表里的值")
    amplitude: Literal["small", "medium", "large"] = Field(default="medium", description="幅度")
    speed: Literal["slow", "normal", "fast"] = Field(default="normal", description="速度")


class ReferenceShot(BaseModel):
    """单元里的一个镜头：从第几秒切入、怎么取景、怎么运镜、发生什么。"""

    model_config = _STRICT_CONFIG

    at_seconds: float = Field(ge=0, description="切入时间（秒）；第一镜为 0，之后严格递增且早于单元时长")
    framing: str = Field(min_length=1, description="景别与主体在画面里的位置，如「中景，主角居左三分之一」")
    camera: ReferenceCameraMove = Field(description="这一镜的主要运镜，一镜只用一种")
    action: str = Field(min_length=1, description="这一镜里看得见的动作与状态变化，不写内心活动")
    why_cut: str = Field(default="", description="为什么切到这一镜：带来了哪个新的主体、空间、状态或视角；第一镜留空")


class ReferencePerformance(DirectorPerformance):
    """参考生视频单元里某个角色的表演：在通用表演说明之外，说话的角色写清声音。"""

    voice: str = Field(default="", description="说话角色的声音：音高、音色、语速，必要时加口音；不说话的角色留空")


class ReferenceLine(BaseModel):
    """一句角色台词落在哪一镜、怎么说。台词逐字取自单元正文的 ``@[角色]{台词}``。"""

    model_config = _STRICT_CONFIG

    speaker: str = Field(min_length=1, description="说话的角色登记名，逐字等于正文里的说话人")
    text: str = Field(min_length=1, description="台词，逐字等于正文花括号里的内容（标点也照抄）")
    shot: int = Field(ge=1, description="这句台词所在的镜头序号（从 1 起）；一句台词不跨镜")
    delivery: str = Field(default="", description="怎么说：语气、音量、停顿与同时的动作")


class ReferenceUnitDirectionOutput(BaseModel):
    """导演步骤对单个视频单元的产出：类型、说明、镜头表、表演、台词、声音与衔接。"""

    model_config = _STRICT_CONFIG

    unit_type: Annotated[ReferenceUnitType, BeforeValidator(_coerce_unit_type)] = Field(description="单元类型")
    brief: str = Field(description="给人看的导演说明：发生什么、为什么、观众该有什么感受，二到四句")
    shots: list[ReferenceShot] = Field(min_length=1, description="按时间顺序排列的镜头")
    performance: list[ReferencePerformance] = Field(description="逐角色的表演；画面里没有角色时为空数组")
    lines: list[ReferenceLine] = Field(description="正文里每一句角色台词各一条，按说出的先后；没有台词时为空数组")
    sounds: str = Field(description="同期声：与动作同步的声响和环境音，按镜头顺序写；没有时为空串")
    music: str = Field(description="只有观众听得到的配乐：乐器、速度与力度变化；不要配乐时为空串")
    must_not_show: str = Field(description="本单元必须按住、不能出现的内容；不需要时为空串")
    end_frame: str = Field(description="本单元最后一帧的画面")
    continuity_in: str = Field(description="开场状态：地点、时间、天气、人物位置与手中物件")
    continuity_out: str = Field(description="结束状态，须与下一个单元的 continuity_in 衔接")


class ReferenceDirectorUnitOutput(BaseModel):
    """导演步骤的逐单元产出：单元 id + 导演说明。"""

    model_config = _STRICT_CONFIG

    id: str = Field(min_length=1, description="单元 id（unit_id），逐字等于输入")
    direction: ReferenceUnitDirectionOutput


class ReferenceDirectorEpisodeOutput(BaseModel):
    """参考生视频导演步骤的结构化产出（文本模型的 response_schema）：集级规划 + 逐单元导演说明。"""

    model_config = _STRICT_CONFIG

    director_plan: DirectorPlan = Field(description="集级导演规划，先于逐单元说明写出")
    units: list[ReferenceDirectorUnitOutput] = Field(min_length=1, description="按剧本顺序排列的逐单元导演说明")


#: 导演步骤写进正式脚本单元的字段（扁平存放，与存量导演说明字段并列）。``beats`` 是存量字段，新导演结果不写。
REFERENCE_DIRECTION_OUTPUT_FIELDS: tuple[str, ...] = tuple(ReferenceUnitDirectionOutput.model_fields)


class ReferenceVideoUnit(BaseModel):
    """视频单元——一个视频文件的最小生成粒度。

    ``text`` 是这个单元的唯一持久化内容真相：一段自由书写的正文，参考图与发声归属都从它
    读时或执行期派生，不另存结构（见 ADR 0064）。unit 是一次生成调用的单元，一个 unit 一个
    编排时长：``duration_seconds`` 是剧本时长的唯一真相，执行前预检再把它投影到供应商申请档位。
    """

    model_config = _STRICT_CONFIG

    unit_id: str = Field(description="格式 E{集}U{序号}")
    text: str = Field(description="单元正文，可包含 @[商品]/@[角色]/@[场景]/@[道具] 引用；迁移问题壳可为空")
    duration_seconds: int = Field(
        ge=0,
        le=REFERENCE_UNIT_DURATION_RANGE[1],
        description="该单元时长（秒）",
    )
    # transition_to_next / note / generated_assets 均为 UI / runtime / 人工字段，对 LLM 隐藏。
    transition_to_next: SkipJsonSchema[TransitionType] = Field(default="cut", description="转场类型")
    note: SkipJsonSchema[str | None] = Field(default=None, description="用户备注")
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="生成资源状态"
    )
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="该单元需要人工重新规划")
    # 对应原文：内容确认时从脚本规划单元透传，供创作者对照来源。对 LLM 隐藏，不在 web PATCH
    # 白名单内；Agent 可经批量编辑改写，项目有源文时须是本集源文的逐字子串。手动新增的单元为空。
    source_text: SkipJsonSchema[str] = Field(default="", description="该单元所依据的逐字原文摘录")
    # 待编写：视觉层尚未补出。不带视觉层的新增条目置位，提示词编写写回或手写齐视觉层时清除；
    # 对 LLM 隐藏，不在任何 PATCH 白名单内。落盘只在置位时出现。
    pending_authoring: SkipJsonSchema[bool] = Field(default=False, description="该条目待编写")
    # H3 全参考提示词档下的六段载荷；其它提示词档不写入。对 LLM 的单元级 schema 隐藏（提示词编写
    # 用独立输出模型承载），落盘只在存在时出现。
    h3_prompt: SkipJsonSchema[H3Prompt | None] = Field(default=None, description="H3 全参考提示词六段")
    # 越南语配音台词：脚本规划步骤随同一次 LLM 调用产出，创作者可在对白面板逐行编辑。
    # 对 LLM 的单元级 schema 隐藏（规划输出用独立的扁平单元承载），落盘只在存在时出现。
    dub_lines: SkipJsonSchema[list[DubLine]] = Field(default_factory=list, description="越南语配音台词")
    # 导演说明：脚本规划随 unit 一并产出、内容确认时透传，创作者可在解析预览里改。字段集见
    # ``ReferenceUnitDirection``；对 LLM 隐藏（提示词编写用独立输出模型，只读不写），落盘只在有内容时出现。
    unit_type: SkipJsonSchema[ReferenceUnitType | None] = Field(default=None, description="单元类型")
    brief: SkipJsonSchema[str] = Field(default="", description="导演说明")
    beats: SkipJsonSchema[list[DirectorBeat]] = Field(default_factory=list, description="可观察动作节点")
    performance: SkipJsonSchema[list[ReferencePerformance]] = Field(default_factory=list, description="表演说明")
    must_not_show: SkipJsonSchema[str] = Field(default="", description="必须按住的内容")
    end_frame: SkipJsonSchema[str] = Field(default="", description="最后一帧画面")
    continuity_in: SkipJsonSchema[str] = Field(default="", description="开场状态")
    continuity_out: SkipJsonSchema[str] = Field(default="", description="结束状态")
    # 导演步骤的镜头表、台词落位、同期声与配乐；对 LLM 隐藏，落盘只在有内容时出现。
    shots: SkipJsonSchema[list[ReferenceShot]] = Field(default_factory=list, description="镜头表")
    lines: SkipJsonSchema[list[ReferenceLine]] = Field(default_factory=list, description="台词落位")
    sounds: SkipJsonSchema[str] = Field(default="", description="同期声")
    music: SkipJsonSchema[str] = Field(default="", description="配乐")

    @model_validator(mode="after")
    def _validate_replan_shell(self) -> "ReferenceVideoUnit":
        """全悬空迁移壳可为空且为 0 秒；其余单元仍须可执行。"""
        if not self.text.strip():
            if not self.needs_replan or self.duration_seconds != 0:
                raise ValueError("空 video unit 仅允许 needs_replan=true 且 duration_seconds=0")
        elif self.duration_seconds < REFERENCE_UNIT_DURATION_RANGE[0]:
            raise ValueError("非空 video unit 的 duration_seconds 必须为正整数")
        return self


class ReferenceVideoScript(BaseModel):
    """参考生视频剧集脚本。

    注意：`episode` 字段不在 schema 中，集号由 CLI 真相源通过 `_add_metadata` 写入。
    详见 `NarrationEpisodeScript` docstring。顶层不走 ``extra="forbid"`` 同理。

    ``content_mode`` 仅承担"内容类型"维度（narration/drama/ad）；"视频来源"维度是项目级事实
    （``project.json`` 的 ``generation_mode``），剧本不携带——生成模式创建时锁定，剧本骨架种类
    本身即生成模式的体现。
    """

    title: str = Field(description="剧集标题")
    # 对 LLM 隐藏：由 _add_metadata 注入。
    content_mode: SkipJsonSchema[Literal["narration", "drama", "ad"]] = Field(
        default="narration", description="内容类型（narration/drama/ad）"
    )
    # 见 NarrationEpisodeScript.novel 说明
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="小说来源信息")
    # 见 NarrationEpisodeScript 同名字段说明。
    hook: SkipJsonSchema[str | None] = Field(default=None, description="集尾钩子（来自分集账本）")
    next_episode_teaser: SkipJsonSchema[str | None] = Field(default=None, description="下集预告语（来自分集账本）")
    video_units: list[ReferenceVideoUnit] = Field(description="视频单元列表")
    # 集级导演规划：导演步骤写入，对 LLM 隐藏，落盘只在存在时出现。
    director_plan: SkipJsonSchema[DirectorPlan | None] = Field(default=None, description="集级导演规划")


def resolve_content_mode(script: dict[str, Any], project: dict[str, Any]) -> str:
    """剧本级 ``content_mode`` 缺失（存量 episode 未打戳）时回退到项目级配置，与
    ``lib.project.data_validator._validate_episode_payload`` 已校验通过的既定口径一致——存量
    episode 允许省略该字段、由项目值兜底，读侧（生成任务）不能另起一份更严格的判定。
    """
    return script.get("content_mode", project.get("content_mode", "narration"))


# ============ 参考生视频 script_plan 结构化中间态 ============
#
# 两段式职责切分（与 narration / drama 同机制，见 ADR 0041）：script_plan（video_unit 拆分）产出
# 内容层（unit 边界 + 正文与时长），prompt_authoring（generate-script）以此为唯一基底生成
# ReferenceVideoScript 的视觉编排（景别 / 构图 / 运镜扩写）。


class ReferenceScriptPlanUnit(ReferenceUnitDirection):
    """参考生视频 script_plan（video_unit 拆分）产出的结构化单元：内容层。

    ``text`` 是正文唯一真相，参考图不在此落盘（见 :class:`ReferenceVideoUnit`）。
    ``duration_seconds`` 的档位枚举依赖运行时视频能力值，由
    ``build_reference_units_script_plan_model`` 动态收紧；参考图数上限同样依赖运行时能力值，
    由拆分工具按正文派生结果后校验，不进本模型。
    """

    model_config = _STRICT_CONFIG

    unit_id: str = Field(min_length=1, description="格式 E{集}U{序号}")
    text: str = Field(min_length=1, description="单元正文，用 @[名称] 引用已注册资产")
    duration_seconds: int = Field(
        ge=REFERENCE_UNIT_DURATION_RANGE[0],
        le=REFERENCE_UNIT_DURATION_RANGE[1],
        description="该单元时长（秒）",
    )
    # 逐字原文锚：拆分工具校验其为源文子串后原样落盘，供内容确认时对照与失真定位。
    # 默认空串：不带该字段的存量草稿照常通过校验。
    source_text: SkipJsonSchema[str] = Field(default="", description="该 unit 所依据的逐字原文摘录（追溯锚）")
    # 越南语配音台词：项目启用配音时随规划一并落盘；不带该字段的存量草稿照常通过校验。
    dub_lines: SkipJsonSchema[list[DubLine]] = Field(default_factory=list, description="越南语配音台词")


class ReferenceScriptPlanDraft(BaseModel):
    """参考生视频 script_plan 结构化中间态（``drafts/episode_N/script_plan_reference_units.json`` 的 schema）。

    顶层容忍附加字段（与 NarrationScriptPlanDraft 同口径），读时按本模型校验。
    """

    model_config = ConfigDict(extra="ignore")

    director_plan: DirectorPlan | None = Field(default=None, description="集级导演规划；存量草稿为 null")
    units: list[ReferenceScriptPlanUnit] = Field(description="video_unit 列表")


# ---------------------------------------------------------------------------
# 参考生视频扁平草稿结构：两级 LLM 产出的形状
# ---------------------------------------------------------------------------
#
# script_plan / prompt_authoring 的 LLM 产出与人在编辑器里写的是同一种格式（见内置共享模版片段
# shared/writing_syntax），故 schema 退化为一层扁平：正文是一段文本，unit_id / 参考图 /
# utterances / 音频编号一律机器派生，不让 LLM 写。schema 只承担「枚举与
# 外层结构」这一层约束（backend 的约束解码重试也只保得住这一层），文本内的语法交
# parser 后校验（lib/script/reference_video/draft_validation.py）。


class ReferenceScriptPlanFlatUnit(ReferenceUnitDirection):
    """script_plan 的 LLM 产出单元：导演说明 + 时长 + 原文锚 + 引用语法正文。"""

    model_config = _STRICT_CONFIG

    duration_seconds: int = Field(
        ge=REFERENCE_UNIT_DURATION_RANGE[0],
        le=REFERENCE_UNIT_DURATION_RANGE[1],
        description="该单元时长（秒）",
    )
    source_text: str = Field(min_length=1, description="该单元所依据的小说原文逐字摘录（不转述、不翻译）")
    text: str = Field(min_length=1, description="该单元的引用语法正文：画面描述 + 行内的台词 / 画外音记号")
    # 仅在项目启用配音时由提示词要求产出；缺省空列表，是否必填由服务端后校验。
    dub_lines: list[DubLineDraft] = Field(
        default_factory=list,
        description="越南语配音台词：按 text 中台词与画外音记号的出现顺序，每条发声一行（未启用配音时留空）",
    )
    # 仅在提示词给出已通过剧本时由模型填写，供产出与剧本的对齐判定（lib.script.screenplay_alignment）使用；
    # exclude=True：model_dump 不含它，持久化的 script_plan 与正式脚本形状不变。
    scene_no: int | None = Field(
        default=None,
        ge=1,
        exclude=True,
        description="所属剧本场次的序号（从 1 起）；仅在提示词提供了已确认剧本时填写，否则省略",
    )


class ReferenceScriptPlanContentFlatUnit(BaseModel):
    """单元拆解（内容确认之前）的 LLM 产出单元：只有时长、原文锚、引用语法正文与配音行。

    导演说明由内容确认之后的导演步骤写，不在拆解里产出；草稿重判与存量 script_plan 收编仍用带导演字段的
    :class:`ReferenceScriptPlanFlatUnit`（存量规划带着导演说明）。
    """

    model_config = _STRICT_CONFIG

    duration_seconds: int = Field(
        ge=REFERENCE_UNIT_DURATION_RANGE[0],
        le=REFERENCE_UNIT_DURATION_RANGE[1],
        description="该单元时长（秒）",
    )
    source_text: str = Field(min_length=1, description="该单元所依据的小说原文逐字摘录（不转述、不翻译）")
    text: str = Field(min_length=1, description="该单元的引用语法正文：画面描述 + 行内的台词 / 画外音记号")
    dub_lines: list[DubLineDraft] = Field(
        default_factory=list,
        description="越南语配音台词：按 text 中台词与画外音记号的出现顺序，每条发声一行（未启用配音时留空）",
    )
    # 见 ReferenceScriptPlanFlatUnit.scene_no。
    scene_no: int | None = Field(
        default=None,
        ge=1,
        exclude=True,
        description="所属剧本场次的序号（从 1 起）；仅在提示词提供了已确认剧本时填写，否则省略",
    )


class ReferenceScriptPlanContentFlatDraft(BaseModel):
    """单元拆解的 LLM 产出顶层形状：只有 unit 列表，没有导演规划。"""

    model_config = ConfigDict(extra="ignore")

    units: list[ReferenceScriptPlanContentFlatUnit] = Field(min_length=1, description="按叙事顺序排列的 unit 列表")


class ReferenceScriptPlanFlatDraft(BaseModel):
    """script_plan 的 LLM 产出顶层形状。"""

    model_config = ConfigDict(extra="ignore")

    # 排在 units 之前：模型按字段顺序生成，先定整集规划再逐 unit 拆分。
    director_plan: DirectorPlan | None = Field(default=None, description="集级导演规划，先于 units 写出")
    units: list[ReferenceScriptPlanFlatUnit] = Field(min_length=1, description="按叙事顺序排列的 unit 列表")


class ReferenceDubLinesUnit(BaseModel):
    """配音回填的 LLM 产出单元：按 ``unit_id`` 对回已有 unit，只含配音行。"""

    model_config = _STRICT_CONFIG

    unit_id: str = Field(min_length=1, description="输入里给出的 unit_id，原样回填")
    dub_lines: list[DubLineDraft] = Field(
        description="该 unit 正文中每条台词 / 画外音各一行越南语配音，按发声序号排列；无发声的 unit 给空数组"
    )


class ReferenceDubLinesBackfill(BaseModel):
    """配音回填（TRANSLATE_DUB）的 LLM 产出顶层形状。"""

    model_config = ConfigDict(extra="ignore")

    units: list[ReferenceDubLinesUnit] = Field(description="与输入 unit 一一对应的配音行列表")


class ReferencePromptAuthoringFlatUnit(BaseModel):
    """prompt_authoring 的 LLM 产出单元：只有展开后的引用语法正文。

    时长与 unit 顺序是 script_plan 已定稿、用户已完成内容确认的内容契约，不进 prompt_authoring 输出——
    不给 LLM 写的字段就没有漂移可校验。
    """

    model_config = _STRICT_CONFIG

    text: str = Field(min_length=1, description="提示词编写后的引用语法正文：画面描述 + 行内的台词 / 画外音记号")


class ReferencePromptAuthoringFlatScript(BaseModel):
    """prompt_authoring 的 LLM 产出顶层形状：标题 + 与 script_plan 等长、同序的 unit 正文列表。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="剧集标题")
    units: list[ReferencePromptAuthoringFlatUnit] = Field(
        min_length=1, description="与 script_plan units 一一对应、顺序不变"
    )


class ReferencePromptAuthoringH3FlatUnit(BaseModel):
    """H3 Ref2VA 提示词档下 prompt_authoring 的 LLM 产出单元：引用语法正文 + 六段英文载荷。"""

    model_config = _STRICT_CONFIG

    text: str = Field(min_length=1, description="提示词编写后的引用语法正文：画面描述 + 行内的台词 / 画外音记号")
    h3_prompt: H3Prompt = Field(description="与 text 同一内容的 MiniMax H3 全参考六段英文提示词")


class ReferencePromptAuthoringH3FlatScript(BaseModel):
    """H3 Ref2VA 提示词档下 prompt_authoring 的 LLM 产出顶层形状。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="剧集标题")
    units: list[ReferencePromptAuthoringH3FlatUnit] = Field(
        min_length=1, description="与 script_plan units 一一对应、顺序不变"
    )


class AdReferenceFlatUnit(BaseModel):
    """广告/短片的参考生视频一次生成产出的自包含书写单元。"""

    model_config = _STRICT_CONFIG

    duration_seconds: int = Field(
        ge=REFERENCE_UNIT_DURATION_RANGE[0],
        le=REFERENCE_UNIT_DURATION_RANGE[1],
        description="该单元的编排时长（秒），不按供应商档位量化",
    )
    text: str = Field(min_length=1, description="引用语法正文：画面描述 + 行内的台词 / 画外音记号")


class AdReferenceFlatScript(BaseModel):
    """广告/短片的参考生视频的单阶段 LLM 输出；ID 与状态均由机器派生。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="短片标题")
    units: list[AdReferenceFlatUnit] = Field(min_length=1, description="按播放顺序排列的视频单元")


# ============ duration 枚举硬约束（按视频模型能力动态构造剧本 schema） ============


def _coerce_digit_string(value: object) -> object:
    """机械强转：纯数字字符串 → int，其余原样透传交给 ``Literal`` 校验。

    Gemini ``responseSchema`` 通道的 ``enum`` 仅支持字符串，整数时长枚举在 wire 层转为
    字符串枚举（``["4","6","8"]``，见 ``lib/backends/text_backends/gemini.py``），约束解码下模型
    输出 ``"4"``；此处恢复 int，使复验与解析两侧的 ``Literal[4,6,8]`` 照常命中。
    """
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _duration_literal(supported_durations: list[int]) -> object:
    """把 supported_durations 去重排序后构造成数字字符串可强转的 ``Literal[...]``。

    多值在 ``model_json_schema()`` 里渲染为 JSON-schema ``enum``、单值渲染为 ``const``，两者都是硬约束。
    与 ``ConfigResolver`` 同口径用 ``int(d)`` 归一（见 ``lib/config/resolver.py`` custom 分支）。空集抛 ValueError。
    """
    values = tuple(sorted({int(d) for d in supported_durations}))
    if not values:
        raise ValueError("supported_durations 为空，无法构造 duration 枚举约束")
    return Annotated[Literal[values], BeforeValidator(_coerce_digit_string)]


def _constrained_duration_item(item_base: type[BaseModel], duration_type: object, description: str) -> type[BaseModel]:
    """在 ``item_base`` 上把 ``duration_seconds`` 收紧为 ``duration_type``（三工厂共用的字段约束骨架）。"""
    return create_model(
        item_base.__name__,
        __base__=item_base,
        duration_seconds=(duration_type, Field(description=description)),
    )


def build_episode_script_model(content_mode: str, supported_durations: list[int]) -> type[BaseModel]:
    """构造 ``duration_seconds`` 被 ``supported_durations`` 枚举硬约束的剧集脚本模型。

    NarrationSegment / DramaScene 静态定义里 ``duration_seconds`` 是 ``Field(ge=1, le=60)`` 的开区间，
    LLM 在此区间内挑个非成员值（如模型支持 [4,6,8] 却写 5/7）能过 Pydantic、却会在执行层
    ``assert_duration_supported`` 处晚失败、甚至漏到供应商 API 报错。这里按当前视频模型的
    ``supported_durations`` 把该字段收紧为 ``Literal[*supported_durations]``：
    - 在 response_schema（结构化输出）里渲染为 JSON-schema ``enum``（单值时为 ``const``）→ LLM 生成层即被卡死；
    - ``model_validate`` 时强制成员校验。

    服务 narration / drama / ad 三种创作类型：骨架种类经规范解析
    （``resolve_declared_kind``，未知/缺失 content_mode fail-loud 抛 ``ValueError``，不落 drama
    兜底），kind → 模型的映射留本地（行为不进注册表）。reference_video 不经此路：其 API 消费的是
    ``unit.duration_seconds``（各 shot 之和），与单 shot 枚举不对应，沿用静态 ``ReferenceVideoScript``。
    """
    duration_type = _duration_literal(supported_durations)
    # storyboard schema 生成不涉 reference 路径，generation_mode 传 None（narration→segments、
    # drama→scenes、ad→shots）；未知 content_mode 在此抛 ValueError。
    kind = resolve_declared_kind(content_mode, None)
    if kind == "segments":
        segment = _constrained_duration_item(
            NarrationSegment, duration_type, "分镜时长（秒），必须取 supported_durations 中的值"
        )
        return create_model(
            "NarrationEpisodeScript",
            __base__=NarrationEpisodeScript,
            segments=(list[segment], Field(description="分镜列表")),
        )
    if kind == "shots":
        return _ad_episode_model(duration_type, "分镜时长（秒），必须取 supported_durations 中的值")
    scene = _constrained_duration_item(DramaScene, duration_type, "分镜时长（秒），必须取 supported_durations 中的值")
    return create_model(
        "DramaEpisodeScript",
        __base__=DramaEpisodeScript,
        scenes=(list[scene], Field(description="分镜列表")),
    )


def build_drama_normalized_script_model(supported_durations: list[int]) -> type[BaseModel]:
    """构造 script_plan 规范化剧本模型，``duration_seconds`` 被 ``supported_durations`` 枚举硬约束。

    内容抽取前移后由 script_plan 决定分镜时长，故 duration 枚举约束加在内容层 ``DramaSceneContent`` 上
    （与 ``build_episode_script_model`` 同口径，渲染为 response_schema 的 enum / const）；prompt_authoring 视觉层
    不含 duration，沿用静态 ``DramaVisualScript``。
    """
    scene = _constrained_duration_item(
        DramaSceneContent,
        _duration_literal(supported_durations),
        "分镜时长（秒），必须取 supported_durations 中的值",
    )
    return create_model(
        "DramaNormalizedScript",
        __base__=DramaNormalizedScript,
        scenes=(list[scene], Field(description="分镜内容列表")),
    )


def _ad_episode_model(duration_type: object, description: str) -> type[BaseModel]:
    """ad 剧集脚本的动态包装骨架：两条生成路径共用，仅 ``duration_seconds`` 约束类型不同。"""
    shot = _constrained_duration_item(AdShot, duration_type, description)
    return create_model(
        "AdEpisodeScript",
        __base__=AdEpisodeScript,
        shots=(list[shot], Field(description="分镜列表")),
    )


#: 发给模型的 script_plan 产出单元：导演说明各字段必填。落盘与草稿重判沿用带缺省的
#: :class:`ReferenceScriptPlanFlatUnit`（存量 script_plan 不带导演说明）；只有发给模型的 response_schema
#: 用它——带缺省的字段在 JSON schema 里不进 ``required``，模型会直接略过它们。
ReferenceScriptPlanDirectedFlatUnit: type[BaseModel] = create_model(
    "ReferenceScriptPlanFlatUnit",
    __base__=ReferenceScriptPlanFlatUnit,
    unit_type=(Annotated[ReferenceUnitType, BeforeValidator(_coerce_unit_type)], Field(description="单元类型")),
    brief=(str, Field(description="给人看的导演说明：发生什么、为什么、观众该有什么感受，二到四句")),
    beats=(list[DirectorBeat], Field(description="2-5 个可观察动作节点，at_pct 递增")),
    performance=(list[DirectorPerformance], Field(description="逐角色的表演说明；画面里没有角色时为空数组")),
    must_not_show=(str, Field(description="本 unit 必须按住、不能出现的内容；不需要时为空串")),
    end_frame=(str, Field(description="本 unit 最后一帧的画面")),
    continuity_in=(str, Field(description="开场状态：地点、时间、天气、人物位置与手中物件")),
    continuity_out=(str, Field(description="结束状态，须与下一个 unit 的 continuity_in 衔接")),
)


def build_reference_units_content_model(supported_durations: list[int]) -> type[BaseModel]:
    """单元拆解发给模型的 response_schema：unit 时长被 ``supported_durations`` 枚举硬约束，不含导演说明。"""
    unit = _constrained_duration_item(
        ReferenceScriptPlanContentFlatUnit,
        _duration_literal(supported_durations),
        "该单元时长（秒），必须取 supported_durations 中的值",
    )
    return create_model(
        "ReferenceScriptPlanContentFlatDraft",
        __base__=ReferenceScriptPlanContentFlatDraft,
        units=(list[unit], Field(min_length=1, description="按叙事顺序排列的 unit 列表")),
    )


def build_reference_units_script_plan_model(
    supported_durations: list[int], *, require_director: bool = False
) -> type[BaseModel]:
    """构造 unit 时长被 ``supported_durations`` 枚举硬约束的参考生视频 script_plan 模型（扁平形状）。

    unit 是一次生成调用的单元，拆分阶段决定的就是发给供应商的那个秒数，故枚举约束加在
    ``ReferenceScriptPlanFlatUnit.duration_seconds`` 上（response_schema 渲染为 enum / const，LLM
    生成层即被卡死），与 prompt_authoring 同口径衔接：prompt_authoring 沿用 script_plan 的 unit 时长，只做提示词编写。
    参考图数上限与文本内语法依赖运行时能力值 / 项目登记表，不进 schema，由拆分工具后校验。

    ``require_director`` 为 True 时导演规划与 unit 级导演说明全部必填：发给模型的 response_schema 用它，
    草稿重判与存量 script_plan 的收编用缺省的宽松形状。

    prompt_authoring 无对应工厂：它不产出时长，没有需要按能力收窄的枚举字段，直接用静态
    ``ReferencePromptAuthoringFlatScript``。
    """
    unit = _constrained_duration_item(
        ReferenceScriptPlanDirectedFlatUnit if require_director else ReferenceScriptPlanFlatUnit,
        _duration_literal(supported_durations),
        "该单元时长（秒），必须取 supported_durations 中的值",
    )
    if not require_director:
        return create_model(
            "ReferenceScriptPlanFlatDraft",
            __base__=ReferenceScriptPlanFlatDraft,
            units=(list[unit], Field(min_length=1, description="按叙事顺序排列的 unit 列表")),
        )
    return create_model(
        "ReferenceScriptPlanFlatDraft",
        __base__=ReferenceScriptPlanFlatDraft,
        director_plan=(DirectorPlan, Field(description="集级导演规划，先于 units 写出")),
        units=(list[unit], Field(min_length=1, description="按叙事顺序排列的 unit 列表")),
    )
