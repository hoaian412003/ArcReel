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
    PatchDraftRequest,
    PatchScreenplayRequest,
    PromoteDraftRequest,
    ScreenplayRequest,
    WriteScreenplayRequest,
    approve_screenplay,
    confirm_script_review,
    decide_direction_review,
    discard_draft,
    generate_director_plan,
    generate_episode_script,
    generate_script_plan,
    get_direction_report,
    get_screenplay,
    open_draft,
    patch_draft,
    patch_screenplay,
    promote_draft,
    write_screenplay,
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
        "提示词编写：为正式脚本中待编写的分镜 / 视频单元补出视觉层，输入是正式脚本自身的内容，不读脚本规划。"
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

GENERATE_SCRIPT_PLAN = ToolDeclaration(
    name="generate_script_plan",
    description=(
        "按项目创作类型生成结构化 script_plan：剧情分镜、旁白分镜或参考生视频单元。"
        "广告/短片项目无 script_plan。dry_run=true 时直接返回 prompt，不提交生成任务。"
    ),
    request_model=GenerateScriptPlanRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=generate_script_plan,
    long_task=True,
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

WRITE_SCREENPLAY = ToolDeclaration(
    name="write_screenplay",
    description=(
        "编剧步骤：把本集源文改编为按场次组织、面向审阅者的可读剧本（标题、看点、逐场标题 / 剧情 / 预计时长 / 正文），"
        "整份写入 drafts/episode_N/screenplay.json，已有剧本被替换且审阅记录清空。旁白 / 剧情项目的分镜与参考生视频两条路线共用；"
        "广告没有剧本规划，不走这一步。总时长由各场预计时长相加得出。dry_run=true 时直接返回 prompt，不提交生成任务。"
    ),
    request_model=WriteScreenplayRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=write_screenplay,
    long_task=True,
    projection=_text_generation_structured,
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

GENERATE_DIRECTOR_PLAN = ToolDeclaration(
    name="generate_director_plan",
    description=(
        "导演步骤：为本集正式脚本写集级导演规划与逐条目导演说明，写回正式脚本，不动待编写标记。"
        "分镜图生视频路线写逐分镜的镜头角度 / 景别 / 运动与意图、逐角色表演、前后衔接；参考生视频路线写逐单元的"
        "镜头表（切入秒数、取景、运镜、动作）、逐角色表演与声音、台词落在哪一镜、同期声与配乐、末帧与衔接。"
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
    WRITE_SCREENPLAY,
    GET_SCREENPLAY,
    PATCH_SCREENPLAY,
    APPROVE_SCREENPLAY,
    GENERATE_EPISODE_SCRIPT,
    GENERATE_SCRIPT_PLAN,
    CONFIRM_SCRIPT_REVIEW,
    GENERATE_DIRECTOR_PLAN,
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
    "GET_SCREENPLAY",
    "OPEN_DRAFT",
    "PATCH_DRAFT",
    "PATCH_SCREENPLAY",
    "PROMOTE_DRAFT",
    "SCRIPT_AUTHORING_TOOLS",
    "WRITE_SCREENPLAY",
]
