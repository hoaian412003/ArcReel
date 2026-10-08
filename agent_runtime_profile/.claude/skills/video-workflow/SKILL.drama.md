---
name: video-workflow
description: 将小说转换为短视频的端到端工作流编排器。当用户提到做视频、创建项目、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我把小说做成视频"、"开个新项目"、"继续"、"下一步"、"看看项目进度"、"从头开始"、"拆集"、"自动跑完流程"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色资产图——那些有专门的 skill）。
---
<!-- mode: drama -->

# 视频工作流编排

你（主 Agent）是编排中枢。你**不直接**处理小说原文或生成剧本，而是：
1. 检测项目状态 → 2. 读计划的 `next_action` → 3. dispatch 合适的子智能体 → 4. 展示结果 → 5. 自动进入下一动作 → 6. 循环

**核心约束**：
- 小说原文**永远不加载到主 Agent context**，由子智能体自行读取
- 每次 dispatch 只传**文件路径和关键参数**，不传大块内容
- 每个子智能体完成一个聚焦目标就返回，主 Agent 负责动作间衔接

> 两种生成模式（分镜图生视频 storyboard，含 grid_storyboard 宫格开关 / 参考生视频 reference_video）的数据结构与 schema 差异详见 `.claude/references/generation-modes.md`；步骤适用性由计划表达，参考文档不重复。

---

## `collect_project_input`：项目设置

**重要**：项目目录的创建由 Web 端 `POST /api/v1/projects` 触发 `ProjectManager.create_project()` 完成（包括所有子目录与 `project.json`、按 content_mode 物化对应的 Agent profile）。**主 Agent 不创建目录、不写入 project.json 初始字段**——session 启动时 cwd 已绑定到已存在的项目根。

### 新项目

1. 提示用户在 Web 端先创建项目，**创建时指定 content_mode（narration / drama）与 generation_mode（storyboard / reference_video）**；两者创建后均不可变更，Agent 无对应写入权限。session 启动后 cwd 已绑定到对应项目根
2. 使用 Read 工具读取 `project.json`，确认 `title`、`content_mode`、`generation_mode` 字段（本 session 当前 content_mode 为 `drama`，创建后不可变更）
3. 请用户将小说文本放入 `source/`
4. **上传后自动生成项目概述**（synopsis、genre、theme、world_setting）

> 标准项目子目录由 `create_project()` 自动建好：`source/`、`scripts/`、`drafts/`、`characters/`、`scenes/`、`props/`、`storyboards/`、`grids/`、`videos/`、`reference_videos/`、`thumbnails/`、`output/`。

### 现有项目

1. session cwd 已经绑定到目标项目根
2. 调用 `mcp__arcreel__get_workflow_plan({})` 取得服务端权威计划
3. 按返回的 `next_action` 从上次未完成的动作继续

---

## 计划查询

进入工作流、用户说“继续/下一步/查看进度”、以及每次工具或子智能体完成后，都调用
`mcp__arcreel__get_workflow_plan({})` 取回权威计划（用户指定集数时传 `{"episode": N}`），
再按 `next_action.type` 路由到下面同名的小节。

计划的字段含义、完整受控动作表、旁白交付、整批准入判定、四条状态轴与 stale / 历史纪律，见
[.claude/references/workflow-plan.md](../../references/workflow-plan.md)。**本 skill 不重复一张按创作类型
或生成模式展开的步骤表**：哪些步骤适用、当前停在哪一步，一律读 `plan.steps[]` 与 `plan.next_action`，
它们是阶段判断的唯一真相源。`plan.status` 内嵌完整状态快照（`project` / `target` / `state` /
`blockers` / `gates` / `artifacts`），不需要再单独查一次状态。

调用后把 `plan.status.target.episode` 作为目标集，把 `next_action.args` 与 `requested_ids` 原样带入
对应动作。Read / Glob 只用于执行已选定动作所需的内容，不用于另建状态机；不得根据空资产 bucket、
文件名、旧文件存在性或对话记忆覆盖服务端结论。

下文各节以 `next_action.type` 为标题。`export` 表示工作流完成，`none` 表示展示 `blockers` 并停止变更。

---

## 动作间衔接

**每个子智能体返回后**，主 Agent 展示一段简短摘要，随即刷新计划并按新的 `next_action` 继续，**不使用 AskUserQuestion 停下来等用户确认**。仅在计划给出需要用户输入的动作（如 `collect_project_input`、`review_screenplay`、`confirm_script_plan`、`review_direction`、`choose_narration_delivery`、`confirm_request_duration`、`configure_provider`）或遇到 `blockers` 时才停。

计划刷新后若进入 `review_screenplay`、`confirm_script_plan` 或 `review_direction`，必须先调用 `review-assistant` skill。该 skill 只调用一次 `create_review_summary`，使用 Review Model 生成准确的越南语摘要并交给用户阅读；不得另建子智能体、不得替用户作决定。同一内容的摘要由工具按 fingerprint 复用。

---

## 分集源文与资产分析：由用户在界面上传、服务端分析

剧情演绎（drama）项目**不做分集规划**：每一集的原文由用户在 Web 的分集列表里逐集上传（`source/episode_N.txt`），
上传即登记账本条目，服务端随即入队资产分析任务（`text_asset_analysis`），按集增量提取角色 / 场景 / 道具并写入
`project.json`。**不要**调 `plan_episodes`（工具会拒绝 drama 项目），**不要**自行切分、合并、改名或重排源文，
**不要**为资产分析 dispatch 子智能体。

- `next_action.type == "collect_project_input"` 且原因是尚未上传分集源文：告诉用户去分集列表上传该集原文，
  上传后刷新计划；用户在对话里直接给出某一集的正文时，也请他在界面上传——Agent 不代为落盘。
- `next_action.type == "analyze_assets"`：资产分析由服务端完成，通常在上传后自动入队。简短告知用户正在分析、稍后刷新计划；
  若长时间不变，提示用户在项目概览的「制作流程」点「分析资产」重试（执行器离线、上次失败或源文有变动后都用它），不要自行读源文写资产。
- 空 bucket 是合法分析结果，不得凭空 bucket 重跑。已登记的资产不会被分析覆盖；要修订描述走 `patch_project`（意图驱动）。

---

## `write_screenplay` / `review_screenplay`：编剧与剧本审阅

**适用**：旁白 / 剧情项目的新一集（还没有脚本规划）。存量集（已有脚本规划或正式脚本）不走这两步，保持原路径；广告不走。

**触发**：

- `next_action.type == "write_screenplay"` → dispatch `write-screenplay` 子智能体：传入项目名称、集数；用户给本次编剧的附加要求原文透传。**编剧由子智能体亲自完成**（读源文与项目事实、做改编取舍、写成节拍、经 `submit_screenplay` 提交并按质量检查修改），主 Agent 不替它写，也没有「服务端编剧」工具可调。子智能体返回后刷新计划，通常转入 `review_screenplay`。
- `next_action.type == "review_screenplay"` → 剧本待用户审阅（需要用户决定，对话中进行；Web 项目页的 Flow 标签页是等价的审阅路径，用户在那里决定后刷新计划即可，不要重复询问）：
  1. 调用 `mcp__arcreel__get_screenplay({"episode": N})`，把 `markdown`（逐场的标题、剧情、预计时长与正文，末尾为本集预计总时长）原样展示给用户，不改写、不补充你自己的评价。
  2. 用 AskUserQuestion 提供：**通过**（推荐）/ **要求修改**（用户说明意见，可点名场次）/ **整集重写**。
  3. 用户表态后才操作：
     - 通过：`mcp__arcreel__approve_screenplay({"episode": N, "fingerprint": <最近一次 get_screenplay 的 fingerprint>})`；
     - 要求修改：用 `mcp__arcreel__patch_screenplay` 的 `update` / `insert` / `delete`（`base_fingerprint` 取最近一次 `get_screenplay`）按场次改，改完重新展示并再次询问——任何修改都会使审阅失效，须重新通过；
     - 整集重写：dispatch `write-screenplay`，用户的修改要求原文作附加指令（会整份替换并清空审阅）。
     即使用户事先说过「不用逐步确认」，剧本审阅也不跳过，不得替用户通过。
  4. 通过后刷新计划，继续 `prepare_script_plan`。

剧本是新一集的内容定稿：用户在这里审阅的就是后面所有单元的依据，所以这一步是整条流程里唯一的内容审阅点。

---

## `prepare_script_plan`：单元拆解（单集脚本规划）

**触发**：`next_action.type == "prepare_script_plan"`

新一集在剧本通过审阅后才会路由到这里：拆解以**已通过的剧本与本集源文**为依据——剧本决定怎么分（场次顺序、节奏、时长预算），源文决定原话是什么，台词与画外音（剧情 / 参考生视频）逐字取自剧本，不得改写。拆解成功后服务端自动完成内容确认（把脚本规划整集转为正式脚本），计划不会再停在 `confirm_script_plan`；该集已有正式脚本时自动确认需要用户同意覆盖，此时不自动确认，按下文「整集重做」的覆盖确认流程处理。存量集（没有剧本）沿用原路径，仍须内容确认。

dispatch `next_action.args.preprocessor` 指名的子智能体，产出 `drafts/episode_{N}/` 下对应的 script_plan
中间文件。**不要自己按 `generation_mode` × `content_mode` 反推该选谁**：服务端在同一张规则表上得出
`preprocessor`，profile 侧再推一遍只会造出第二个真相源。各 script_plan 文件与 schema 的对应关系见
`.claude/references/generation-modes.md`。

dispatch prompt 通用参数：项目名称、项目路径、集数、本集小说文件路径；可选附加指令（用户对本次生成的要求等任何需带给子智能体的临时上下文，原文透传）。

若 `next_action.args` 含 `expected_stale_script_plan_revision`，子智能体成功产出正式 script_plan 后必须调用
`mcp__arcreel__complete_script_plan_rebuild({"episode": N, "expected_stale_script_plan_revision": next_action.args.expected_stale_script_plan_revision})`。
该完成事实不可用“文件内容是否变化”推断：确定性重建可能产出完全相同的 JSON。工具报冲突时刷新计划，
不得用旧参数重试。

（两个脚本规划子智能体会自行读 project.json + 调用
`mcp__arcreel__get_video_capabilities({})`
拿到模型能力与用户偏好；主 Agent 不需要预先注入角色/场景/道具列表或
`supported_durations` / `max_duration` / `max_reference_images` / `default_duration` / `episode_target_duration` 等数据。）

**内容确认后的内容修改在正式脚本上做**：内容确认把脚本规划整集转为正式脚本 `scripts/episode_{N}.json`，此后正式脚本是该集内容的唯一真相源，脚本规划只读（Web 端保存，以及 Agent 取回、修改、晋升编辑副本，都返回 `script_plan_confirmed`）。

- **修改内容**：用户要改分镜台词 `utterances` 与对应原文 `source_text`，或参考生视频单元正文 `text` 与对应原文 `source_text` 时，先 `mcp__arcreel__get_episode_script` 取正文与 revision，再用一次 `mcp__arcreel__patch_episode_script` 的 `update` 写回，不改脚本规划、不重跑编写。写入的非空对应原文须是本集源文 `source/episode_{N}.txt` 的逐字子串（空白归一后比对，可截取首尾、中间不得删改；本集源文缺失时比对项目源文），项目有源文时服务端校验，不符以 `source_text_not_verbatim` 拒绝；清空对应原文不校验。参考生视频单元的导演说明（`unit_type` / `brief` / `beats` / `performance` / `must_not_show` / `end_frame` / `continuity_in` / `continuity_out`）同样用 `patch_episode_script` 的 `update` 逐字段改，界面「导演指南」页签走同一条通道；改导演说明不会自动改画面，要让它进入画面须对该单元重新生成 H3 提示词（`generate_episode_script` 带 `entry_ids`）。分镜改了台词后若要让提示词跟上，由用户决定是否用 `generate_episode_script` 带 `entry_ids` 显式重写这几条；参考生视频的编写会改写单元正文，对刚改过正文的单元传 `entry_ids` 会覆盖这次修改，须先向用户说明
- **整集重做**：只有用户要推倒整集时，才重新 dispatch 脚本规划子智能体（操作类型写「整集重做」）重跑脚本规划。新的脚本规划写出后本集内容确认回到待确认（计划的 `gates.script_plan_review.state` 为 `pending`），但旧正式脚本仍在用，计划不会停在 `confirm_script_plan`，`next_action` 照常指向旧正式脚本的下游动作；整集重做时不要跟随它，也不要调 `generate_episode_script`——它会去编写即将被覆盖的旧正式脚本，按下述流程直接调 `confirm_script_review`。该集已有正式脚本时确认就是覆盖：`confirm_script_review` 返回 `script_overwrite_required`，`params.script_overwrite` 列出将被移除的条目与产物。须先向用户说明——旧条目全部移除、其分镜图与视频不再显示、手改的提示词一并丢弃——取得明确同意后，才以 `overwrite_revision=params.script_overwrite.revision` 重新确认；覆盖后全部条目待编写
- **提示词编写**：`generate_episode_script` 默认只补待编写条目，已有视觉层的条目（包括用户手写的）原样保留；只在用户明确要求重写某几条时传 `entry_ids`

---

## `confirm_script_plan` / `generate_script`：JSON 剧本生成

**触发**：

- `next_action.type == "confirm_script_plan"` → 先完成下述内容确认，刷新计划后再路由
- `next_action.type == "generate_script"` → dispatch 剧本生成

**script_plan→prompt_authoring 内容确认（需要用户审阅，在对话中进行）**：`prepare_script_plan` 产出的脚本规划须经**用户审阅并明确同意**才放行提示词编写（三种结构化 script_plan 变体——drama / narration / reference_video——一律适用；`reference_video` 的 `script_plan_reference_units.json` 同样须确认，不要跳过。ad 无 script_plan，不要求内容确认）。Web 端不再提供脚本规划标签页，审阅一律在对话中完成：
1. 子智能体返回后，读取脚本规划文件，用精简形式展示给用户：先给本集一句话摘要，再按顺序列出每个分镜（drama：序号 + `scene_description` + 台词 / 画外音；narration：序号 + `novel_text`；reference_video：序号 + 单元正文），不展示 `source_text`、时长等技术字段，除非用户追问。
2. 用 AskUserQuestion 提供：**确认并继续**（推荐）/ **修改**（用户给出修改意见，dispatch 同一子智能体按情况 B 走「取回草稿 → 改草稿 → 晋升」，改完重新展示）/ **整集重做**。
3. 只有用户选择确认后，才调用 `mcp__arcreel__confirm_script_review({"episode": N})`。即使用户事先说过「不用逐步确认」，脚本规划的审阅也不跳过。
该集尚无正式脚本时，未确认的计划停在 `confirm_script_plan`、不会路由到提示词编写（已有正式脚本时不挡下游，见上方「整集重做」）；尚无正式脚本时 `generate_episode_script` 直接报「尚无正式脚本」。确认即把脚本规划整集转为正式脚本、全部条目待编写；该集已有正式脚本时确认会覆盖它，须按上方「整集重做」先取得用户同意。

**dispatch `create-episode-script` 子智能体**：传入项目名称、项目路径、集数；可选附加指令（用户对本次生成的要求等任何需带给子智能体的临时上下文，原文透传）。

---

## `direct_episode` / `review_direction`：导演与导演审阅

**适用**：drama / narration 项目（分镜图生视频与参考生视频两条路线），且该集仍有待编写条目。广告不走这两步；条目已全部编写的存量集，计划不会路由到这里。内容确认把脚本规划转为正式脚本后，计划先停在导演步骤，通过审阅才进入 `author_prompts`。

**触发**：

- `next_action.type == "direct_episode"` → dispatch `direct-episode` 子智能体：传入项目名称、集数；`next_action.args.entry_ids`（审阅者点名要改的分镜）与 `args.note`（修改意见）有值时一并带上；用户给本次导演的附加要求原文透传。子智能体返回后刷新计划，通常转入 `review_direction`。
- `next_action.type == "review_direction"` → 导演结果待用户审阅（需要用户决定，对话中进行；Web 项目页的 Flow 标签页是等价的审阅路径，用户在那里决定后刷新计划即可，不要重复询问）：
  1. dispatch `report-director` 子智能体（只读，只调 `mcp__arcreel__get_direction_report`）：传入项目名称、集数，由它把导演报告整理给用户——整集意图 / 基调 / 情绪弧线、逐分镜的镜头与意图（如「低角度：托出对方的气势」）、表演、衔接、角度分布与机械提示。意图文字取自导演当时的产出，不自行编造。
  2. 把报告展示给用户，用 AskUserQuestion 提供：**通过**（推荐）/ **要求修改**（用户说明意见，可点名分镜）/ **整集重做**。
  3. 用户表态后才调用 `mcp__arcreel__decide_direction_review`：通过 `{"episode": N, "decision": "approve"}`；要求修改 `{"episode": N, "decision": "changes", "note": "<用户原话>", "entry_ids": [点名的分镜，可省略]}`；整集重做 `{"episode": N, "decision": "redo"}`。即使用户事先说过「不用逐步确认」，导演审阅也不跳过，不得替用户通过。
  4. 修改类决定只记录为「要求修改」，不会自动重新导演：刷新计划后 `next_action` 回到 `direct_episode`（`args` 带 `note` / `entry_ids`），按上面第一条 dispatch，再回到审阅。通过后刷新计划，继续 `author_prompts`。

导演结果写在正式脚本里（集级 `director_plan` 与逐分镜 `direction`），不要用 `Edit` 或手写补丁改它；要改走「要求修改」。提示词编写会读取每个分镜的 `direction`，画面构图、动作与运镜按导演已定的镜头与表演来写。

---

## `generate_asset_sheets`：资产设计（character / scene / prop 三类并行）

**触发**：`next_action.type == "generate_asset_sheets"`。空资产 bucket 是 `analyze_assets` 的合法完成结果，
不得据此回退；对每个资产类型，取 `artifacts.asset_sheets[type].missing_ids` 与 `requested_ids` 的交集作为
该类型的 `names`，同时传给子智能体和工具：
- character 缺 character_sheet
- scene 缺 scene_sheet
- prop 缺 prop_sheet

**调度规则（显式条件判断，按类型独立决定）**：

```text
对于 type ∈ {character, scene, prop}:
  names = artifacts.asset_sheets[type].missing_ids ∩ requested_ids
  若 names 非空 → dispatch 对应的 `generate-assets` 子智能体，并把 names 原样传给子智能体和工具
  若 names 为空 → 跳过，不 dispatch；不得回退到整类 missing_ids

三类判断彼此独立，结果可能 dispatch 0~3 个子智能体。
所有 dispatch 的子智能体返回后，合并摘要展示给用户，随即刷新计划继续。
```

下面三个 dispatch 块是模板，只实例化满足上述条件的那几个：

### 子智能体 — 角色设计

**触发**：该类 `names` 交集非空

```text
dispatch `generate-assets` 子智能体：
  任务类型：character
  项目名称：{project_name}
  待生成项：{names 交集}
  工具调用：
    mcp__arcreel__generate_assets({"type": "character", "names": [该类型 names]})
  验证方式：重新读取 project.json，检查对应角色的 character_sheet 字段
```

### 子智能体 — 场景设计

**触发**：该类 `names` 交集非空

```text
dispatch `generate-assets` 子智能体：
  任务类型：scene
  项目名称：{project_name}
  待生成项：{names 交集}
  工具调用：
    mcp__arcreel__generate_assets({"type": "scene", "names": [该类型 names]})
  验证方式：重新读取 project.json，检查对应场景的 scene_sheet 字段
```

### 子智能体 — 道具设计

**触发**：该类 `names` 交集非空

```text
dispatch `generate-assets` 子智能体：
  任务类型：prop
  项目名称：{project_name}
  待生成项：{names 交集}
  工具调用：
    mcp__arcreel__generate_assets({"type": "prop", "names": [该类型 names]})
  验证方式：重新读取 project.json，检查对应道具的 prop_sheet 字段
```

---

## `generate_storyboards` / `generate_grid`：分镜图生成

**触发**：`next_action.type` 为 `"generate_storyboards"` 或 `"generate_grid"`；服务端不会在
参考生视频返回这两个动作。

按动作直接选择工具，不二次检查 `generation_mode` 或 `grid_storyboard`：

- `next_action.type == "generate_storyboards"` → dispatch `generate-assets`，调
  `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": requested_ids})`
- `next_action.type == "generate_grid"` → dispatch `generate-assets`，调
  `mcp__arcreel__generate_grid({"script": target.script_filename})`——**不传 `scene_ids`**：
  缺失即生成选中的正是 `requested_ids` 所在的分组，同时跳过联合图已就绪而未切分的宫格、沿用在途宫格；
  点名 `scene_ids` 会把它们当作重做请求再付一次费

`generate_storyboards` 把 `next_action.args` 与 `requested_ids` 原样传给子智能体；`generate_grid` 只传剧本文件名。

> **宫格是两段式**：`generate_grid` 只产出联合图，分镜图要经切分落格才会写入，所以在切分之前计划会继续
> 给出 `generate_grid`。结果里的 `grid_ids_awaiting_split`（或摘要里「联合图已就绪、未切分」的宫格）
> 列出未切分的宫格：联合图生成完成后**直接**调 `mcp__arcreel__split_grids({"grid_ids": [...]})` 切分，不等待用户审阅；切分会覆写宫格
> 覆盖的全部分镜图，旧图留在版本历史里可回滚。

> **切换 `grid_storyboard` 后的重做**：本动作的常规触发条件是「缺分镜图」，而用户在设置页切换该开关不会让已有分镜图失效，剧本里也不记录分镜图由哪种装配方式产出——单看缺图会把整集判成已完成。用户在已有分镜图的项目上切换开关后要求按新方式出图时，与其确认要重做的分镜范围，再显式带 ID 重生：切到宫格用 `mcp__arcreel__generate_grid({"script": target.script_filename, "scene_ids": [...]})`，切回单图用 `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": [...]})`（`script` 必填；ID 列表省略时只补缺图，达不到重做效果）。已生成的视频同样不会自动失效，重出分镜图后需按新图重跑 `generate_videos` 对应分镜。

## `generate_videos`：视频生成

**触发**：`next_action.type == "generate_videos"`

入队前计划可能先交回两个受控动作，按 [workflow-plan](../../references/workflow-plan.md) 处理完再重查计划：

- `choose_narration_delivery` — 本次请求含叙述旁白。向用户**显式说明**这次要发起的是叙述旁白视频
  请求，并在「使用当前 TTS」与「后期配音」之间二选一；选择经 `narration_delivery` 带进下一次
  `mcp__arcreel__get_workflow_plan`，不持久化，之后每次查询都要重新带上。未配置 TTS 时默认后期配音，
  不要为了让视频继续而建议用户去配置 TTS 供应商；选 TTS 时先显式生成并让用户试听，再按
  预检返回的 `problems[].action` 处理（action 是权威，不要按 `code` 自己推）
- `confirm_request_duration` — 整批准入判定要求确认申请档位。按 `admission.confirmation.tiers[]` 逐档位
  展示涉及的视频单元与费用，取得确认后经 `confirmed_request_durations` 连同仍成立的 `narration_delivery` 一起带回

只有 `plan.steps[].admission.decision == "admitted"` 才入队；`blocked` 或 `confirmation_required` 时
**一个任务都不入队**。此时逐视频单元报告 `admission.units[]` 的 `unit_id`、`problems[].code`、原因与
`problems[].action`；被别人挡住的视频单元带 `generation_batch_admission_withheld`，如实说明是被
`blocked_unit_ids` 连累而非自身有问题。修掉被拒视频单元后**整批重来**，不拆批先跑通过的那一半，
否则会重复提交已经付过费的视频单元。

**dispatch `generate-assets` 子智能体**：请求选择语义与 Web 完全一致。计划里的 `requested_ids` 总是数组：
非空表示**点名强制重做（必然计费）**；`[]` 表示计划未点名，应在工具调用中**省略 ID 参数**以只补缺。
工具入参显式空数组非法，绝不能把计划的 `[]` 原样传给工具。按这两种计划值二选一，不要两个工具都试：

```text
dispatch `generate-assets` 子智能体：
  任务类型：video
  项目名称：{project_name}
  工具调用（两个工具的 narration_delivery 均为必填，填本次已向用户确认的那个值）：
    requested_ids 非空 →
      mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "selected", "ids": requested_ids},
                                             "force": true, "narration_delivery": chosen_narration_delivery})
    requested_ids == []（计划未点名；工具调用不传 scene_ids）→
      mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "episode", "episode": target.episode},
                                            "narration_delivery": chosen_narration_delivery})
  验证方式：重新读取 target.script，检查各分镜的 video_clip 字段
```

`narration_delivery` 省略或写错值一律返回工具错误、不入队任何任务，也不退回后期配音。凑够必填项
不等于做过选择：没和用户确认过就先走 `choose_narration_delivery`，不要自己填一个值。

返回后按逐 ID 分账陈述结果（`succeeded` / `failed` / `blocked` / `skipped`），并把 workflow 步骤状态、
队列任务、供应商 checkpoint、产物时效四轴**分开说**——「任务成功」不等于「当前产物有效」。
stale 产物照常可预览、可导出、可参与成片，是否重做由用户明确决定；不自动删除、覆盖或重生已付费产物。

---

## 可选：越南语配音（`voice_over`）

仅参考生视频项目、且 `project.json` 启用了 `dub_target_language: "vi"` 时才有。计划在 `video` 与 `export` 之间多出可选步骤 `voice_over`（`required: false`），**永不阻断导出**，详见 workflow-plan 参考「可选步骤：越南语配音」。

- 视频生成完成后，向用户提出并执行：`mcp__arcreel__generate_dub_voiceover({"script": target.script_filename})`（可选 `unit_ids`、`skip_timing`）。它与界面「自动配音」同一入口，入队后立即返回 `task_ids`，**不是生成批次**，不要用 `get_generation_batch`。
- 之后按间隔调 `mcp__arcreel__get_dub_status({"script": target.script_filename})`，直到 `active_tasks.running` 为 `false`；逐单元读 `state`（`no_video` / `no_lines` / `needs_timing` / `needs_audio` / `ready`），`summary.complete` 为 `true` 即配音齐备。`overflow_seconds` > 0（配音超出单元时长）或 `delayed_lines` > 0 要如实告诉用户，由用户决定是否改台词。
- 用户要开启配音：`mcp__arcreel__patch_project({"settings": {"dub_target_language": "vi"}})`，可同时设 `narrator_gender`、`dub_voice_male`、`dub_voice_female`。工具返回 `dub_not_enabled` / `dub_not_applicable` / `dub_vieneu_disabled` 时按提示处理，不要自行开启。

---

## `repair_video_units` / `patch_episode_script`：改剧本再重做

**触发**：`next_action.type` 为 `"repair_video_units"` 或 `"patch_episode_script"`。

Read `target.script`，**只处理 `requested_ids` 对应的条目**。revision 按动作取：
`patch_episode_script` 的 `next_action.args` 已直接给出 `base_revision` 与逐条 `problems`，
直接用，不必再查；`repair_video_units` 的 args 里没有，先调
`mcp__arcreel__get_episode_script({"script": target.script_filename})` 读取正文并取 revision。
再用**一次** `mcp__arcreel__patch_episode_script({"script": target.script_filename,
"base_revision": <上面取到的 revision>, "operations": [...]})` 把全部条目改完——每条一个有序 `update`。
`needs_replan` 之类的标记由工具重算，不要手写。工具报 revision 冲突时刷新计划重来，不得用旧
revision 重试。改完后按上面的请求选择语义点名重做这些 ID，再刷新计划。

---

## `wait_for_task`：有任务在跑

**触发**：`next_action.type == "wait_for_task"`。

已有任务在队列或供应商侧执行中。**不入队任何新任务**，把 `steps[].tasks[]` 的 `task_id`、`status`
与 `provider_checkpoint` 如实说给用户，等待后重新调 `mcp__arcreel__get_workflow_plan` 复查。
`provider_checkpoint.submitted == true` 表示供应商侧已提交、很可能已计费，此时重新提交等于重复付费。

---

## 灵活入口

工作流**不强制从头开始**。根据计划结果，自动从正确的动作开始：

- "分析小说角色" → 只执行 `analyze_assets`
- "创建第2集剧本" → 先确认第 2 集源文已由用户上传，再从 `write_screenplay` / 脚本规划开始（如果角色已有）
- "继续" → 计划给出第一个未完成动作
- 指定具体动作（如"生成分镜图"）→ 该动作只是用户意图，仍先查计划：与 `next_action.type` 一致才执行；
  不一致或有 blockers 时不入队，改为说明计划当前要求的动作与原因

---

## 数据分层

- 角色 / 场景 / 道具完整定义**只存 project.json**，剧本中仅引用名称
- 项目摘要 `episodes[]` 的派生字段（item_count、status、progress）**读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
