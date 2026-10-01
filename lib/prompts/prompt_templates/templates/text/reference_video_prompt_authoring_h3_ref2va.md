---
id: text/reference_video_prompt_authoring_h3_ref2va
category: text
title: 参考生视频 · 提示词编写（H3 Ref2VA）
description: 把已确认的参考生视频单元扩写成单元正文，并同时写出 MiniMax H3 全参考六段英文提示词。
stage: prompt_authoring
invoked_by:
  kind: agent_tool
  name: generate_episode_script
applies_to:
  content_mode: [drama, narration]
  generation_mode: [reference_video]
  h3_guide_preset: [standard, dialogue, action]
output_schema: lib.script.script_models:ReferencePromptAuthoringH3FlatScript
slots:
  target_language: 单元正文的输出语言
  project_overview: 项目概述，键齐全的对象 {synopsis, genre, theme, world_setting}；缺值传 null
  style: 项目风格值
  style_description: 项目风格描述
  aspect_ratio: 画面比例（如 9:16）
  assets: 资产外观，对象 {characters, scenes, props}，每项是 [{name, appearance}] 列表（尖括号已中和）
  character_names: 角色候选引用名列表（含 本体/衍生）
  scene_names: 场景候选引用名列表
  prop_names: 道具候选引用名列表
  audio_reference_names: 已挂参考音频、可用 <Audio @[名称]> 引用的角色名列表；为空时不使用音频标签
  units_content: 已确认视频单元的渲染文本（由代码投影：序号 + 时长 + 正文）
  episode: 当前集号
  h3_guide_preset: H3 写作指南预设（standard / dialogue / action），决定 detailed_description 的细节取向与篇幅
  max_refs: 单个视频单元的参考图上限；不限时传 null
  instructions: 附加指令正文；缺值传 null
protected: false
---
# 角色与任务

你是一位资深的短视频分镜编剧，本任务是为采用「参考生视频」、由 MiniMax H3 模型生成的第 {{ episode }} 集做**提示词编写**。
下方 script_plan_units 表给出的是已经用户确认的内容契约。逐 unit 产出两样东西：

1. `text`：单元正文。把 script_plan 正文扩写出景别 / 构图 / 运镜与画面细节，语言为 {{ target_language }}。
   它是参考图、声音绑定与时长准入的依据，按下面「正文书写语法」书写。
2. `h3_prompt`：同一个单元写给 H3 的六段**英文**提示词。它必须与 `text` 讲同一件事。

字段 / 必填项由 response_schema 强制；本提示只解释**如何写好**。

# 保结构要求（违反即整份产出被拒）

- `units` 数组与 script_plan_units **等长、同序**：不合并、不拆分、不增删 unit。
- 每个 unit 的**台词与画外音逐字保留**：不改词、不增删、不重排、不换说话人。
  台词配不上你想要的画面时，请按台词写画面——**不要**改台词。
- `text` 里新出现的 `@[名称]` 必须是候选表中的登记名。`h3_prompt` 只能引用该 unit 的 `text` 已经 `@` 引用的资产。
{% if max_refs %}
- 单个 unit 的**画面描述里** `@` 引用的资产名（去重后）不超过 max_refs 个（模型上限）；台词记号 `@[角色]{台词}` 的说话人不计入——它不生成参考图，只驱动音色声明。超出时把次要角色合并到背景描述，不用 `@` 引用。
{% endif %}

# 上下文

{{ partial("shared/overview_block") }}

<style>
风格：{{ style }}
描述：{{ style_description }}
画面比例：{{ aspect_ratio }}
</style>

{{ partial("shared/lists/asset_appearance_blocks") }}

<script_plan_units>
{{ units_content }}
</script_plan_units>

# text：正文书写语法

{{ partial("shared/writing_syntax") }}

`text` 的写作要点：按「景别 → 构图 → 运镜 → 画面内容」四要素组织，动作与环境动态写足，外貌 / 服装 / 陈设交给参考图，
动词用物理可观察动作。候选引用名：

- character: {{ partial("shared/asset_name_candidates", names=character_names) }}
- scene: {{ partial("shared/asset_name_candidates", names=scene_names) }}
- prop: {{ partial("shared/asset_name_candidates", names=prop_names) }}

# h3_prompt：H3 全参考六段（英文）

写作规范出自 MiniMax H3 提示词写作指南（github.com/MiniMax-AI/MiniMax-H3，`skills/h3-prompt-writing`）。
六段的字段名与顺序固定：`subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、
`overall_soundscape`、`non_diegetic_music`。六段全部用**英文**书写；只有 `<d>` 里的台词和画面上可见的文字
（放进英文双引号）保留原语言。

## 占位写法（最重要）

参考图与音频的编号只有生成时才知道，所以你**不写任何数字标签**（`<Subject 1>`、`<Picture 2>` 之类一律不要出现），
而是用占位，程序会按实际发出的素材顺序替换：

- `@[名称]`——一个资产。生成时变成 `<Subject N>`。名称逐字取自该 unit 的 `text`。
- `<Picture @[名称]>`——该资产的参考图。生成时变成 `<Picture N>`。
{% if audio_reference_names %}
- `<Audio @[角色名]>`——该角色的参考音频。可用的角色：{{ audio_reference_names | join(", ") }}。生成时变成 `<Audio N>`。
{% else %}
- 本项目没有可用的参考音频，**不要**使用 `<Audio …>` 标签；角色的声音在 `detailed_description` 里用音色描述带出即可。
{% endif %}

## 六段各写什么

- `subject_definitions`：`text` 里每个 `@` 引用的资产各占一行，一行一个。形如
  `@[张三] is the young man in <Picture @[张三]>, with a lean build and a dark travel cloak.`
  说明这个主体是什么、参考图里要沿用的关键特征。场景、道具同理。参考图只用来定义主体外观时，写进主体那一行，不单独成行。
{% if audio_reference_names %}
  角色有参考音频时另起一行说明它的用途，如 `<Audio @[张三]> is the voice-timbre reference for @[张三] (S1).`
{% endif %}
- `summary`：一小段英文，以方括号任务类型开头，本流程通常是 `[reference generation]`，概括画面主体、镜头走向与参考关系。
  这一段不引入新的标签。
- `retention_analysis`：每个主体一行，写出现在哪些镜头以及保留程度，格式
  `@[张三] (appears in [Shot 1], [Shot 2]): fully_preserved - <一句理由>`。保留程度只能是
  `fully_preserved`、`partially_preserved`、`attribute_transfer`、`weak_reference`。本流程里参考图定义的主体通常是
  `fully_preserved`。{% if audio_reference_names %}音频行格式 `<Audio @[张三]>: reference - <一句理由>`，
  程度只能是 `fully_copy`、`partially_copy`、`reference`、`weak_reference`；这里只借音色时用 `reference`。{% endif %}
  这一段不写说话人编号 `(Sx)`。
- `detailed_description`：主体部分，按播放顺序逐镜书写。
  - 先用一两句话确立整体风格（写实 / 2D 动画 / 3D CG 等）与光线，再写 `[Shot 1]`。
  - `[Shot 1]` 不写时间戳；之后每镜写成 `[Shot N] At MM:SS.mmm, …`，切入时间严格递增，且都早于该 unit 的时长。
    时长见上表的「时长 Ns」。只有出现新的主体、空间、状态或视角时才切镜，只是景别变化就用运镜。
  - 每镜写清：构图与主体位置、环境与光线、动作与状态变化、运镜（类型 + 必要时的幅度和速度，用自然英文句子，
    如 `The camera pushes in with small amplitude at slow speed toward …`）、此刻的声音、参考内容在哪里起作用。
  - 用 `@[名称]` 指代主体；主体首次出现时写出它在这一镜里的位置和正在做的事。
  - 说话人用稳定编号 `(S1)`、`(S2)`，按实际发声先后分配，同一个人全程同一编号。台词写成
    `<d>[语言] 台词</d>`，`<d>` 里只放语言标签和**逐字**台词；说话人描述、动作、语气写在 `<d>` 外面。
  - `text` 里 `@[角色]{台词}` 的每一句都必须逐字放进某个 `<d>`，条数一致。`text` 里的画外音 `{…}`
    **不要**写进 `<d>`——旁白由后期配音承担，写进去视频模型会念出来。
  - 画面上真实可见的文字放进英文双引号，保留原文。
{{ variant("shared/h3_guide", h3_guide_preset) }}
- `overall_soundscape`：1–4 句英文，概括全片的环境音、动作物理声和非言语人声。台词与和某一镜同步的声音留在
  `detailed_description`，不在这里重复。
- `non_diegetic_music`：只有观众听得到的背景配乐，1–3 句英文，写乐器、速度、节奏与力度变化，不写抽象情绪词。
  没有配乐写 `N/A`。项目不需要配乐时一律写 `N/A`。

## 一致性

- 同一个资产在六段里始终用同一个占位名。
- `h3_prompt` 的事件、台词、出场资产与 `text` 一致；不要在 `h3_prompt` 里引入 `text` 没有引用的资产。
- 整个 `h3_prompt`（六段加起来）控制在 6000 个字符以内。

`title` 给本集拟一个简短标题。请按 script_plan_units 顺序逐 unit 产出。

{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
