"""越南语配音工具的声明：入队自动配音，查询配音进度。

配音任务按视频单元入队，不是以产物为键的生成批次：不走 ``generation_batch`` 长任务机制，两宿主都在入队后
立即返回任务 id，由 ``get_dub_status`` 轮询。
"""

from __future__ import annotations

from typing import Any

from server.agent_toolset.declaration import BLOCKED, READ_CHECK, ToolDeclaration
from server.media_tools.dub_voiceover import (
    GenerateDubVoiceoverRequest,
    GetDubStatusRequest,
    generate_dub_voiceover,
    get_dub_status,
)


def _generate_summary(value: dict[str, Any]) -> str:
    skipped = value.get("skipped") or []
    tail = f"；跳过 {len(skipped)} 个没有成片的单元" if skipped else ""
    return (
        f"已入队 {len(value['task_ids'])} 个配音任务（单元 {', '.join(value['unit_ids'])}）{tail}。"
        "任务在后台运行，调用 get_dub_status 轮询，直到 active_tasks.running 为 false。"
    )


GENERATE_DUB_VOICEOVER = ToolDeclaration(
    name="generate_dub_voiceover",
    description=(
        "为参考生视频项目的视频单元生成越南语配音（与界面「自动配音」同一入口；本地模型合成，不产生供应商费用）。"
        "每个已有成片的单元入队一个任务：缺越南语台词先写台词，再用本地语音识别从成片检测原台词开始时间"
        "（skip_timing=true 则跳过、按估算排布），最后合成各行音频写入项目；同时最多 4 个单元在跑。"
        "没有成片的单元记入 skipped 且不入队。仅在项目启用配音（settings 的 dub_target_language 为 vi）"
        "且本地配音未被关闭时可用，否则返回 dub_not_applicable / dub_not_enabled / dub_vieneu_disabled。"
        "配音任务不是生成批次：不返回 generation_batch，入队后立即返回 task_ids / unit_ids / skipped，"
        "之后用 get_dub_status 轮询进度，不要用 get_generation_batch。"
        "该步骤可选，不阻断导出。"
    ),
    request_model=GenerateDubVoiceoverRequest,
    migration=BLOCKED,
    domain_key="dub_voiceover",
    handler=generate_dub_voiceover,
    summary=_generate_summary,
)

GET_DUB_STATUS = ToolDeclaration(
    name="get_dub_status",
    description=(
        "读取一集的越南语配音进度。逐单元给出：has_video、台词行数 lines / lines_expected、已检测到开始时间的行数 "
        "detected_timing、音频仍对应现行台词的行数 audio_current、被顺延 0.3 秒以上的行数 delayed_lines、"
        "超出单元时长的 overflow_seconds，以及单一状态 state：no_video 没有成片；no_lines 台词缺失或已过期；"
        "needs_timing 音频未齐且缺开始时间；needs_audio 只缺音频；ready 台词与音频齐备。"
        "另给 summary 汇总（complete 为所有有成片的单元都 ready）与 active_tasks（项目内仍在排队 / 运行的配音任务，"
        "running 为 true 时继续轮询）。needs_timing / needs_audio / no_lines 都由 generate_dub_voiceover 一并补齐。"
        "只读，无副作用。项目不是参考生视频或未启用配音时返回 dub_not_applicable / dub_not_enabled；"
        "项目数据升级失败时返回 project_migration_failed problem。"
    ),
    request_model=GetDubStatusRequest,
    migration=READ_CHECK,
    domain_key="dub_status",
    handler=get_dub_status,
)

DUB_VOICEOVER_TOOLS = (GENERATE_DUB_VOICEOVER, GET_DUB_STATUS)

__all__ = ["DUB_VOICEOVER_TOOLS", "GENERATE_DUB_VOICEOVER", "GET_DUB_STATUS"]
