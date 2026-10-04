---
status: accepted
---

# 导演步骤先于提示词编写：Agent 单元产出、确定性报告、人工单元把关

提示词编写原先直接从正式脚本的内容写画面与动作，镜头语言（角度、景别、运动）与角色表演没有独立的决定点：创作者看不到「为什么这样拍」，也没有机会在画面生成前纠正。我们在内容确认与提示词编写之间新增导演步骤，并把它做成通用模式「Agent 单元 → 报告 → 人工单元」的第一个实例。

## Decisions

- **范围**：只覆盖旁白 / 剧情的分镜图生视频路线。参考生视频的导演规划随 script_plan 产出（`ReferenceUnitDirection`），移出需要迁移，不在本次；广告没有这一步。
- **数据落在正式脚本里，不另设文件**：集级 `director_plan`（复用 `DirectorPlan`）与逐条目 `direction`（`EntryDirection`：`camera{angle, shot_size, movement, intent}`、`performance[]`、`continuity_in / out`）都是可选字段，对 LLM 的视觉层 schema 隐藏。可选字段不改变既有数据形状，项目 schema 版本不变（ADR 0022）。
- **镜头字段是自由文本**：不设角度 / 景别 / 运动词表。导演按画面自行措辞，机械校验只要求非空，并要求意图不空泛（不短于下限、不是占位文本）；词表会把审美灌给模型，也与既有「不重复 schema 枚举」的提示词原则相悖。
- **导演产出整体校验、违约整体拒绝**（`lib/script/director_validation.py`）：集级规划完整（弧线至少两拍且引用有效序号）、条目与本次范围逐一对应、镜头四字段非空且意图不空泛、出场角色逐一有表演说明、衔接字段非空。违约不落盘，回报逐条问题，由 Agent 重新调用；不设草稿与晋升通道。衔接是否真能接上是语义判断，只在报告里以提示呈现。
- **写回走 `ProjectManager.save_script`**，带调用前取得的内容基线：导演期间正式脚本被改动即按冲突拒绝，不覆盖。导演只合并 `direction` 与 `director_plan`，不触碰 `pending_authoring`。`entry_ids` 只修订点名条目，要求该集已有完整导演结果。
- **工作流两步**：新增状态 `DIRECTOR_PLAN`（动作 `direct_episode`）与 `DIRECTOR_REVIEW`（动作 `review_direction`），位于 `SCRIPT_PLAN_REVIEW` 与 `FINAL_SCRIPT` 之间；两步分属两个检查点，因为一个检查点只能对应一个步骤。步骤只对旁白 / 剧情分镜路线适用。
- **存量不被阻塞（grandfather）**：只有该集仍有待编写条目时才进入导演步骤；条目已全部编写的集视为不适用，计划照旧。判据读时从正式脚本推出，不新增迁移。
- **人工单元的决定以指纹记录**：`episodes[i].direction_review = {fingerprint, decision, decided_at, note, entry_ids}`，指纹是导演数据（规划 + 各条目 `direction`）的规范化摘要，状态读时派生：指纹一致才算 `approved` / `changes_requested`，导演数据一变即回到 `pending_review`。要求修改与整集重做只记录决定，不在服务端调用 Agent；计划随后把 `next_action` 交回 `direct_episode`（带说明与点名条目）。通过只在已有导演数据且处于待审阅 / 要求修改时接受。
- **报告是确定性的只读事实**（`lib/script/director_report.py`）：同一份脚本永远得到同一份报告；意图取自导演写下的内容。连续同角度（达到阈值）、衔接断档、角色缺表演、镜头缺意图是机械提示，不阻断。
- **两条等价的人工通路**：对话里由 `report-director` 子智能体（只读）呈现报告、用户表态后经 `decide_direction_review` 记录；Web 的 Flow 看板走 REST（`GET /projects/{p}/flow-board`、`GET /projects/{p}/episodes/{n}/direction-review`、`POST …/direction-review/decision`）。两者共用 `DirectionReviewService`，决定语义一致。脚本规划的内容确认仍须用户审阅，不因导演步骤而自动确认。
- **Flow 看板是对每集工作流状态的纯映射**（`lib/workflow/flow_board.py`）：列按制作顺序交替 Agent 与人工单元，卡片列取自状态名，阻断只标 `blocked`，项目被阻断时看板照常可读。各集共用一次项目级事实读取。
- **提示词编写只读消费**：带 `direction` 的条目在编写输入里多一组「导演：」行（镜头与意图、表演、衔接），模版要求画面构图、动作与运镜按它来写。提示词编写的依据仍不含脚本规划（ADR 0080），导演数据改动不使已编写的提示词自动过期——这也是已编写的集不再进入导演步骤的原因。

## Consequences

- 内容确认整集覆盖正式脚本时导演结果一并丢弃，需要重新导演；覆盖确认已有认可环节，不另加提示。
- 导演审阅的待审阅状态不阻塞 Web 端对正式脚本的手改，但手改导演数据会使指纹漂移、决定失效，回到待审阅。
- 该模式可推广到资产、分镜、视频等步骤；那时再抽出通用的「决定记录」，本次不预先抽象。
