"""文本生成、内容确认与草稿工具的声明。

两个文本生成工具是长任务：ArcReel Agent 等到文本任务终态，拿到 ``text_generation``；外部 Agent 立即拿到
``generation_batch`` 句柄并轮询。``dry_run`` 两宿主都直接返回 prompt。
"""

from __future__ import annotations

from typing import Any

from lib.generation.generation_batch import GenerationBatchReadModel
from lib.script.draft_quarantine import OPEN_DRAFT_TOOL_NAME, PROMOTE_TOOL_NAME
from server.agent_toolset.declaration import BLOCKED, Exempt, ToolDeclaration
from server.agent_toolset.envelope import json_value
from server.tool_runtime import (
    ApproveScreenplayRequest,
    ConfirmScriptReviewRequest,
    DecideDirectionReviewRequest,
    DirectionReportRequest,
    DiscardDraftRequest,
    DraftLocator,
    GenerateDirectorPlanRequest,
    GenerateEpisodeScriptRequest,
    GenerateScriptPlanRequest,
    GetDirectorBriefRequest,
    GetPromptAuthoringBriefRequest,
    GetScriptPlanBriefRequest,
    PatchDraftRequest,
    PatchScreenplayRequest,
    PromoteDraftRequest,
    ScreenplayRequest,
    SubmitDirectorPlanRequest,
    SubmitH3PromptsRequest,
    SubmitScreenplayRequest,
    SubmitScriptPlanRequest,
    approve_screenplay,
    confirm_script_review,
    decide_direction_review,
    discard_draft,
    generate_director_plan,
    generate_episode_script,
    generate_script_plan,
    get_direction_report,
    get_director_brief,
    get_prompt_authoring_brief,
    get_screenplay,
    get_script_plan_brief,
    open_draft,
    patch_draft,
    patch_screenplay,
    promote_draft,
    submit_director_plan,
    submit_h3_prompts,
    submit_screenplay,
    submit_script_plan,
)

_TEXT_GENERATION = "text_generation"
_GENERATION_BATCH = "generation_batch"
_DRAFT = "draft"


def _text_generation_structured(value: Any) -> dict[str, Any]:
    """批次句柄放在 ``generation_batch`` 下，生成结果与 dry_run 的 prompt 放在 ``text_generation`` 下。"""
    if isinstance(value, GenerationBatchReadModel):
        return {_GENERATION_BATCH: json_value(value)}
    return {_TEXT_GENERATION: json_value(value)}


GENERATE_EPISODE_SCRIPT = ToolDeclaration(
    name="generate_episode_script",
    description=(
        "提示词编写（由服务端文本模型生成；H3 Ref2VA 提示词档的参考生视频项目不走这里，由子智能体经 "
        "get_prompt_authoring_brief / submit_h3_prompts 撰写并提交）：为正式脚本中待编写的分镜 / 视频单元补出视觉层，输入是正式脚本自身的内容，不读脚本规划。"
        "默认只编写全部待编写条目；entry_ids 显式重写指定条目的视觉层。内容字段、备注、尾帧与已生成产物原样保留。"
        "ad 项目尚无正式脚本时整份生成。dry_run=true 时直接返回 prompt，不提交生成任务。"
    ),
    request_model=GenerateEpisodeScriptRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=generate_episode_script,
    long_task=True,
    projection=_text_generation_structured,
)

GET_PROMPT_AUTHORING_BRIEF = ToolDeclaration(
    name="get_prompt_authoring_brief",
    description=(
        "H3 Ref2VA 提示词编写的写作简报（只读，不调用模型）：待编写的视频单元（正文、时长、已通过审阅的导演说明）、登记资产外观、"
        "风格、参考图上限、可用的参考音频，以及 MiniMax H3 六段英文提示词的写作指南与占位规则。写提示词之前先读它。"
        "entry_ids 点名时只取这些单元。仅 H3 Ref2VA 提示词档的参考生视频项目可用。"
    ),
    request_model=GetPromptAuthoringBriefRequest,
    migration=Exempt("只读：渲染写作简报，不写任何文件；迁移失败时仍须可读，供诊断。"),
    domain_key=_TEXT_GENERATION,
    handler=get_prompt_authoring_brief,
    projection=_text_generation_structured,
)

SUBMIT_H3_PROMPTS = ToolDeclaration(
    name="submit_h3_prompts",
    description=(
        "提示词编写提交（H3 Ref2VA）：把你自己写好的单元 H3 六段英文提示词（subject_definitions / summary / retention_analysis / "
        "detailed_description / overall_soundscape / non_diegetic_music）写进正式脚本，可分批。单元正文原样保留，不用提交。"
        "服务端不调模型，对着现有正文做机械校验：占位与引用、台词逐字进 <d>、画外音不进 <d>、分镜时间戳、长度上限等。"
        "有违约时正式脚本不改，产出连同逐条报告落到 prompt_authoring 草稿，按报告走 open_draft → patch_draft → promote_draft 修复。"
    ),
    request_model=SubmitH3PromptsRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=submit_h3_prompts,
    projection=_text_generation_structured,
)

GENERATE_SCRIPT_PLAN = ToolDeclaration(
    name="generate_script_plan",
    description=(
        "按项目创作类型由服务端文本模型生成结构化 script_plan：剧情分镜或旁白分镜。"
        "参考生视频项目不走这里（由子智能体经 get_script_plan_brief / submit_script_plan 撰写并提交）；"
        "广告/短片项目无 script_plan。dry_run=true 时直接返回 prompt，不提交生成任务。"
    ),
    request_model=GenerateScriptPlanRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=generate_script_plan,
    long_task=True,
    projection=_text_generation_structured,
)

GET_SCRIPT_PLAN_BRIEF = ToolDeclaration(
    name="get_script_plan_brief",
    description=(
        "参考生视频单元拆解的写作简报（只读，不调用模型）：本集源文、登记的角色 / 场景 / 道具名、已通过剧本、"
        "按引用状态生效的时长档位、单元参考图上限、口播语速、引用语法规范与场景引用规则。"
        "写 units 之前先读它；提交时校验的就是这里写明的约束。仅参考生视频项目可用。"
    ),
    request_model=GetScriptPlanBriefRequest,
    migration=Exempt("只读：渲染写作简报，不写任何文件；迁移失败时仍须可读，供诊断。"),
    domain_key=_TEXT_GENERATION,
    handler=get_script_plan_brief,
    projection=_text_generation_structured,
)

SUBMIT_SCRIPT_PLAN = ToolDeclaration(
    name="submit_script_plan",
    description=(
        "单元拆解提交（参考生视频）：把你自己写好的视频单元表交给服务端。每个单元是一次视频生成调用，含 duration_seconds、"
        "source_text（本集源文逐字片段）、text（引用语法正文：@[资产名]、@[角色]{台词}、{画外音}）；"
        "项目启用配音时另带 dub_lines；有已通过剧本时每个单元须带 scene_no（所属剧本场次，从 1 起）。unit_id 由服务端编号。"
        "服务端不调模型，只机械校验：时长落在该单元引用状态对应的生效档位、source_text 是源文逐字子串、正文语法与资产引用、"
        "参考图上限、台词念得完、整集覆盖源文、与已通过剧本对齐。有违约时正式文件不写，产出连同逐条违约报告落到草稿，"
        "按报告走 open_draft → patch_draft → promote_draft 修复，不要重新整份提交。通过且有已通过剧本时自动完成内容确认。"
    ),
    request_model=SubmitScriptPlanRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=submit_script_plan,
    projection=_text_generation_structured,
)

CONFIRM_SCRIPT_REVIEW = ToolDeclaration(
    name="confirm_script_review",
    description=(
        "确认本集 script_plan：整份转为正式脚本（全部分镜待编写），放行 prompt_authoring 视觉生成。"
        "仅在用户已明确认可进入视觉生成时调用。该集已有正式脚本时确认会整份覆盖它，未认可时返回"
        " script_overwrite_required 与将被移除的分镜清单（params.script_overwrite）；须向用户说明并取得同意后，"
        "再以清单中的 revision 作为 overwrite_revision 重新确认。"
    ),
    request_model=ConfirmScriptReviewRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=confirm_script_review,
)

SUBMIT_SCREENPLAY = ToolDeclaration(
    name="submit_screenplay",
    description=(
        "编剧提交：把你自己写好的本集剧本交给服务端落盘。剧本由「场」组成，每场是按发生顺序排列的有类型节拍："
        "action（动作 / 环境，须给 seconds）、dialogue（一句对白，须给 speaker，可给 tone）、voice_over（一句画外音）。"
        "服务端把节拍确定性地渲染成存盘格式，并由台词字数按口播语速加动作秒数算出每场预计时长、总时长，时长不取自你。"
        "整份替换已有剧本且清空审阅记录。检查分两档：error（如无任何台词、非中文项目里出现中文字符、单句台词超硬上限）"
        "拒绝落盘并返回原因；warning（台词偏长、说话人不在登记角色、总时长偏离目标、开场冷场、集尾落在画外音、"
        "某场无动作、某场对白过多）照常落盘并回报，按质量要求修改后可重新提交整份剧本。"
        "旁白 / 剧情项目的分镜与参考生视频两条路线共用；广告没有剧本规划，不走这一步。"
    ),
    request_model=SubmitScreenplayRequest,
    migration=BLOCKED,
    domain_key="screenplay_submission",
    handler=submit_screenplay,
)

GET_SCREENPLAY = ToolDeclaration(
    name="get_screenplay",
    description=(
        "读取本集剧本与审阅状态：标题、看点、逐场内容与预计时长、总时长、可读 Markdown，以及 status（none / pending_review / approved）"
        "与内容 fingerprint。修改或通过剧本时须带回这里读到的 fingerprint。只读，无副作用。"
    ),
    request_model=ScreenplayRequest,
    migration=Exempt("只读：读取剧本文件，不写任何文件；迁移失败时仍须可读，供诊断。"),
    domain_key="screenplay_review",
    handler=get_screenplay,
)

PATCH_SCREENPLAY = ToolDeclaration(
    name="patch_screenplay",
    description=(
        "按场次修改剧本：update 改某场的部分字段、insert 插入整场、delete 删除某场、set_meta 改标题 / 看点。"
        "须带 base_fingerprint（取自 get_screenplay）；内容已变则拒绝。任一条操作非法则整批不落盘。"
        "成功修改会使已有的审阅通过失效，须重新取得用户通过。只在用户明确要求修改时调用。"
    ),
    request_model=PatchScreenplayRequest,
    migration=BLOCKED,
    domain_key="screenplay_review",
    handler=patch_screenplay,
)

APPROVE_SCREENPLAY = ToolDeclaration(
    name="approve_screenplay",
    description=(
        "记录用户对本集剧本的审阅通过，放行单元拆解。fingerprint 须是用户审阅过的那一版（取自 get_screenplay），"
        "内容已变则拒绝。仅在用户明确表态通过后调用，不得替用户通过。"
    ),
    request_model=ApproveScreenplayRequest,
    migration=BLOCKED,
    domain_key="screenplay_review",
    handler=approve_screenplay,
)

GET_DIRECTOR_BRIEF = ToolDeclaration(
    name="get_director_brief",
    description=(
        "参考生视频导演的写作简报（只读，不调用模型）：全集视频单元（正文、台词、时长）、登记的角色 / 场景 / 道具外观、"
        "已有导演说明、审阅者的修改意见，以及集级导演规划与单元导演说明的写作要求。写导演说明之前先读它。"
        "entry_ids 点名时只修订这些单元。仅参考生视频项目可用。"
    ),
    request_model=GetDirectorBriefRequest,
    migration=Exempt("只读：渲染写作简报，不写任何文件；迁移失败时仍须可读，供诊断。"),
    domain_key=_TEXT_GENERATION,
    handler=get_director_brief,
    projection=_text_generation_structured,
)

SUBMIT_DIRECTOR_PLAN = ToolDeclaration(
    name="submit_director_plan",
    description=(
        "导演提交（参考生视频）：把你自己写好的导演结果写回正式脚本。director_plan 是集级规划（本集意图、情绪弧线、基调、意象），"
        "整份替换；units 是若干单元的导演说明，每个单元一段自由文本 direction（镜头、表演与声音、台词落位、同期声与配乐、末帧与衔接），"
        "可分批提交，未提交的单元保持原样。服务端不调模型，只做机械检查：单元 id 存在、说明不空泛、集级规划形状；"
        "角色台词没出现在说明里时返回提示。全部单元与集级规划齐了，这一集才进入人工审阅。"
    ),
    request_model=SubmitDirectorPlanRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=submit_director_plan,
    projection=_text_generation_structured,
)

GENERATE_DIRECTOR_PLAN = ToolDeclaration(
    name="generate_director_plan",
    description=(
        "导演步骤（分镜图生视频路线，由服务端文本模型生成）：为本集正式脚本写集级导演规划与逐分镜的镜头角度 / 景别 / "
        "运动与意图、逐角色表演、前后衔接，写回正式脚本，不动待编写标记。参考生视频项目不走这里（由子智能体经 "
        "get_director_brief / submit_director_plan 撰写并提交）。"
        "只适用于旁白 / 剧情项目，且该集仍有待编写条目；广告不走这一步。entry_ids 只修订指定条目（须已有完整导演结果），"
        "省略时整集导演；"
        "要求修改时审阅者的意见取自该集的导演审阅记录，附加意见走 instructions。导演产出须过机械校验，违约整体拒绝并回报。"
        "dry_run=true 时直接返回 prompt，不提交生成任务。"
    ),
    request_model=GenerateDirectorPlanRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=generate_director_plan,
    long_task=True,
    projection=_text_generation_structured,
)

GET_DIRECTION_REPORT = ToolDeclaration(
    name="get_direction_report",
    description=(
        "读取本集的导演审阅状态与导演报告：整集意图 / 基调 / 情绪弧线；分镜路线逐分镜的镜头（角度、景别、运动、意图）、"
        "表演、衔接与角度分布，参考生视频路线逐单元的镜头表、表演与声音、台词落位、同期声 / 配乐、衔接与运镜分布"
        "（报告的 route 字段区分两种形状）；以及机械提示（连续同角度、衔接断档、出场角色缺表演、镜头缺意图、说话角色缺声音）。"
        "报告由脚本里的导演数据确定性派生，意图文字取自导演当时写下的内容。只读，无副作用。"
    ),
    request_model=DirectionReportRequest,
    migration=Exempt("只读：由正式脚本派生报告，不写任何文件；迁移失败时仍须可读，供诊断。"),
    domain_key="direction_review",
    handler=get_direction_report,
)

DECIDE_DIRECTION_REVIEW = ToolDeclaration(
    name="decide_direction_review",
    description=(
        "记录用户对本集导演结果的决定：approve 通过（放行提示词编写）；changes 要求修改（须附 note 或 entry_ids）；"
        "redo 整集重做。修改类决定只记录为 changes_requested，不在此调用导演，随后按工作流下一步 direct_episode 重新导演。"
        "仅在用户明确表态后调用，不得替用户通过。"
    ),
    request_model=DecideDirectionReviewRequest,
    migration=BLOCKED,
    domain_key="direction_review",
    handler=decide_direction_review,
)

OPEN_DRAFT = ToolDeclaration(
    name=OPEN_DRAFT_TOOL_NAME,
    description="读取指定草稿；草稿不存在时从对应正式文档创建编辑副本。返回完整正文、违约与 canonical revision。",
    request_model=DraftLocator,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=open_draft,
)

PATCH_DRAFT = ToolDeclaration(
    name="patch_draft",
    description=(
        "按 canonical revision 原子替换草稿正文；revision 冲突时拒绝且不写入。"
        "给出 accept_formal_revision 即接受正式文档的并发修改，给出 source 即更新重判范围，二者显式传 null 同样生效。"
    ),
    request_model=PatchDraftRequest,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=patch_draft,
)

PROMOTE_DRAFT = ToolDeclaration(
    name=PROMOTE_TOOL_NAME,
    description="重新全量校验指定草稿；通过则晋升为正式文件并清除草稿，不通过则刷新违约报告。可反复调用。",
    request_model=PromoteDraftRequest,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=promote_draft,
)

DISCARD_DRAFT = ToolDeclaration(
    name="discard_draft",
    description="按 canonical revision 丢弃指定草稿；正式文档保持不变。",
    request_model=DiscardDraftRequest,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=discard_draft,
)

SCRIPT_AUTHORING_TOOLS = (
    SUBMIT_SCREENPLAY,
    GET_SCREENPLAY,
    PATCH_SCREENPLAY,
    APPROVE_SCREENPLAY,
    GENERATE_EPISODE_SCRIPT,
    GENERATE_SCRIPT_PLAN,
    GET_SCRIPT_PLAN_BRIEF,
    SUBMIT_SCRIPT_PLAN,
    CONFIRM_SCRIPT_REVIEW,
    GENERATE_DIRECTOR_PLAN,
    GET_DIRECTOR_BRIEF,
    SUBMIT_DIRECTOR_PLAN,
    GET_PROMPT_AUTHORING_BRIEF,
    SUBMIT_H3_PROMPTS,
    GET_DIRECTION_REPORT,
    DECIDE_DIRECTION_REVIEW,
    OPEN_DRAFT,
    PATCH_DRAFT,
    PROMOTE_DRAFT,
    DISCARD_DRAFT,
)

__all__ = [
    "APPROVE_SCREENPLAY",
    "CONFIRM_SCRIPT_REVIEW",
    "DECIDE_DIRECTION_REVIEW",
    "DISCARD_DRAFT",
    "GENERATE_DIRECTOR_PLAN",
    "GENERATE_EPISODE_SCRIPT",
    "GENERATE_SCRIPT_PLAN",
    "GET_DIRECTION_REPORT",
    "GET_DIRECTOR_BRIEF",
    "GET_PROMPT_AUTHORING_BRIEF",
    "GET_SCREENPLAY",
    "GET_SCRIPT_PLAN_BRIEF",
    "OPEN_DRAFT",
    "PATCH_DRAFT",
    "PATCH_SCREENPLAY",
    "PROMOTE_DRAFT",
    "SCRIPT_AUTHORING_TOOLS",
    "SUBMIT_DIRECTOR_PLAN",
    "SUBMIT_H3_PROMPTS",
    "SUBMIT_SCREENPLAY",
    "SUBMIT_SCRIPT_PLAN",
]
