"""起始帧规划工具的声明：读简报、提交格子清单。

规划由子智能体完成，服务端只读取、校验与落盘，不调文本模型（``docs/adr/0096``）。
"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, Exempt, ToolDeclaration
from server.tool_runtime import (
    KeyframeBriefRequest,
    SubmitKeyframePlanRequest,
    get_keyframe_brief,
    submit_keyframe_plan,
)

GET_KEYFRAME_BRIEF = ToolDeclaration(
    name="get_keyframe_brief",
    description=(
        "读取起始帧规划简报：本集每个视频单元的正文与导演说明、已登记的角色（含 本体/衍生 形态）与场景、"
        "每张联合图的格子上限，以及已有看板的状态。plan_fingerprint 是这份内容的摘要，提交规划时带回它。"
        "只用于采用 H3 全参考提示词档的参考生视频项目。只读，无副作用。"
    ),
    request_model=KeyframeBriefRequest,
    migration=Exempt("只读：读取正式脚本与看板文件，不写任何文件；迁移失败时仍须可读，供诊断。"),
    domain_key="keyframe_brief",
    handler=get_keyframe_brief,
)

SUBMIT_KEYFRAME_PLAN = ToolDeclaration(
    name="submit_keyframe_plan",
    description=(
        "提交本集的起始帧格子清单。每一格是「某站位下某起始景别」的一帧画面，供一个或多个视频单元作首帧参考："
        "同一站位下起始景别相同的单元共用一格，站位变化（起身、换位）另起一格；景别须与使用它的单元第一镜一致。"
        "服务端逐条校验（场景与角色须已登记、一个单元至多一格、出镜角色须在使用该格的单元正文里被 @ 引用），"
        "任何违约整份拒绝并返回逐条问题；通过后整份替换看板，内容未变的格子沿用已有图与审核状态，"
        "并按场景把格子装进每张至多 4 格的联合图。这一步不生成图片。"
    ),
    request_model=SubmitKeyframePlanRequest,
    migration=BLOCKED,
    domain_key="keyframe_plan",
    handler=submit_keyframe_plan,
)

KEYFRAME_PLANNING_TOOLS = (GET_KEYFRAME_BRIEF, SUBMIT_KEYFRAME_PLAN)

__all__ = ["GET_KEYFRAME_BRIEF", "KEYFRAME_PLANNING_TOOLS", "SUBMIT_KEYFRAME_PLAN"]
